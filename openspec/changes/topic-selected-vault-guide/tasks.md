## Implementation

- [x] Validate strict deployment JSON/maps, selectors and safe paths.
- [x] Require tool client; advertise base/topic discovery and AND matching.
- [x] Assemble fresh complete selected files with stable path deduplication.
- [x] Refuse bad maps, selectors, files and existing-limit overflow; typed outcomes.
- [x] Remove implicit instruction lookup and automatic primer source/output.
- [x] Update current specs, README, environment example and architecture.
- [x] Add focused synthetic and actual in-process MCP SDK tests.
- [x] Update outcome registry and required-client integration call fixture.

## Validation and delivery gates

- [x] Initial focused suite: 225 tests passed; no live database/HTTP claim.
- [x] Guide-focused suite after fixes: 59 passed. Extended read/admission suite:
  452 passed, one pre-existing registry-count failure (25 expected, 27 actual).
- [x] Broad offline invocation completed: 6,773 passed, 846 skipped, four
  failures (existing concurrency-map assumptions and a transfer-refusal case),
  75 mandatory transfer-Postgres setup errors because its database URL was unset.
  All four failing cases reproduced on the clean unchanged base; a representative
  transfer-Postgres case reproduced the same mandatory missing-URL error.
  No database or services started; this does not qualify the real-Postgres gate.
- [ ] OpenSpec strict validation unavailable: CLI absent; offline npx returned
  ENOTCACHED. No network installation attempted.
- [x] Two independent parent review rounds: no runtime blockers; hermetic
  Settings test fix and current deployment/provenance documentation fixes applied.
  Shared workflow file unavailable; named unavailable gates not claimed.
- [ ] Human review before shipping; no deployment or production acceptance here.
