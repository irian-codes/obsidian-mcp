"""Synthetic folder cleanup: real rmdir safety, protocol and honest progress."""
import errno
import os
from unittest.mock import AsyncMock

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

import src.mcp_server.tools as tools
from src.auth.session import current_user_id
from src.mcp_server.auth import current_permission
from src.mcp_server.server import mcp
from src.services import empty_folders, vault, vault_fs
from src.services.tool_outcomes import BodyOutcome, body_refusal


@pytest.fixture
def tree(monkeypatch, tmp_path):
    monkeypatch.setattr(tools.settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(tools.settings, "multi_user_mode", False)
    # Even deployments requiring file hashes can safely remove directories.
    monkeypatch.setattr(tools.settings, "write_precondition_required", True)
    monkeypatch.setattr(tools, "_log_usage", AsyncMock())
    uid = current_user_id.set(None)
    permission = current_permission.set("readwrite")
    vault.clear_user_vault_cache()
    try:
        yield tmp_path
    finally:
        current_user_id.reset(uid)
        current_permission.reset(permission)
        vault.clear_user_vault_cache()


async def test_removes_nested_empty_tree_and_retry_is_a_noop(tree):
    (tree / "Parent/Old notes/One/Two").mkdir(parents=True)
    (tree / "Parent/Old notes/Other").mkdir()
    sibling = tree / "Parent/keep.md"
    sibling.write_bytes(b"Keep this note unchanged.\n")
    result = await tools.delete_empty_folder_tree_impl("Parent/Old notes")
    assert type(result) is str and "4 empty directories" in result
    assert not (tree / "Parent/Old notes").exists()
    assert sibling.read_bytes() == b"Keep this note unchanged.\n"
    assert sorted(p.name for p in tree.iterdir()) == ["Parent"]
    again = await tools.delete_empty_folder_tree_impl("Parent/Old notes")
    assert type(again) is str and "already absent" in again
    assert sibling.read_bytes() == b"Keep this note unchanged.\n"


@pytest.mark.parametrize("entry", ["note.md", "image.png", ".keep"])
async def test_nonempty_tree_refuses_before_any_removal(tree, entry):
    (tree / "Old/Empty/Nested").mkdir(parents=True)
    (tree / "Old/Other").mkdir()
    file = tree / "Old/Other" / entry
    file.write_bytes(b"never remove these bytes")
    result = await tools.delete_empty_folder_tree_impl("Old")
    assert isinstance(result, BodyOutcome) and result.disposition == "refused"
    assert "Removed 0 directories" in result
    assert (tree / "Old/Empty/Nested").is_dir()
    assert file.read_bytes() == b"never remove these bytes"
    assert not (tree / ".trash").exists()


@pytest.mark.parametrize("relative", ["", ".", "Old/..", "../outside", "/", ".obsidian"])
async def test_root_traversal_and_hidden_paths_are_refused(tree, relative):
    (tree / "Old").mkdir()
    (tree / ".obsidian").mkdir()
    result = await tools.delete_empty_folder_tree_impl(relative)
    assert isinstance(result, BodyOutcome) and result.disposition == "refused"
    assert (tree / "Old").is_dir() and (tree / ".obsidian").is_dir()


async def test_hidden_directory_and_symlinks_are_never_followed(tree, tmp_path):
    (tree / "Old/.private").mkdir(parents=True)
    result = await tools.delete_empty_folder_tree_impl("Old")
    assert isinstance(result, BodyOutcome)
    assert (tree / "Old/.private").is_dir()
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    try:
        (outside / "content.md").write_bytes(b"outside")
        (tree / "Alias").symlink_to(outside, target_is_directory=True)
        (tree / "Old/link").symlink_to(outside, target_is_directory=True)
        for selected in ("Alias", "Alias/child", "Old/link"):
            result = await tools.delete_empty_folder_tree_impl(selected)
            assert isinstance(result, BodyOutcome)
        # An otherwise ordinary tree containing a symlink also refuses.
        (tree / "WithLink").mkdir()
        (tree / "WithLink/link").symlink_to(outside, target_is_directory=True)
        assert isinstance(await tools.delete_empty_folder_tree_impl("WithLink"), BodyOutcome)
        assert (tree / "WithLink/link").is_symlink()
        assert (outside / "content.md").read_bytes() == b"outside"
    finally:
        (outside / "content.md").unlink()
        outside.rmdir()


async def test_unreadable_preflight_leaves_even_empty_siblings(tree, monkeypatch):
    (tree / "Old/Empty").mkdir(parents=True)
    (tree / "Old/Unreadable").mkdir()
    real_open = vault_fs.open_dir_beneath

    def fail(root_fd, relative, **kwargs):
        if relative == "Old/Unreadable":
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_open(root_fd, relative, **kwargs)

    monkeypatch.setattr(vault_fs, "open_dir_beneath", fail)
    result = await tools.delete_empty_folder_tree_impl("Old")
    assert result.disposition == "refused" and result.refusal.code == "io_failure"
    assert (tree / "Old/Empty").is_dir() and (tree / "Old/Unreadable").is_dir()


async def test_realistic_wide_tree_at_cap_succeeds_and_above_cap_refuses(tree):
    # Assumption: cleanup normally covers modest organizational trees. Exercise
    # the full public 1,000-directory bound so width cannot bypass preflight.
    root = tree / "Old"
    root.mkdir()
    for i in range(1000):
        (root / str(i)).mkdir()
    refused = await tools.delete_empty_folder_tree_impl("Old")
    assert refused.refusal.code == "size_limit" and refused.disposition == "refused"
    assert len(list(root.iterdir())) == 1000
    (root / "999").rmdir()
    done = await tools.delete_empty_folder_tree_impl("Old")
    assert type(done) is str and "1000 empty directories" in done
    assert not root.exists()


async def test_file_arriving_at_rmdir_survives_with_partial_progress_and_retry(tree, monkeypatch):
    (tree / "Old/A").mkdir(parents=True)
    (tree / "Old/B").mkdir()
    real_rmdir = os.rmdir
    injected = False

    def late_file(name, *, dir_fd=None):
        nonlocal injected
        if name == "Old" and dir_fd is not None and not injected:
            injected = True
            (tree / "Old/arrived.md").write_bytes(b"fresh sync content")
        return real_rmdir(name, dir_fd=dir_fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "rmdir", late_file)
        result = await tools.delete_empty_folder_tree_impl("Old")
    assert result.disposition == "partial" and "Removed 2 directories" in result
    assert result.refusal.nothing_written is None
    assert (tree / "Old/arrived.md").read_bytes() == b"fresh sync content"
    assert not (tree / "Old/A").exists() and not (tree / "Old/B").exists()
    assert tools._log_usage.call_args.args[1]["body_outcome"] == "partial"
    again = await tools.delete_empty_folder_tree_impl("Old")
    assert again.disposition == "refused" and "Removed 0 directories" in again
    assert (tree / "Old/arrived.md").read_bytes() == b"fresh sync content"
    # Simulate the human moving their new content out of the selected tree.
    (tree / "Old/arrived.md").rename(tree / "arrived.md")
    done = await tools.delete_empty_folder_tree_impl("Old")
    assert type(done) is str and not (tree / "Old").exists()
    assert (tree / "arrived.md").read_bytes() == b"fresh sync content"


async def test_preflight_bug_still_cannot_delete_files(tree, monkeypatch):
    # Deliberately lie about emptiness: kernel rmdir, not the planner, is the
    # final protection against content loss.
    (tree / "Old").mkdir()
    (tree / "Old/note.md").write_bytes(b"must survive a bad emptiness check")
    info = (tree / "Old").stat()
    monkeypatch.setattr(empty_folders, "plan_empty_tree", lambda *_: [
        empty_folders.Directory("Old", info.st_dev, info.st_ino),
    ])
    result = await tools.delete_empty_folder_tree_impl("Old")
    assert result.disposition == "refused"
    assert (tree / "Old/note.md").read_bytes() == b"must survive a bad emptiness check"


async def test_replaced_directory_is_preserved(tree, monkeypatch):
    (tree / "Old/A").mkdir(parents=True)
    original = empty_folders.plan_empty_tree

    def replace_after_plan(*args):
        plan = original(*args)
        (tree / "Old/A").rename(tree / "original-A")
        (tree / "Old/A").mkdir()
        return plan

    monkeypatch.setattr(empty_folders, "plan_empty_tree", replace_after_plan)
    result = await tools.delete_empty_folder_tree_impl("Old")
    assert result.refusal.code == "concurrent_write" and result.disposition == "refused"
    assert (tree / "Old/A").is_dir() and (tree / "original-A").is_dir()


async def test_symlink_substitution_of_parent_never_reaches_another_tree(tree, monkeypatch):
    (tree / "Old/Child").mkdir(parents=True)
    (tree / "Other/Child").mkdir(parents=True)
    original = empty_folders.plan_empty_tree

    def redirect_after_plan(*args):
        plan = original(*args)
        (tree / "Old").rename(tree / "original-Old")
        (tree / "Old").symlink_to(tree / "Other", target_is_directory=True)
        return plan

    monkeypatch.setattr(empty_folders, "plan_empty_tree", redirect_after_plan)
    result = await tools.delete_empty_folder_tree_impl("Old")
    assert result.disposition == "refused" and result.refusal.code == "unsafe_path"
    assert (tree / "Other/Child").is_dir() and (tree / "original-Old/Child").is_dir()
    assert (tree / "Old").is_symlink()


async def test_reassignment_between_removals_stops_and_preserves_partial_outcome(tree, monkeypatch):
    (tree / "Old/A").mkdir(parents=True)
    confirmed = tools._confirmed_publication
    calls = 0

    async def reassign(uid, publish):
        nonlocal calls
        calls += 1
        if calls == 2:
            return body_refusal("Vault assignment changed.", "vault_assignment_changed"), None
        return await confirmed(uid, publish)

    monkeypatch.setattr(tools, "_confirmed_publication", reassign)
    result = await tools.delete_empty_folder_tree_impl("Old")
    assert result.disposition == "partial" and result.refusal.code == "vault_assignment_changed"
    assert "Removed 1 directories" in result and calls == 2
    assert (tree / "Old").is_dir() and not (tree / "Old/A").exists()


async def test_confirmation_outage_after_removal_reports_partial_work(tree, monkeypatch):
    (tree / "Old/A").mkdir(parents=True)
    confirmed = tools._confirmed_publication
    calls = 0

    async def outage(uid, publish):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Database unavailable")
        return await confirmed(uid, publish)

    monkeypatch.setattr(tools, "_confirmed_publication", outage)
    result = await tools.delete_empty_folder_tree_impl("Old")
    assert result.disposition == "partial" and result.refusal.code == "partial_completion"
    assert (tree / "Old").is_dir() and not (tree / "Old/A").exists()


async def test_protocol_schema_permission_and_actual_cleanup(tree):
    (tree / "Old/Empty").mkdir(parents=True)
    async with create_connected_server_and_client_session(mcp) as client:
        schema = {tool.name: tool for tool in (await client.list_tools()).tools}["delete_empty_folder_tree"]
        assert set(schema.inputSchema["properties"]) == {"path"}
        assert schema.inputSchema["required"] == ["path"]
        assert schema.inputSchema["additionalProperties"] is False
        assert schema.annotations.readOnlyHint is False
        assert schema.annotations.destructiveHint is True
        assert schema.annotations.openWorldHint is False
        permission = current_permission.set("read")
        try:
            # The session's server task inherits context at session creation;
            # use a separate session to exercise read-only caller context.
            async with create_connected_server_and_client_session(mcp) as reader:
                denied = await reader.call_tool("delete_empty_folder_tree", {"path": "Old"})
                assert "Permission denied" in denied.content[0].text
        finally:
            current_permission.reset(permission)
        assert (tree / "Old/Empty").is_dir()
        bad = await client.call_tool("delete_empty_folder_tree", {"path": "Old", "permanent": True})
        assert bad.isError is True and (tree / "Old/Empty").is_dir()
        deleted = await client.call_tool("delete_empty_folder_tree", {"path": "Old"})
        assert not deleted.isError and "2 empty directories" in deleted.content[0].text
        assert not (tree / "Old").exists()
