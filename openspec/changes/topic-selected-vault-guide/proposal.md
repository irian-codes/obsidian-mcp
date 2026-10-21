## Why

The guide currently combines a bundled primer with one implicit vault filename.
A pointer inside that file can leave mandatory instructions unread, while every
call receives unrelated syntax/mechanics. Operators need explicit full-file
assembly with topic and caller context, resolved freshly inside the selected
vault.

## What Changes

- **BREAKING:** require `client` on `get_vault_guide`; optional `topics=[]`
  returns configured base files and a generated discovery catalog.
- Validate `VAULT_GUIDE_CONFIG` JSON file maps; assemble base, requested common
  topic files, then topic AND client additions with stable deduplication.
- Refuse missing configuration, bad selectors and incomplete/oversized files;
  retain selected-vault admission, existing read safety and size limits.
- Remove implicit `CLAUDE.md` lookup and bundled primer output/source.
- Update tool metadata, current specs, README/environment example, architecture
  and focused synthetic tests including actual MCP SDK dispatch.

## Non-goals

Canonical personal policies/templates, private deployment map tooling, vault
installation, production adoption, connector refresh, hosting/service changes,
indexing behavior, write safeguards, client authentication, read-session state,
new resource budgets and automatic guide propagation.

## Impact

Old no-argument clients require metadata refresh and explicit caller values.
Operators must install mapped vault files and set configuration before adopting
this contract; environment changes require selected MCP service recreation.
No database migration or new dependency. Missing maps refuse guide calls while
other functions may start; malformed configured maps fail startup validation.
Canonical reusable guidance extraction belongs to the following private
integration change; removed primer content remains available in Git history.
