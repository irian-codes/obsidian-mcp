## Contract and scope

Implement the generic public-server portion of file-mapped topic guidance.
`client` is caller-declared application/mode context, never authentication.
Public source contains generic assembly and synthetic examples, not canonical
operator policy. Tool count, admission/resource class and write behavior stay
unchanged. No new dependency or migration.

## Configuration

Strict Pydantic models validate a nonempty `base.files`, optional `topics`,
required per-topic `files`, and optional per-topic `clients` file groups.
All groups forbid unsupported fields and require string lists. Strict JSON
parsing rejects duplicate object keys at every level. Names use exact
case-sensitive `[A-Za-z0-9][A-Za-z0-9_-]*` identifiers. Configured paths use
canonical visible relative POSIX syntax and the existing 1,024-character path
bound; actual selected-vault containment/visibility is checked again on read.

Missing configuration remains `None` so unrelated server/migration imports can
start, but every guide call returns an explicit incomplete-guide refusal.
Configured malformed JSON/maps fail Settings validation before adoption.
Existing hidden-input settings errors avoid printing raw deployment maps.
Configuration text is process configuration; adopt changes by recreating only
the selected MCP service. Content itself is never cached.

## Selection and presentation

Validate caller/topic selectors, reject unknown topics before reads, deduplicate
requested topics, then select base files, all requested common files, and all
matching topic/client additions. Stable path deduplication retains first
occurrence. Valid unmatched clients receive common files. Catalog order follows
configuration insertion order; selected topic order follows the request.
Headings, source labels and a map-derived catalog are generated structural text.
Every instruction body comes from explicitly selected files. Links/includes
inside them are inert. There is no fallback or bundled instructional content.

## Complete fresh reads

Call existing `vault.read_bytes` with selected authenticated user id and
`MAX_FILE_READ_BYTES`, decode UTF-8 strictly, preserve full file text including
frontmatter/newlines, and require non-whitespace content. Filesystem reader
retains its ordinary safe in-vault symlink behavior and refuses escape/hidden
targets. Existing admission/overlap/request-selected root guards remain active.
Do not echo OS errors/host paths. Every selected required-file failure omits
all previously assembled instruction content. The aggregate response including
catalog/labels must fit `MAX_READ_RESPONSE_CHARS`; refuse instead of truncating.

Use existing typed post-body outcomes: `validation_failed` for absent config
and file completeness/safety/byte failures, `invalid_argument` for invalid or
unknown selectors, `size_limit` for aggregate response overflow. Missing client
and declared-type failures remain SDK pre-body argument errors. No new refusal
code or telemetry field is needed. Caller-controlled selectors/maps are not
added to usage logging.

## Accepted limitations

Reads are sequential fresh reads, not an atomic multi-file snapshot. Existing
filesystem race/mount limitations remain as documented for ordinary reads.
Guide instructions remain advisory; enforcement stays in authenticated vault
and write services. One deployment map applies relative to every tenant root,
so operators must install its required files in every served vault. Canonical
reusable policy installation and production acceptance are separate changes.

## Verification

Synthetic files only: strict JSON/maps/paths/selectors, required client schema,
base/topic order/deduplication, AND negative controls, full frontmatter/text,
missing/unreadable/invalid UTF-8/empty/byte and aggregate limits, safe symlinks
and escape/hidden refusal, freshness and selected A/B isolation through actual
in-process MCP SDK sessions. Stub usage DB logging only and emulate already
authenticated selection with ContextVars; this is not live HTTP/auth coverage.
Run relevant read/admission/outcome/schema regressions. Real Postgres suites
require their configured test database; do not claim those gates passed without
running them. The transfer-Postgres gate fails deliberately when its URL is absent.
