## 1. Author the primer content

- [x] 1.1 Create `src/mcp_server/vault_guide_primer.md` with sections for: wikilinks (`[[Note]]`, `[[Note|alias]]`), embeds (`![[Note]]`), block refs (`[[Note#^id]]`), heading refs (`[[Note#Heading]]`), backlinks, tags including nested (`#tag/nested`), YAML frontmatter conventions, and a "Dataview / Templater appears as literal text" note
- [x] 1.2 Keep the primer under ~150 lines / ~2KB; favor terse examples over prose
- [x] 1.3 Open the primer file in a Markdown preview to verify rendering and example syntax is correct

## 2. Implement `get_vault_guide`

- [x] 2.1 In `src/mcp_server/tools.py`, add a module-level constant `_VAULT_GUIDE_PRIMER` that loads the primer file once at import (use `pathlib.Path(__file__).parent / "vault_guide_primer.md"`)
- [x] 2.2 Rename `get_vault_context_impl` to `get_vault_guide_impl` and update the `@_tracked("get_vault_context", [])` decorator to `@_tracked("get_vault_guide", [])`
- [x] 2.3 Replace the body so it returns: primer + section separator + `CLAUDE.md` contents (when present) OR primer + section separator + onboarding message (when absent)
- [x] 2.4 Ensure the absent-`CLAUDE.md` branch returns successfully (no exception, no error string) — the response should be a positive instruction to create one
- [x] 2.5 Use clear section headers in the response (e.g., `# Obsidian Primer` and `# Vault-Specific Conventions`) so the agent can parse the two sections

## 3. Wire up the renamed tool

- [x] 3.1 In `src/mcp_server/server.py`, update the import on line 10 from `get_vault_context_impl` to `get_vault_guide_impl`
- [x] 3.2 Rename the registered tool function `get_vault_context` to `get_vault_guide` (around line 188-194) and update its docstring to matter-of-factly describe what it returns ("Returns the Obsidian primer and any vault-specific conventions from CLAUDE.md") without compelling language
- [x] 3.3 Update peer-tool docstrings on lines ~153 and ~169 to replace "IMPORTANT: Call get_vault_context first…" with neutral framing like "See `get_vault_guide` for vault conventions"
- [x] 3.4 Search for any remaining references to `get_vault_context` in the codebase (`grep -rn "get_vault_context" src/ tests/`) and update them

## 4. Verify usage logging

- [x] 4.1 Confirm the `@_tracked("get_vault_guide", [])` decorator routes invocations to `usage_logs` with `tool='get_vault_guide'` (mirrors the `keyword_search` rename pattern from commit `bbf8dc1`) — verified by inspection: `_log_usage(tool_name, ...)` passes the decorator's name string verbatim
- [x] 4.2 If the control panel has a tool-name allowlist or display map (check `src/api/` and `src/control_panel/`), add `get_vault_guide` so logs render with proper labels — none exists; `_usage_detail` in `src/control_panel/routes.py:78` returns `None` for tools with no params, matching prior `get_vault_context` behavior
- [x] 4.3 Manually invoke the tool through the MCP endpoint and confirm a row appears in `usage_logs` with the new name — verified post-deploy: two `get_vault_guide` rows present in `usage_logs`; historical `get_vault_context` rows preserved unchanged

## 5. Update docs

- [x] 5.1 Search README.md and `src/control_panel/templates/` for `get_vault_context` references and update to `get_vault_guide` — N/A: no README exists; templates have no tool-name strings
- [ ] 5.2 Add a short README section explaining that users can create `CLAUDE.md` at the vault root to teach the agent vault-specific conventions, with a brief example of what to include — DEFERRED: no README exists, and project guidelines require explicit user request before creating one. The tool's "no CLAUDE.md found" response (in `_NO_CLAUDE_MD_MESSAGE`) now self-documents this. Reopen if a README is added later.
- [x] 5.3 Update docs/agent-reference.md (repository documentation, not vault content) if it documents the tool surface — N/A at implementation time: developer documentation did not enumerate MCP tools by name

## 6. Test and deploy

- [x] 6.1 Run any existing tests; rename references to `get_vault_context_impl` if found (search shows none today, but verify) — N/A: no automated test suite. Manual smoke-test doc `tests/note_filters_smoke.md:128` already updated to use `get_vault_guide()`. Standalone syntax + AST checks pass for `tools.py` and `server.py`; primer covers all 7 required syntax elements (wikilink, embed, block ref, heading ref, nested tag, frontmatter fence, plugin-literal note).
- [x] 6.2 Start the server locally and call `get_vault_guide` via `curl` against the MCP endpoint to confirm it returns the primer + vault section — verified via `docker compose exec` invoking `get_vault_guide_impl()` directly: 19,685-char response containing both `# Obsidian Primer` and `# Vault-Specific Conventions` headers plus the real `CLAUDE.md` content
- [x] 6.3 Test the absent-`CLAUDE.md` path by temporarily renaming the vault's `CLAUDE.md` and confirming the tool returns the onboarding message instead of an error — verified by mocking `read_file` to raise `FileNotFoundError`: response is 3,627 chars, contains primer + onboarding message ("No `CLAUDE.md` found at the vault root...") with no error string
- [x] 6.4 Run `make deploy` to ship the change — completed: image built and pushed to `localhost:5000/obsidian-mcp:latest`, container recreated, health check passing

## 7. Out-of-repo follow-ups

- [ ] 7.1 Update the Claude Code Obsidian skill (referenced in conversation, lives outside this repo) to reference `get_vault_guide` instead of `get_vault_context` — out of this repo; user follow-up
