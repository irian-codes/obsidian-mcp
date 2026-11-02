# Validation

Focused regression command:

```sh
python -B -m pytest tests/test_empty_folder_trees.py tests/test_tool_annotations.py tests/test_mcp_server_metadata.py tests/test_issue_263_coverage_registry.py tests/test_issue_295_unknown_arguments.py tests/test_issue_261_tool_admission.py tests/test_issue_261_controller.py tests/test_mcp_rate_limits.py tests/test_issue_88_root_confirmed_before_publish.py tests/test_vault_fs.py tests/test_transfer_tools.py tests/test_chatgpt_image_import.py -q
```

Final complete focused run: 623 passed, 1 skipped, including four additional
scenarios: parent-symlink substitution and actual root-assignment confirmation
before, between and throughout removals. The existing move single-publication
parameter is deliberately skipped in its own test. A pre-existing Pydantic
incomplete forward-reference warning remains. Thread-based legacy tests stalled
under restricted execution; the complete run used the existing test environment
outside those restrictions and finished normally.

A temporary live loopback Streamable HTTP server exercised `tools/list`,
`delete_empty_folder_tree` and `read_file` against synthetic temporary trees.
Nonempty refusal, nested empty cleanup, absent retry, read-only denial,
unchanged file bytes and absence of trash all passed. This used injected
synthetic permission context and mocked usage logging; it is not native HTTP
authentication, real PostgreSQL, indexing, Sync or production qualification.

Two independent review rounds found no actionable runtime
defects. The second identified a duplicate test-dictionary key and stale plan
checkboxes; both were corrected. Accepted optimistic substitution/parent-rename
limits are documented in `design.md`; no guarantee of an atomic tree operation
is made. Registry tests were brought current for both pre-existing optional
native-file tools: 26 default registrations, 28 concurrency mapping entries.

The separate integration guidance change passed all five existing package tests.
No dependency or schema changes were introduced. Real PostgreSQL integration,
full repository suite, image build and production deployment were not run.
The shared engineering workflow file and OpenSpec CLI are unavailable; no claim
is made that their unavailable gates executed. Git whitespace checks passed.
