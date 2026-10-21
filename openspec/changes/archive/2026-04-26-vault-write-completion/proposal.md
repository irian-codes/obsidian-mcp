## Why

The MCP server's write surface is incomplete: agents can `create_note` and
`edit_note`, but cannot move/rename, delete, or safely modify frontmatter.
`edit_note` itself is brittle — full-replace and single-match find/replace are
the only structured options, with no preflight diff and no crash-safe write.
This forces agents into fragile YAML find/replace for tag updates, blocks
"rename and update links" workflows, and leaves notes truncatable if the
process dies mid-write. Rounding out the CRUD surface unblocks the next tier
of vault-organization workflows and removes a class of footguns.

## What Changes

- All write paths (`create_note`, `edit_note`, and the three new tools) write
  via tmp file + `os.replace()`, eliminating torn-write risk.
- `edit_note` gains three new modes/parameters — all additive, no breaking
  changes to existing call shapes:
  - `dry_run: bool = False` — return a unified diff without writing.
  - `replace_all: bool = False` — allow `find` to match more than once.
  - `section: str | None = None` — replace the body under a named ATX
    heading (up to the next heading of equal/shallower depth).
- New tool `move_note(from_path, to_path, rewrite_links=False)` — rename
  and/or move a note. Updates `notes_metadata.file_path` and `note_links`
  rows whose resolved target was the old path. When `rewrite_links=True`,
  also rewrites incoming `[[wikilinks]]` and `![[embeds]]` in source notes.
- New tool `delete_note(path, permanent=False)` — soft-delete by default
  (move to `.trash/<YYYYMMDD-HHMMSS>-<basename>` inside the vault).
  `permanent=True` does an actual `os.unlink`. Indexer's existing dot-dir
  exclusion handles search/embedding cleanup automatically.
- New tool `set_frontmatter(path, updates: dict, remove: list[str] = [])` —
  structured frontmatter mutation. Parses, merges, re-serializes via
  `yaml.safe_dump`, leaves the body untouched.
- All new and modified tool docstrings use neutral framing (no "MUST call
  `get_vault_guide` first") consistent with the existing tool-design rule.

### Out of scope (explicitly deferred)

- File watching (IMPROVEMENTS.md item #3) — 5-minute reindex is sufficient.
- Git auto-commit and `note_revisions` table — vault is on a file server
  with daily backups; restore-from-backup is the rollback story.
- Cross-tool-call transactions / two-phase commit — overkill for
  personal-vault scale.
- `bulk_edit` — defer until measured-needed.
- Heading-aware chunking (item #4) — separate roadmap item.
- Auto-cleanup of dangling backlinks after `delete_note` — surface via
  existing `find_orphans` / `get_backlinks` instead.

## Capabilities

### New Capabilities

- `vault-write`: structured create/read/update/delete operations on vault
  notes, including atomic write semantics, the four `edit_note` modes
  (full-replace, append, find/replace, section), `move_note`, `delete_note`,
  and `set_frontmatter`. Covers the docstring-framing rule for write tools.

### Modified Capabilities

None. Existing `note-filters` and `vault-guide` capabilities are unaffected.

## Impact

- **Code**: `src/services/vault.py` (atomic `write_file`, frontmatter
  serialize helper), `src/mcp_server/tools.py` (edit_note modes, three new
  `*_impl` functions), `src/mcp_server/server.py` (three new `@mcp.tool()`
  registrations, expanded `edit_note` signature).
- **Database**: `move_note` updates `notes_metadata.file_path` and
  `note_links.target_path`/`source_path` rows for the moved note. No schema
  changes; uses existing tables. Soft-delete cleanup happens via the
  existing periodic reindex (no special-casing).
- **APIs**: New MCP tools `move_note`, `delete_note`, `set_frontmatter`.
  Expanded parameters on `edit_note`. All gated by the existing
  `_require_write()` permission check.
- **Dependencies**: `PyYAML` (already present for frontmatter parsing).
  `difflib` (stdlib). No new packages.
- **Tests**: New smoke checklist at `tests/vault_write_completion_smoke.md`
  following the format of `tests/note_filters_smoke.md` and
  `tests/wikilink_graph_smoke.md`.
- **Docs**: Update `docs/agent-reference.md` "Project Layout" / capability summary if a
  tool index exists. Update `IMPROVEMENTS.md` to reflect items #5/#6 status.
