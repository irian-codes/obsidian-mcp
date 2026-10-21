"""Synthetic guide configuration, complete-file assembly and real MCP boundary."""
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

import src.mcp_server.tools as tools
from src.auth.session import current_user_id, current_vault_root
from src.config import Settings
from src.mcp_server.server import mcp
from src.services import vault as vault_service
from src.services.tool_outcomes import BodyOutcome
from src.vault_guide_config import VaultGuideConfig, parse_guide_config


MAP = {
    "base": {"files": ["AGENTS.md", "Guides/MCP.md"]},
    "topics": {
        "editing": {"files": ["Guides/Editing.md", "Guides/Syntax.md", "AGENTS.md"]},
        "attachments": {
            "files": ["Guides/Attachments.md"],
            "clients": {"chatgpt-chat": {"files": ["Guides/Chat.md", "Guides/MCP.md"]}},
        },
        "syntax": {"files": ["Guides/Syntax.md"]},
    },
}
CONTENTS = {
    "AGENTS.md": "---\npolicy: complete\n---\nROOT-INSTRUCTIONS\n[[unmapped.md]]\n",
    "Guides/MCP.md": "COMMON-MCP\n",
    "Guides/Editing.md": "EDITING-ONLY\n",
    "Guides/Syntax.md": "SYNTAX-ONLY\n",
    "Guides/Attachments.md": "ATTACHMENTS-COMMON\n",
    "Guides/Chat.md": "CHAT-ATTACHMENTS-ONLY\n",
}


