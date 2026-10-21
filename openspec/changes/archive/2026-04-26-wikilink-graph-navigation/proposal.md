## Why

Obsidian's defining feature is the `[[wikilink]]` graph: notes reference each other and the resulting backlink/forward-link structure is how humans actually navigate the vault. Our MCP server currently treats the vault as a flat folder of markdown files — no link parsing, no backlinks, no neighborhood traversal. Agents can find Note A by search, but they cannot follow the threads the way you would when reading the vault yourself. This blocks high-value workflows like "summarize everything connected to this project", "find orphan notes", or "find notes related to this one I'm reading". The information is already in the markdown — we just are not extracting and exposing it.

## What Changes

- Parse `[[wikilinks]]` and resolved `[md links](path.md)` during indexing and store them in a new `note_links` table (`source_note_id`, `target_path`, `target_note_id`, `link_text`, `position`).
- Resolve wikilink targets to note IDs via Obsidian's filename-first resolution: if the wikilink is `[[Foo]]`, prefer a note whose stem is `Foo` anywhere in the vault; if `[[Folder/Foo]]`, prefer that exact path. Unresolved links are stored with `target_note_id = NULL` so agents can also see broken/dangling links.
- Add an MCP tool `get_backlinks(path, limit)` returning notes that link TO `path`, with surrounding context.
- Add an MCP tool `get_links(path)` returning notes that `path` links TO, plus unresolved targets.
- Add an MCP tool `get_neighborhood(path, depth=1, limit=50)` returning the connected subgraph N hops out (links + backlinks).
- Add an MCP tool `find_related(path, limit=10)` returning semantically-similar notes by averaging the source note's chunk embeddings and querying pgvector — independent of the link graph but a natural companion.
- Add an MCP tool `find_orphans(limit, folder)` returning notes with zero incoming AND zero outgoing resolved links — useful for vault hygiene.
- Re-index links incrementally on every change (same hash trigger that drives metadata/embeddings).
- Surface basic graph stats in the control panel dashboard (total links, unresolved/dangling count, top hub notes).

This is **non-breaking** — purely additive tools and a new table.

## Capabilities

### New Capabilities
- `wikilink-graph`: Link extraction during indexing, link storage and resolution, and MCP tools that expose backlinks, forward links, neighborhood traversal, related notes, and orphans.

### Modified Capabilities
<!-- No existing specs in openspec/specs/. -->

## Impact

- **Code**:
  - `src/services/links.py` (new) — link parser and resolver, batch upsert helpers.
  - `src/services/indexer.py` — call link extraction after metadata upsert; populate `note_links` for changed notes; on file delete, cascade clears links.
  - `src/services/vault.py` — small helper for resolving a wikilink target string to a vault path.
  - `src/models/db.py` — new `NoteLink` model.
  - `alembic/versions/` — new migration creating `note_links` with composite indexes on `(source_note_id)` and `(target_note_id)`, plus on `(target_path)` for unresolved-link lookup.
  - `src/mcp_server/tools.py` — new `_impl` functions for the five new tools.
  - `src/mcp_server/server.py` — register the new tools.
  - `src/control_panel/routes.py` + dashboard template — graph stats widget.
- **DB schema**: One new table `note_links`. Migration runs on next `make deploy`.
- **APIs**: Five new MCP tools. No changes to existing tool signatures.
- **Performance**: Indexer does one extra parse pass per changed note (~µs each); link upsert batched with the existing 100-row batches. Backlinks query uses the `(target_note_id)` index — fast even at 100K links. Neighborhood is BFS bounded by `depth` and `limit`.
- **Dependencies**: None new. Wikilink parsing is a regex.
- **Docs**: Project `AGENTS.md` gets a short note on the new tools; vault `CLAUDE.md` may benefit from guidance on when an agent should call `get_neighborhood` vs `find_related` vs `search`.
