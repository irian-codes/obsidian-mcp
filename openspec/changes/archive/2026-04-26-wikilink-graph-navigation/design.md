## Context

The vault contains ~2,577 markdown files. Notes reference each other via two link forms:

1. **Wikilinks**: `[[Note Name]]`, `[[Folder/Note Name]]`, `[[Note Name|alias]]`, `[[Note Name#Heading]]`, `[[Note Name#Heading|alias]]`. Obsidian-native. The most common form.
2. **Markdown links**: `[label](relative/path.md)`, occasionally `[label](Note%20Name.md)`. Standard CommonMark.

Embeds (`![[Note]]`) are a third form but for graph purposes can be treated identically to wikilinks.

The indexer (`src/services/indexer.py::index_vault`) already iterates every changed file, parses frontmatter, computes a content hash, and upserts `notes_metadata`. Adding link extraction here costs essentially nothing — we are already holding the file contents in memory.

Resolution rules (mirroring Obsidian's defaults):
- If the link target contains a `/`, treat it as a vault-relative path and try `<target>` and `<target>.md`.
- If it has no `/`, search by file *stem* (basename without extension) across the whole vault. If multiple notes share a stem, prefer one in the same folder as the source, then fall back to the alphabetically first.
- Heading anchors (`#Heading`) and aliases (`|alias`) do not change the target note; we strip them before resolving and store the original anchor/alias text as part of the link record.

If a link does not resolve to any existing note, store it as a "dangling" link with `target_note_id = NULL` and the raw target string in `target_path`. These are useful for the agent ("create the note this links to") and for vault-hygiene tools.

## Goals / Non-Goals

**Goals:**
- Persistent graph derived from the vault's actual content, kept up-to-date with the same incremental cadence as the rest of the index.
- Cheap backlink and forward-link lookups (single-table, single-key index hits).
- BFS-based neighborhood expansion bounded by depth and result limit.
- Embedding-based related-notes lookup that works even when no explicit links exist.
- Visibility on dangling links and orphan notes for vault hygiene.

**Non-Goals:**
- Updating wikilinks when notes are moved/renamed (no rename tool exists yet — this is in the next-up roadmap).
- Markdown anchor resolution (we keep the heading anchor as text but do not validate that the heading exists).
- Property-graph traversal beyond simple BFS (no SPARQL, no shortest-path).
- Block references (`[[Note^block-id]]`).
- Bidirectional / "auto-linked" inference based on title mentions in plain text.
- Tag-as-node graph (already covered by `get_tags` + filters).

## Decisions

### Decision: Store links in a normalized SQL table, not a graph DB

**What:** Single table `note_links(id, source_note_id, target_note_id NULL, target_path, link_text, position)` with indexes on `source_note_id`, `target_note_id`, and `target_path`.

**Why:**
- Backlinks/forward-links are 99% of access patterns; both are single-table single-index lookups.
- Neighborhood BFS at `depth ≤ 3` over a vault this size is sub-millisecond per hop in Postgres.
- We already run Postgres + pgvector. Adding a graph DB for this is overkill.
- JOINs to `notes_metadata` give title/tags/path for free.

**Alternatives considered:**
- An adjacency JSONB column on `notes_metadata`. Cheaper to maintain but no efficient backlink query.
- Neo4j or AGE. More expressive but big operational cost for marginal benefit at this scale.

### Decision: Resolve wikilink targets at index time, not at query time

**What:** During link extraction, look up the target note ID and write it into `note_links.target_note_id` (or NULL if unresolved). When notes are added/renamed/deleted, re-resolve any links that pointed at or now match the changed paths.

**Why:**
- Backlinks queries become an indexed lookup on `target_note_id`, no per-query string matching.
- Front-loads the cost into the indexer (which already runs in the background).

**Alternatives considered:**
- Resolve at query time by joining on `target_path` against `notes_metadata.file_path`. Simpler to implement, but every backlink query becomes a string match across the whole table — and we pay it on every call.

**Resolution invalidation rule:** When a note is created or its path changes, scan `note_links` for any rows whose `target_path` matches the new note's vault-relative path, name, or stem and update `target_note_id`. This keeps dangling-link counts accurate.

### Decision: Filename-first wikilink resolution with same-folder preference

**What:** Given `[[Foo]]`:
1. If a note exists at `<source_dir>/Foo.md`, use it.
2. Otherwise, find all notes whose `file_path` ends with `/Foo.md` or equals `Foo.md`. If exactly one, use it.
3. If multiple, prefer the one in `<source_dir>/`, then the alphabetically first.
4. If none, mark unresolved with `target_path = "Foo"`.

For `[[Path/With/Slashes]]`, treat as a path attempt: try `<target>.md` then `<target>` (already-extensioned).

**Why:** Matches Obsidian's default "shortest path when possible" behavior closely enough for personal use. Same-folder bias matches how people actually write vault notes.

**Alternatives considered:**
- Strict path-only resolution. Simpler but breaks `[[Foo]]` patterns that are 80% of real wikilinks.

### Decision: Single regex pass with explicit forms, ignoring code blocks

**What:** Strip fenced code blocks (` ``` ` and `~~~`) and inline code (`` ` `` ) before regex matching to avoid false positives from links inside code samples. Then run two regexes:
- Wikilink: `(?P<embed>!)?\[\[(?P<target>[^\]\|#]+)(?:#(?P<anchor>[^\]\|]+))?(?:\|(?P<alias>[^\]]+))?\]\]`
- Markdown link: `\[(?P<text>[^\]]+)\]\((?P<href>[^)]+\.md)(?:#[^)]*)?\)`

**Why:**
- These forms cover essentially every link in the vault.
- Stripping code blocks before parsing avoids the embarrassing cases where examples in tech notes generate fake backlinks.
- One regex per form is fast and grep-able.

**Alternatives considered:**
- A real markdown parser (markdown-it-py, mistune). More accurate, more dependency, marginal gain for a personal vault.

### Decision: Re-extract links per changed note, not differentially

**What:** For every note in the indexer's `to_upsert` list, delete its existing rows in `note_links` and re-insert from scratch.

**Why:** Diffing links is fiddly and a single note has at most a few dozen links — full replace is fast and simple. Wrapped in the same transaction as the metadata upsert so backlink queries never see partial state.

**Alternatives considered:**
- Diff old vs. new link sets and emit only inserts/deletes. More efficient on tiny edits, but the simpler approach is plenty fast.

### Decision: BFS neighborhood with `(depth, limit)` budget

**What:** `get_neighborhood(path, depth=1, limit=50)` returns the connected subgraph reachable in ≤ `depth` hops following links **or** backlinks (treated as undirected). Capped at `limit` distinct notes total. Each result row carries `distance` (hop count) and `via` (the path of the predecessor that brought it in).

**Why:** Most agent use cases are "what's around this?", which is symmetric. BFS gives a stable expansion order; the limit prevents exploding on hub notes.

**Default depth = 1** because that's almost always what the agent wants and it keeps the response small.

### Decision: `find_related` averages chunk embeddings, then queries pgvector

**What:** Fetch all chunk embeddings for the source note, average them into a single 1024-d vector, then run cosine-distance order against `note_embeddings.embedding` joined to `notes_metadata`, excluding the source note itself, deduped to one row per note (best chunk).

**Why:**
- Cheap and well-defined; single SQL query.
- Average-of-chunks is a noisy but acceptable summary at this scale.
- Independent of the link graph — useful when the source note is sparsely linked.

**Alternatives considered:**
- For each chunk run a separate similarity query and fuse via RRF. Better quality, more queries. Defer until base behavior is observed in use.

### Decision: Dangling/orphan visibility through a panel widget and tools

**What:**
- `find_orphans(folder, limit)` MCP tool returns notes with zero incoming AND zero outgoing resolved links.
- Dashboard widget shows `total_links`, `dangling_links`, `orphan_count`, top 5 most-linked-to notes.

**Why:** Cheap to compute, useful for vault hygiene, and gives an at-a-glance signal that the graph extraction is working.

## Risks / Trade-offs

- **[Wikilink resolution is heuristic and can be wrong]** → Same-folder-first plus alphabetical fallback occasionally picks the wrong "Foo.md" when two exist in different subtrees. **Mitigation**: store the literal target string in `target_path` for transparency; expose it in `get_links` output so the agent can detect ambiguity. Re-resolution on note creation keeps things converging.

- **[Re-extracting links on every change is wasteful when only frontmatter changed]** → A 50-MB note with 200 links gets re-parsed on any edit. **Mitigation**: link parsing is regex-only over already-loaded text; cost is microseconds. Premature optimization not worth chasing.

- **[`get_neighborhood` can blow up on hub notes]** → A note linked from 500 others has a depth-2 neighborhood of thousands. **Mitigation**: hard `limit` (default 50, max 200). BFS expands distinct notes only; once `limit` is hit, expansion stops and the response notes that the result was truncated.

- **[`find_related` quality depends on embedding coverage]** → Source notes that have not been embedded yet (the indexer is still catching up) return zero results. **Mitigation**: surface "not yet embedded" in the response so the agent doesn't think the note has no neighbors.

- **[Code-block stripping is approximate]** → A regex strip can miss exotic markdown (e.g., indented code blocks). False positives from such cases will produce phantom links. **Mitigation**: rare in this vault; can add a real markdown parser later if it bites.

- **[Indexer migration on existing data]** → The first deploy after this lands has to populate `note_links` for all 2,577 notes. **Mitigation**: do this as a one-shot pass at startup if `note_links` is empty; logged with progress (`Linked N/2577 notes`).

## Migration Plan

1. Create alembic migration adding `note_links` table with required indexes; deploy via `make db-migrate`.
2. Ship the link extractor (`src/services/links.py`) and indexer integration. On first run after deploy:
   - If `note_links` is empty, the indexer iterates all notes once to backfill (separate startup phase logged as "link backfill", runs after `index_vault` and before `embed_vault`).
   - On subsequent runs, links are kept current incrementally as part of normal indexing.
3. Add the five MCP tools and register them.
4. Update the dashboard template + a small route to compute graph stats.
5. Update project `docs/agent-reference.md` with new tool descriptions.

**Rollback:** Drop the `note_links` table and remove the new tools. The metadata/embedding pipelines do not depend on links, so a partial rollback is safe.

## Open Questions

- Should embeds (`![[...]]`) be marked distinctly in `note_links.link_text` so an agent can tell embeds from references? Probably yes via a `kind` column (`"link" | "embed" | "markdown"`); cheap to add now and painful later. Resolution: add `kind` column.
- Are unresolved-link counts useful enough to warrant a dedicated `get_dangling_links()` tool? Defer — the dashboard widget covers visibility, and the agent can derive it from `get_links()` per-note.
