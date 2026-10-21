## 1. Atomic write foundation

- [x] 1.1 Refactor `src/services/vault.py::write_file` to write via tmp file (`.tmp-<name>-<pid>-<hex>`) in the same directory followed by `os.replace()`. Clean up the tmp file on exception. Verify behavior on the existing `create_note` and `edit_note` paths still works.
- [x] 1.2 Add a unit-style smoke check (run-once script or notes in the smoke checklist) confirming a partial write does not truncate the destination — simulate by raising mid-write and asserting destination unchanged.

## 2. Frontmatter helper extraction

- [x] 2.1 Locate the existing frontmatter parsing path (likely inside the indexer or `src/services/links.py`).
- [x] 2.2 Factor a public `parse_frontmatter(text: str) -> tuple[dict, str]` and `serialize_frontmatter(meta: dict, body: str) -> str` into `src/services/vault.py` (or new `src/services/frontmatter.py`). Update existing call sites to use it. Ensure indexer behavior is unchanged.
- [x] 2.3 Cover the no-frontmatter case and the frontmatter-not-on-line-1 case in the parser per the spec ("must start on line 1" — anything else is treated as no frontmatter).

## 3. `edit_note` robustness

- [x] 3.1 Add `dry_run: bool = False`, `replace_all: bool = False`, `section: str | None = None` parameters to `edit_note_impl` in `src/mcp_server/tools.py` and to the `@mcp.tool()`-decorated `edit_note` in `src/mcp_server/server.py`.
- [x] 3.2 Implement mode-mutex validation: setting more than one of `append=True`, `find=...`, `section=...` returns an error naming the conflicting parameters and does not mutate. Order of checks: validate first, compute would-be content second, write (or diff) third.
- [x] 3.3 Implement `replace_all=True` branch: when `find` matches N≥1 times, replace all and report `"Replaced N occurrence(s) in <path>"`. Keep current single-match guard when `replace_all=False`.
- [x] 3.4 Implement section-mode helper: `replace_section(text: str, heading: str, new_body: str) -> str | error`. Strip fenced/inline code (reuse the existing helper from `src/services/links.py` if it's exposed; otherwise factor it out). Match ATX headings only. Bound replacement at the next heading of equal-or-shallower depth. On not-found, return an error listing all headings in the note with their depths. On multiple matches, return an error instructing path-style disambiguation.
- [x] 3.5 Implement path-style heading disambiguation (`Parent/Child`): match `Child` whose nearest enclosing heading at depth ≤ child's depth has trimmed text `Parent`.
- [x] 3.6 Implement `dry_run=True`: compute would-be content via the same code path, then return `difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=path, tofile=path, lineterm="")` joined as a string. Return `"No changes for <path>"` when before == after.
- [x] 3.7 Update the `edit_note` docstring in `src/mcp_server/server.py` to describe the four modes and the `dry_run`, `replace_all` flags. Keep neutral framing wrt `get_vault_guide`. Mention ATX-only limitation for `section` mode and PyYAML comment-loss for the eventual `set_frontmatter`.

## 4. `move_note` tool

- [x] 4.1 Add `move_note_impl(from_path, to_path, rewrite_links=False)` in `src/mcp_server/tools.py` decorated with `@_tracked("move_note", ["from_path", "to_path", "rewrite_links"])`.
- [x] 4.2 Validate both paths via `validate_path`. Refuse if dest exists. Create dest parent dirs.
- [x] 4.3 File move via `os.replace` (or `shutil.move` fallback for cross-FS).
- [x] 4.4 In one DB transaction, update `notes_metadata.file_path` for the moved note and `note_links.target_path` / `source_path` for matching rows. Log a warning if the FS move succeeded but DB updates failed (reindex will reconcile).
- [x] 4.5 Implement the `rewrite_links=True` branch: query source notes via `note_links` (or call `get_backlinks_impl`), open each, run regex rewrites for `[[Old]]`, `[[Old|alias]]`, `[[Old#anchor]]`, `[[Old#^block]]`, `![[Old...]]`, and path-style `[[folder/Old]]` variants. Write back via `write_file` (atomic). Preserve aliases and anchors; only the title portion is rewritten.
- [x] 4.6 Register `@mcp.tool() async def move_note(...)` in `src/mcp_server/server.py` with neutral docstring (mentions `get_vault_guide` only as "see for context").

## 5. `delete_note` tool

- [x] 5.1 Add `delete_note_impl(path, permanent=False)` in `src/mcp_server/tools.py` decorated with `@_tracked("delete_note", ["path", "permanent"])`.
- [x] 5.2 Validate path. If missing, return actionable error.
- [x] 5.3 Soft-delete branch (default): create `<vault>/.trash/` if needed; compute `<YYYYMMDD-HHMMSS>-<basename>` destination; `os.replace` (or `shutil.move`); return message including the trash path.
- [x] 5.4 Permanent branch: `os.unlink`; return message stating permanent delete.
- [x] 5.5 Do NOT touch the DB directly — let the next reindex pass remove the row from `notes_metadata` and cascade `note_embeddings` and `note_links`.
- [x] 5.6 Register `@mcp.tool() async def delete_note(...)` in `src/mcp_server/server.py` with neutral docstring (mention that soft-deleted files accumulate in `.trash/` and are the user's responsibility to empty).

## 6. `set_frontmatter` tool

- [x] 6.1 Add `set_frontmatter_impl(path, updates: dict, remove: list[str] = [])` in `src/mcp_server/tools.py` decorated with `@_tracked("set_frontmatter", ["path"])` (omit `updates`/`remove` from logged params if they may be large; check `_tracked` truncation).
- [x] 6.2 Read note via `read_file`. Use the parse helper from §2 to split frontmatter and body.
- [x] 6.3 Apply `updates` (overwriting/adding), then drop keys in `remove`. If `updates == {}` and `remove == []`, return "no changes" without writing.
- [x] 6.4 Re-serialize via `yaml.safe_dump(default_flow_style=False, sort_keys=False, allow_unicode=True)`. Reassemble as `---\n<yaml>---\n<body>`. If the note had no frontmatter, prepend a fresh block before the unchanged body.
- [x] 6.5 Write back via `write_file` (atomic). Return summary of changes ("set: <keys>; removed: <keys>").
- [x] 6.6 Register `@mcp.tool() async def set_frontmatter(...)` in `src/mcp_server/server.py` with neutral docstring; document the PyYAML comment-loss caveat.

## 7. Smoke checklist

- [x] 7.1 Create `tests/vault_write_completion_smoke.md` modeled after `tests/note_filters_smoke.md` and `tests/wikilink_graph_smoke.md`. Each new tool and each `edit_note` mode gets a checklist item with the exact arguments to pass and the expected response shape. Include the dry-run, replace_all, and section-mode cases. Include both soft-delete and permanent delete. Include `move_note` with and without `rewrite_links`.

## 8. Documentation refresh

- [x] 8.1 Update docs/agent-reference.md "Project Layout" or any tool index with the new `move_note`, `delete_note`, `set_frontmatter` tools and the expanded `edit_note` modes.
- [x] 8.2 Update IMPROVEMENTS.md to mark items #5 (the parts we adopted: dry-run + atomic write) and #6 (move/delete/set_frontmatter) as shipped, and re-number the suggested order accordingly. Note explicitly which sub-items were skipped (git auto-commit, `note_revisions`, `bulk_edit`).

## 9. Verification

- [x] 9.1 Per the openspec-workflow rule (memory): after implementation, before deploy/archive, spawn a fresh subagent with the spec at `openspec/changes/vault-write-completion/specs/vault-write/spec.md` and the diff. Have it verify each requirement's scenarios are covered by the actual code (not just the smoke checklist) and report any spec-vs-code drift before we commit to deploy.
- [x] 9.2 Run `make deploy`. Verify health, then walk through `tests/vault_write_completion_smoke.md` against the live server.
- [ ] 9.3 Archive the change via `/opsx:archive vault-write-completion`.
