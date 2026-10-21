# vault-guide Specification

## Purpose
Deliver complete, freshly read vault instructions from an explicit deployment
file map, with caller/topic selection and fail-closed completeness.

## Requirements
### Requirement: Tool name and registration

The MCP server SHALL expose `get_vault_guide(client: str, topics: list[str] = [])`
returning a single string. `client` SHALL be required; omitted topics SHALL
select base guidance. The server SHALL NOT expose `get_vault_context`.
Tool metadata SHALL describe required caller context, topic discovery and
selection; it SHALL NOT advertise an implicit primer or instruction filename.

#### Scenario: Required client and discoverable topics
- **WHEN** a client lists tools
- **THEN** `get_vault_guide` SHALL require `client` and default `topics` to `[]`
- **AND** its description SHALL explain the generated topic catalog
- **WHEN** a caller omits `client` or supplies an invalid declared type
- **THEN** SDK argument validation SHALL refuse the call before its body runs

### Requirement: Strict deployment file map

`VAULT_GUIDE_CONFIG` SHALL define `base.files`, optional `topics.<name>.files`
and optional `topics.<name>.clients.<client>.files`. Base files SHALL be
nonempty. File lists SHALL contain strict strings; unknown fields, duplicate
JSON keys, wrong types and unsafe paths SHALL be rejected before configured
maps are adopted. Topic/client names SHALL start with an ASCII letter/digit
and contain only ASCII letters, digits, underscores and hyphens. Paths SHALL
be canonical printable visible vault-relative POSIX paths of at most 1,024 characters,
without absolute/backslash/empty/dot/hidden/control components or invalid UTF-8.

Missing configuration SHALL permit startup but every guide call SHALL return
an explicit incomplete-guide refusal. Configured malformed maps SHALL fail
Settings validation at startup, without echoing their input values. The server
SHALL NOT silently substitute any map, instruction file or primer.

#### Scenario: Bad map and missing map fail closed
- **WHEN** a map has malformed/duplicate-key JSON, unsupported fields, wrong
  types, empty base or unsafe paths
- **THEN** Settings validation SHALL fail
- **WHEN** the setting is omitted
- **THEN** a guide call SHALL return an incomplete-guide `validation_failed`
  body refusal with no file content

### Requirement: Topic AND client file selection

Every successful call SHALL include base files, then common files for every
requested topic in request order, then matching client additions for those
requested topics in request order. Paths and repeated topics SHALL be
deduplicated preserving first occurrence. Client additions SHALL require BOTH
the enclosing topic and exact case-sensitive client match. A valid unmatched
client SHALL receive common files only. Unknown topics and invalid selector
values SHALL return explicit `invalid_argument` body refusals. The caller's
client value SHALL NOT act as authentication or authorization.

#### Scenario: AND match and negative controls
- **WHEN** `chatgpt-chat` requests both `editing` and `attachments`
- **THEN** base, common topic files and matching attachment additions SHALL appear
- **WHEN** that client requests only editing or bootstrap, or another valid
  client requests attachments
- **THEN** the Chat attachment addition SHALL be absent

#### Scenario: Stable deduplication and unknown topic
- **WHEN** files recur across base, requested topics and matching additions
- **THEN** each mapped path SHALL appear once at its earliest selected position
- **WHEN** any requested topic is unknown
- **THEN** the whole call SHALL refuse without returning base or other files

### Requirement: Fresh complete selected-vault files only

All instruction body content SHALL come from freshly read selected vault files.
The server MAY generate headings, source labels, a map-derived topic catalog
and diagnostics. It SHALL NOT insert bundled primer content, use an implicit
instruction filename, follow includes/links, use index content or cache file
text. Each selected file SHALL appear in full with its vault-relative source
label, including frontmatter and original decoded line endings.

The existing authenticated selected-vault filesystem service SHALL enforce
containment, visibility, symlink and tenant guards. Safe in-vault symlinks
SHALL retain ordinary read behavior. Missing, unreadable, unsafe, empty,
invalid UTF-8 or over-byte-cap required files SHALL produce an explicit
incomplete-guide `validation_failed` body refusal containing no partial file
content. Each read SHALL obey `MAX_FILE_READ_BYTES`; the entire response,
including catalog and labels, SHALL obey `MAX_READ_RESPONSE_CHARS`. Aggregate
overflow SHALL return `size_limit`, never success with truncated instructions.
No compatibility mode or `VAULT_GUIDE_INCLUDE_PRIMER` option SHALL exist.

#### Scenario: Fresh full text and no implicit source
- **WHEN** mapped file text changes between calls
- **THEN** the next call SHALL return changed full text including frontmatter
- **AND** an unmapped `CLAUDE.md` or file linked inside guidance SHALL remain absent

#### Scenario: Required-file and aggregate failures return no partial guide
- **WHEN** any selected file is missing, unreadable, empty, unsafe, invalid UTF-8
  or larger than the existing byte cap
- **THEN** the whole response SHALL be an explicit incomplete-guide refusal
- **WHEN** complete assembled text, source labels and catalog exceed the
  existing response character cap
- **THEN** the whole response SHALL refuse with `size_limit`
- **AND** neither refusal SHALL return already-read instruction text

#### Scenario: Authenticated A/B isolation at the tool boundary
- **WHEN** two authenticated selections with distinct synthetic vault roots
  invoke the actual MCP tool using identical relative maps
- **THEN** each SHALL receive only its selected vault's file text
- **AND** modifying one selected root SHALL be visible on its next call
- **AND** traversal, symlink escape and hidden targets SHALL not expose content

### Requirement: Other tools reference the guide neutrally

MCP tool docstrings on peer tools (e.g., `create_note`, `edit_note`) SHALL NOT instruct the agent to call `get_vault_guide` first using compelling language such as "MUST", "IMPORTANT: Call …first", or equivalent. References to `get_vault_guide` in peer-tool docstrings SHALL use neutral framing such as "see `get_vault_guide` for vault conventions".

#### Scenario: Peer-tool docstrings use neutral framing

- **WHEN** the docstring of a peer write tool (e.g., `create_note`) is inspected
- **THEN** the docstring SHALL NOT contain the phrase "MUST call" or "IMPORTANT: Call … first" in reference to `get_vault_guide`
- **AND** if the docstring mentions `get_vault_guide`, it SHALL frame the reference as informational ("see", "for context", "describes")

### Requirement: Usage logging records the new tool name

The usage-logging system SHALL record invocations of `get_vault_guide` with `tool='get_vault_guide'` in the `usage_logs.tool` column. Pre-existing rows with `tool='get_vault_context'` SHALL be left unchanged for historical accuracy.

#### Scenario: New invocations are logged under the new name

- **WHEN** a client invokes `get_vault_guide`
- **THEN** a row SHALL be appended to `usage_logs` with `tool='get_vault_guide'`

#### Scenario: Historical rows are not rewritten

- **WHEN** the change is deployed
- **THEN** rows previously written with `tool='get_vault_context'` SHALL remain in `usage_logs` with their original tool name unchanged