@pytest.fixture
def vault(monkeypatch, tmp_path):
    rows = []
    async def log(tool, params, *args):
        rows.append((tool, params))
    monkeypatch.setattr(tools, "_log_usage", log)
    monkeypatch.setattr(tools.settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(tools.settings, "multi_user_mode", False)
    monkeypatch.setattr(tools.settings, "vault_guide_config", VaultGuideConfig.model_validate(MAP))
    uid = current_user_id.set(None)
    root = current_vault_root.set(None)
    for path, content in CONTENTS.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    # A legacy file and a linked file must never enter output implicitly.
    (tmp_path / "CLAUDE.md").write_text("LEGACY-IMPLICIT")
    (tmp_path / "unmapped.md").write_text("UNMAPPED-LINK")
    yield tmp_path, rows
    current_vault_root.reset(root)
    current_user_id.reset(uid)


def assert_refused(value, code="validation_failed"):
    assert isinstance(value, BodyOutcome)
    assert value.refusal.code == code
    assert "## Source:" not in value
    assert "ROOT-INSTRUCTIONS" not in value


async def test_bootstrap_is_full_base_only(vault):
    value = await tools.get_vault_guide_impl("chatgpt-chat")
    assert not isinstance(value, BodyOutcome)
    for path in MAP["base"]["files"]:
        assert f"## Source: {path}\n\n{CONTENTS[path]}" in value
    assert "## Available topics" in value
    for text in ["CHAT-ATTACHMENTS-ONLY", "EDITING-ONLY", "LEGACY-IMPLICIT", "UNMAPPED-LINK", "Obsidian primer"]:
        assert text not in value


@pytest.mark.parametrize("client,topics,expected", [
    ("chatgpt-chat", ["editing"], ["AGENTS.md", "Guides/MCP.md", "Guides/Editing.md", "Guides/Syntax.md"]),
    ("codex", ["attachments"], ["AGENTS.md", "Guides/MCP.md", "Guides/Attachments.md"]),
    ("Chatgpt-chat", ["attachments"], ["AGENTS.md", "Guides/MCP.md", "Guides/Attachments.md"]),
    ("chatgpt-chat", ["attachments", "editing", "syntax", "editing"], ["AGENTS.md", "Guides/MCP.md", "Guides/Attachments.md", "Guides/Editing.md", "Guides/Syntax.md", "Guides/Chat.md"]),
])
async def test_topic_and_client_selection_deduplicates_in_order(vault, client, topics, expected):
    value = await tools.get_vault_guide_impl(client, topics)
    sources = [line.removeprefix("## Source: ") for line in value.splitlines() if line.startswith("## Source: ")]
    assert sources == expected
    assert ("CHAT-ATTACHMENTS-ONLY" in value) == ("Guides/Chat.md" in expected)


@pytest.mark.parametrize("client,topics", [("", []), ("white space", []), ("/path", []), ("codex\n", []), ("codex", None), ("codex", [""]), ("codex", ["unknown"]), ("codex", ["Editing"])])
async def test_bad_selectors_refuse(vault, client, topics):
    assert_refused(await tools.get_vault_guide_impl(client, topics), "invalid_argument")


async def test_missing_config_refuses_and_logs_body_outcome(vault, monkeypatch):
    monkeypatch.setattr(tools.settings, "vault_guide_config", None)
    value = await tools.get_vault_guide_impl("codex")
    assert_refused(value)
    assert "VAULT_GUIDE_CONFIG" in value
    assert len(vault[1]) == 1
    assert vault[1][0][1]["error"] == "validation_failed"
    assert vault[1][0][1]["body_outcome"] == "refused"


@pytest.mark.parametrize("defect", ["missing", "empty", "whitespace", "invalid-utf8", "directory", "byte-cap", "unreadable", "symlink-escape", "symlink-hidden"])
async def test_required_file_defects_return_no_partial_guide(vault, monkeypatch, defect, tmp_path):
    path = vault[0] / "Guides/MCP.md"
    path.unlink()
    if defect == "empty": path.write_bytes(b"")
    elif defect == "whitespace": path.write_bytes(b" \n\t")
    elif defect == "invalid-utf8": path.write_bytes(b"\xff")
    elif defect == "directory": path.mkdir()
    elif defect == "byte-cap":
        path.write_bytes(b"x" * 100)
        monkeypatch.setattr(tools.settings, "max_file_read_bytes", 80)
    elif defect == "unreadable":
        original = vault_service.read_bytes
        def deny(name, **kwargs):
            if name == "Guides/MCP.md": raise PermissionError("private host details")
            return original(name, **kwargs)
        monkeypatch.setattr(vault_service, "read_bytes", deny)
    elif defect == "symlink-escape":
        outside = tmp_path.parent / (tmp_path.name + "-outside.md")
        outside.write_text("OUTSIDE-SECRET")
        path.symlink_to(outside)
    elif defect == "symlink-hidden":
        hidden = vault[0] / ".hidden"
        hidden.mkdir()
        (hidden / "secret.md").write_text("HIDDEN-SECRET")
        path.symlink_to(hidden / "secret.md")
    value = await tools.get_vault_guide_impl("codex")
    assert_refused(value)
    assert "Incomplete vault guide" in value
    assert "private host details" not in value
    assert "OUTSIDE-SECRET" not in value
    assert "HIDDEN-SECRET" not in value


async def test_aggregate_cap_refuses_whole_guide_including_labels_and_catalog(vault, monkeypatch):
    good = await tools.get_vault_guide_impl("codex")
    monkeypatch.setattr(tools.settings, "max_read_response_chars", len(good))
    assert await tools.get_vault_guide_impl("codex") == good
    monkeypatch.setattr(tools.settings, "max_read_response_chars", len(good) - 1)
    assert_refused(await tools.get_vault_guide_impl("codex"), "size_limit")
    monkeypatch.setattr(tools.settings, "max_read_response_chars", 1)
    assert_refused(await tools.get_vault_guide_impl("codex"), "size_limit")


@pytest.mark.parametrize("raw", [
    "", "{", "null", "[]", "true", '{}',
    '{"base":{"files":[]}}',
    '{"base":{"files":["AGENTS.md"]},"base":{"files":["Other.md"]}}',
    '{"base":{"files":["AGENTS.md"],"unknown":true}}',
    '{"base":{"files":["AGENTS.md"]},"unknown":{}}',
    '{"base":{"files":[1]}}',
    '{"base":{"files":"AGENTS.md"}}',
    '{"base":{"files":["AGENTS.md"]},"topics":{"bad name":{"files":[]}}}',
    '{"base":{"files":["AGENTS.md"]},"topics":{"editing":{"files":[],"clients":{"bad/name":{"files":[]}}}}}',
    '{"base":{"files":["AGENTS.md"]},"topics":{"editing":{"files":[],"clients":{"codex":{"files":[],"extra":true}}}}}',
])
def test_bad_maps_refused_at_settings_boundary(raw):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, secret_key="synthetic-guide-secret", vault_guide_config=raw)
    assert "vault_guide_config" in str(error.value)
    assert "input_value=" not in str(error.value)


