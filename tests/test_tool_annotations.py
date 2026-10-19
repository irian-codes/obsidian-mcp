"""Client-visible tool hints, exercised through the pinned v1 SDK protocol.

The classification is about supported effects, not permissions or rate-limit
classes. In particular, minting a download capability persists token state,
and minting an upload capability can authorize a later overwrite.
"""

import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ListToolsResult

import src.mcp_server.tools as tools
from src.auth.session import current_user_id
from src.mcp_server.auth import current_permission
from src.mcp_server.server import mcp
from src.services import vault as vault_service


READ_ONLY = {
    "keyword_search", "semantic_search", "read_note", "list_notes", "get_tags",
    "get_recent", "get_vault_guide", "get_backlinks", "get_links",
    "get_neighborhood", "find_related", "find_orphans", "read_file",
    "list_files", "check_upload",
}
ADDITIVE = {"create_note", "request_download"}
DESTRUCTIVE = {
    "edit_note", "move_note", "delete_note", "set_frontmatter", "write_file",
    "delete_file", "request_upload", "import_from_url",
}


def _assert_advertised_contract(result: ListToolsResult):
    # Assert the serialized protocol fields, including explicit false values;
    # SDK defaults or attributes on decorators alone cannot satisfy this.
    advertised = json.loads(result.model_dump_json(by_alias=True, exclude_none=True))
    listed = {tool["name"]: tool for tool in advertised["tools"]}
    assert len(advertised["tools"]) == len(listed)
    assert set(listed) == READ_ONLY | ADDITIVE | DESTRUCTIVE
    for name, tool in listed.items():
        assert tool["annotations"] == {
            "readOnlyHint": name in READ_ONLY,
            "destructiveHint": name in DESTRUCTIVE,
            "openWorldHint": name == "import_from_url",
        }, name
        assert tool["inputSchema"]["additionalProperties"] is False, name
    assert "outputSchema" in listed["read_note"]


async def test_fastmcp_list_tools_serializes_annotations_for_entire_surface():
    _assert_advertised_contract(ListToolsResult(tools=await mcp.list_tools()))


async def test_client_session_receives_annotations_for_entire_surface():
    async with create_connected_server_and_client_session(mcp) as session:
        _assert_advertised_contract(await session.list_tools())


@pytest.fixture
def vault(monkeypatch, tmp_path):
    # Run real tool bodies only against synthetic files. Telemetry is the only
    # database write on these paths and is replaced; admission stays active.
    monkeypatch.setattr(tools, "_log_usage", AsyncMock())
    monkeypatch.setattr(tools.settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(tools.settings, "multi_user_mode", False)
    monkeypatch.setattr(tools.settings, "write_precondition_required", False)
    vault_service.clear_user_vault_cache()
    uid = current_user_id.set(None)
    try:
        yield tmp_path
    finally:
        current_user_id.reset(uid)
        vault_service.clear_user_vault_cache()


def _text(result):
    assert result.isError is not True
    return "\n".join(block.text for block in result.content if block.type == "text")


async def test_protocol_reads_and_write_permission_guards_are_preserved(vault):
    note = vault / "sample.md"
    original = "# Sample\noriginal body\n"
    note.write_text(original)
    permission = current_permission.set("read")
    try:
        async with create_connected_server_and_client_session(mcp) as session:
            read = await session.call_tool("read_file", {"path": "sample.md", "encoding": "text"})
            assert _text(read) == original
            structured = await session.call_tool("read_note", {"path": "sample.md"})
            assert structured.isError is not True
            assert structured.structuredContent["content"] == original
            for name, arguments in [
                ("create_note", {"path": "new.md", "content": "new"}),
                ("edit_note", {"path": "sample.md", "content": "changed"}),
                ("request_upload", {"path": "sample.md", "overwrite": True}),
                ("import_from_url", {"url": "https://example.com/file", "path": "new.bin"}),
            ]:
                refused = await session.call_tool(name, arguments)
                assert "Permission denied" in _text(refused), name
            invalid = await session.call_tool("read_file", {"path": "sample.md", "bogus_arg": True})
            assert invalid.isError is True
            assert "bogus_arg" in invalid.content[0].text
    finally:
        current_permission.reset(permission)
    assert note.read_text() == original
    assert sorted(path.name for path in vault.iterdir()) == ["sample.md"]


async def test_protocol_write_preconditions_are_preserved(vault):
    note = vault / "sample.md"
    original = b"# Sample\noriginal body\n"
    note.write_bytes(original)
    stale = "sha256:" + "0" * 64
    permission = current_permission.set("readwrite")
    try:
        async with create_connected_server_and_client_session(mcp) as session:
            for name, arguments in [
                ("edit_note", {"path": "sample.md", "content": "changed", "expected_hash": stale}),
                ("write_file", {"path": "sample.md", "content": "changed", "encoding": "text", "overwrite": True, "expected_hash": stale}),
            ]:
                refused = await session.call_tool(name, arguments)
                assert '"code":"stale_precondition"' in _text(refused).replace(" ", ""), name
                assert note.read_bytes() == original
            current = "sha256:" + hashlib.sha256(original).hexdigest()
            edited = await session.call_tool("edit_note", {
                "path": "sample.md", "content": "changed", "expected_hash": current,
            })
            assert "content_hash:" in _text(edited)
            assert note.read_bytes() == b"changed"
    finally:
        current_permission.reset(permission)
