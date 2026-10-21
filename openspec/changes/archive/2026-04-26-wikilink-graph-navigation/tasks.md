## 1. Schema and migration

- [x] 1.1 Add `NoteLink` ORM model to `src/models/db.py` with columns: `id`, `source_note_id` (FK → `notes_metadata.id`, ON DELETE CASCADE), `target_note_id` (FK NULL → `notes_metadata.id`, ON DELETE SET NULL), `target_path` (String 1024, NOT NULL), `link_text` (Text), `kind` (String 16, default `"link"`), `position` (Integer)
- [x] 1.2 Add indexes: `ix_note_links_source` on `(source_note_id)`, `ix_note_links_target` on `(target_note_id)`, `ix_note_links_target_path` on `(target_path)`
- [x] 1.3 Generate Alembic migration `005_note_links.py` creating the table and indexes
- [x] 1.4 Run `make db-migrate` against staging/local first to confirm migration is clean

## 2. Link extractor

- [x] 2.1 Create `src/services/links.py` with `extract_links(content: str) -> list[ExtractedLink]` returning a dataclass with `target` (raw target string sans alias/anchor), `link_text` (full original), `kind`, `position`
- [x] 2.2 Implement code-block stripping (fenced ` ``` ` and `~~~`, plus inline `` ` ``) before regex passes
- [x] 2.3 Implement wikilink regex covering `[[Target]]`, `[[Target|Alias]]`, `[[Target#Anchor]]`, `[[Target#Anchor|Alias]]`, `![[Target]]`
- [x] 2.4 Implement markdown-link regex restricted to `.md` (or `.md#anchor`) hrefs
- [x] 2.5 Add unit-style sanity invocations against representative notes (covered in §7)

## 3. Resolution

- [x] 3.1 Add `resolve_target(target: str, source_path: str, vault_index: dict[str, int]) -> int | None` in `src/services/links.py`
- [x] 3.2 Build `vault_index` from `notes_metadata` once per indexer pass: a dict mapping `file_path → id`, plus a dict mapping `stem → list[(file_path, id)]` for bare-name lookups
- [x] 3.3 Implement resolution: path-style first (`<target>.md` or `<target>` against `file_path`), then bare-name with same-folder preference, then alphabetical fallback for ambiguous cases
- [x] 3.4 Strip `#anchor` and `|alias` from target before resolving; preserve the full original wikilink text in `link_text`

## 4. Indexer integration

- [x] 4.1 In `src/services/indexer.py::index_vault`, after metadata upsert/commit, build the `vault_index` from the now-current `notes_metadata`
- [x] 4.2 For each changed note (those in `to_upsert`), delete existing rows from `note_links` where `source_note_id = note.id` and re-insert freshly-extracted+resolved links — do this in batches and within the same session
- [x] 4.3 On note deletion, rely on FK CASCADE to remove `source_note_id` rows, and explicitly UPDATE `note_links` setting `target_note_id = NULL` for any rows that referenced the deleted IDs (covered by `ON DELETE SET NULL`)
- [x] 4.4 On note creation/path change, run a re-resolution pass: `UPDATE note_links SET target_note_id = :id WHERE target_note_id IS NULL AND (target_path = :file_path OR target_path = :stem OR target_path = :file_path_no_ext)`
- [x] 4.5 Add a `link_backfill_pass()` function that runs on startup if `select count(*) from note_links` is zero; iterates all notes, extracts links, batches inserts (chunks of 1000), logs progress every 500 notes

## 5. MCP tools

- [x] 5.1 Add `get_backlinks_impl(path, limit=50)` in `src/mcp_server/tools.py` using a JOIN of `note_links` + `notes_metadata` on `source_note_id`; return rows with source path/title plus `link_text` and `position`; decorated with `@_tracked("get_backlinks", ...)`
- [x] 5.2 Add `get_links_impl(path)` returning resolved + dangling rows distinguished by `resolved` flag; include `target_title` for resolved (via JOIN), NULL for dangling
- [x] 5.3 Add `get_neighborhood_impl(path, depth=1, limit=50)` performing iterative BFS in Python: query the next frontier with `SELECT ... WHERE source_note_id IN :ids OR target_note_id IN :ids`, deduplicate, track `distance` and `via`, stop at depth or limit, clamp `limit ≤ 200`
- [x] 5.4 Add `find_related_impl(path, limit=10)`: fetch the source note's chunks, average their embeddings (numpy), run an ORM `select ... order_by(NoteEmbedding.embedding.cosine_distance(avg))` excluding source, dedupe-by-note in Python by keeping the highest-similarity chunk
- [x] 5.5 Add `find_orphans_impl(folder=None, limit=50)`: subquery of note IDs that appear in `note_links.source_note_id` ∪ `note_links.target_note_id`, then `SELECT ... WHERE id NOT IN (...)`, optional folder prefix, ordered by `modified_at DESC`, clamp `limit ≤ 500`
- [x] 5.6 Register the five tools in `src/mcp_server/server.py` with descriptive docstrings that point agents at the right tool for each use case

## 6. Control panel + project docs

- [x] 6.1 Add a `_graph_stats(session)` helper in `src/control_panel/routes.py` returning `total_links`, `dangling_links`, `orphan_count`, top 5 most-linked-to notes
- [x] 6.2 Pass the stats into the dashboard template; update `dashboard.html` with a "Graph" section
- [x] 6.3 Add a "Link extraction in progress" indicator visible while the startup backfill is running (set a module-level flag in the indexer that the dashboard reads)
- [x] 6.4 Add new tool descriptions to project `AGENTS.md` under a "Graph tools" subsection so the next architectural review reflects the change

## 7. Verification

- [x] 7.1 Manual: deploy migration via `make db-migrate`; confirm `note_links` exists with correct indexes
- [x] 7.2 Manual: redeploy the app; confirm the backfill log shows progress and `select count(*) from note_links` is non-trivial after it completes
- [x] 7.3 Manual: pick a known well-linked note and verify `get_backlinks` and `get_links` return expected results, including a known dangling reference
- [x] 7.4 Manual: call `get_neighborhood(path, depth=2)` against a hub note; confirm `limit` truncation kicks in and `distance` values are correct
- [x] 7.5 Manual: call `find_related` against a fully-embedded note; confirm results are reasonable and the source note is not in the output
- [x] 7.6 Manual: call `find_related` against a note that has not been embedded yet; confirm the "not yet embedded" message
- [x] 7.7 Manual: call `find_orphans()`; confirm the count matches what the dashboard widget shows
- [x] 7.8 Manual: rename or create a note that resolves a previously-dangling link; on the next index pass, confirm the corresponding `note_links.target_note_id` is now populated
- [x] 7.9 Manual: delete a note; confirm rows for that note as source are gone (CASCADE) and rows that targeted it have `target_note_id = NULL`
- [x] 7.10 Manual: load `/admin/` and confirm the Graph widget renders correct numbers and clickable top-hub links