@pytest.mark.parametrize("path", ["/tmp/file", "../secret.md", "a/../secret.md", ".hidden.md", "a/.hidden/file", "a//b", "a/./b", "a/", "", "a\\b", "a\n.md", "a\x00.md", "a\u0085.md", "a\u2028.md", "\ud800", "x" * 1025])
def test_unsafe_map_paths_refused(path):
    with pytest.raises(ValidationError):
        parse_guide_config(json.dumps({"base": {"files": [path]}}))


def test_environment_json_setting_and_existing_path_cap(monkeypatch):
    monkeypatch.setenv("VAULT_GUIDE_CONFIG", json.dumps(MAP))
    config = Settings(_env_file=None, secret_key="synthetic-guide-secret").vault_guide_config
    assert config.base.files == MAP["base"]["files"]
    assert vault_service.MAX_PATH_CHARS == 1024
    monkeypatch.delenv("VAULT_GUIDE_CONFIG")
    assert Settings(_env_file=None, secret_key="synthetic-guide-secret").vault_guide_config is None


def text(result):
    assert result.isError is not True
    return "\n".join(block.text for block in result.content if block.type == "text")


async def test_protocol_requires_client_and_rejects_invalid_schema_before_body(vault):
    async with create_connected_server_and_client_session(mcp) as session:
        listed = {tool.name: tool for tool in (await session.list_tools()).tools}
        schema = listed["get_vault_guide"].inputSchema
        assert schema["required"] == ["client"]
        assert schema["properties"]["topics"]["default"] == []
        assert "topic AND client" in listed["get_vault_guide"].description
        for args in [{}, {"client": None}, {"client": 123}, {"client": "codex", "topics": None}, {"client": "codex", "topics": [2]}, {"client": "codex", "path": "AGENTS.md"}]:
            result = await session.call_tool("get_vault_guide", args)
            assert result.isError is True
        assert vault[1] == []
        assert "ROOT-INSTRUCTIONS" in text(await session.call_tool("get_vault_guide", {"client": "codex"}))


async def test_protocol_fresh_contents_and_selected_tenant_isolation(vault, monkeypatch):
    monkeypatch.setattr(tools.settings, "multi_user_mode", True)
    a, b = vault[0] / "A", vault[0] / "B"
    a.mkdir()
    b.mkdir()
    for root, marker in [(a, "TENANT-A"), (b, "TENANT-B")]:
        (root / "AGENTS.md").write_text(marker)
    monkeypatch.setattr(tools.settings, "vault_guide_config", parse_guide_config('{"base":{"files":["AGENTS.md"]}}'))
    for uid, root, own, other in [(11, a, "TENANT-A", "TENANT-B"), (22, b, "TENANT-B", "TENANT-A")]:
        user_token = current_user_id.set(uid)
        root_token = current_vault_root.set((uid, root))
        try:
            async with create_connected_server_and_client_session(mcp) as session:
                before = text(await session.call_tool("get_vault_guide", {"client": "codex"}))
                assert own in before and other not in before
                (root / "AGENTS.md").write_text(own + "-FRESH")
                after = text(await session.call_tool("get_vault_guide", {"client": "codex"}))
                assert own + "-FRESH" in after and other not in after
        finally:
            current_vault_root.reset(root_token)
            current_user_id.reset(user_token)


async def test_full_text_line_endings_and_safe_in_vault_symlink(vault, monkeypatch):
    source = vault[0] / "Canonical.md"
    raw = b"---\r\nfull: yes\r\n---\r\nCOMPLETE\r\n"
    source.write_bytes(raw)
    (vault[0] / "Alias.md").symlink_to(source)
    monkeypatch.setattr(
        tools.settings, "vault_guide_config",
        parse_guide_config('{"base":{"files":["Alias.md"]}}'),
    )
    value = await tools.get_vault_guide_impl("codex")
    assert "## Source: Alias.md\n\n" + raw.decode("utf-8") in value
