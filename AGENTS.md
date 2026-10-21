# Project conventions

This file is the canonical entry point for developer instructions for both
Claude and Codex. Read and follow the complete
[developer reference](docs/agent-reference.md) before working in this repository;
it preserves the project conventions, commands, validation gates and decisions.
Keep shared developer conventions here and in that reference.

## Workflow and review

Read the shared engineering workflow at
`~/.claude/rules/engineering-workflow.md`. The explicit project-specific
overrides here and in the developer reference take precedence. If the shared
file is unavailable, report that limitation and continue with the available
project instructions. Claude-specific tools, model names and slash commands
require an available equivalent; do not claim an unavailable gate ran.

Triage adversarial findings on plausible input and real impact. Use two full
Codex rounds by default, then verification-only; record accepted limitations in
the spec and say which findings were declined. Changes to write tools, section
addressing or chunking/embedding require an adversarial pass: destructive
writes and silently wrong search results are the expensive failures here.

For MCP-side changes, substitute an end-to-end exercise of the affected MCP
tools for the `user-representative` browser pass, and report which tools were
called. Panel, auth, consent-template and `panel.js` changes require the manual
browser pass with devtools open and zero CSP violations. Follow the full
[engineering workflow and local gates](docs/agent-reference.md#engineering-workflow).

## Architecture and validation

Read the relevant [architecture note](docs/agent-reference.md#architecture-notes)
before changing that area, and update it in the same change. Those notes hold
the load-bearing rationale; do not silently reverse their decisions. Formal
requirements live in `openspec/specs/`, with history in
`openspec/changes/archive/`.

Run `make test-schema` before a deploy carrying a migration, and `make db-check`
after any migration and after a deploy that ran one; `alembic check` must report
"No new upgrade operations detected." `tests/integration/` skips without
`PGVECTOR_TEST_ADMIN_URL`: only claim the real Postgres suite passed when
`make test-integration` ran. Do not run it concurrently with `make test-schema`;
they share the throwaway container and port. See the reference for all gates.

## Public repository and scope

Keep host-specific paths, secrets and hostnames outside tracked files; use the
gitignored `Makefile.local` overrides. Invoke `make` from the repository so
those overrides load. See the [deployment conventions](docs/agent-reference.md#public-repo--host-paths-live-outside-the-tree)
before changing deployment configuration.

Repository developer instructions are separate from vault content. The current
`get_vault_guide` tool reads `CLAUDE.md` at the **vault** root; moving this
repository's developer instructions does not change that runtime contract.
