"""Native image imports: verified bytes, locked identity, atomic no-clobber."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import importlib.util
import json
import logging
from pathlib import Path

import httpx
import pytest

from src.config import settings
from src.mcp_server import tools
from src.services import chatgpt_file_probe as native, transfer, vault_fs
from tests.test_chatgpt_file_probe import FILE, SECRET_URL, PNG
from tests.test_transfer_tools import vault, readwrite, readonly, usage_log, minted, credential, _api_key

JPEG = b"\xff\xd8\xff\xe0" + b"synthetic image bytes" * 30000


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def image_fetch(monkeypatch):
    state = {"payload": JPEG, "calls": [], "before_close": None, "error": None, "closed": False}
    @asynccontextmanager
    async def fetch(url, **kw):
        state["calls"].append((url, kw))
        async def chunks():
            if state["error"]:
                raise state["error"]
            for offset in range(0, len(state["payload"]), 65536):
                yield state["payload"][offset:offset + 65536]
            if state["before_close"]:
                state["before_close"]()
        try:
            yield transfer.FetchResult(chunks(), SECRET_URL, "image/png")
        finally:
            state["closed"] = True
    monkeypatch.setattr(transfer, "fetch_url_guarded", fetch)
    return state


@pytest.fixture
def identity_gate(monkeypatch, credential):
    # Exercise real identity predicate and real lock context, with fake DB rows.
    state = {"held": False, "statements": []}
    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            pass
        @asynccontextmanager
        async def begin(self):
            state["held"] = True
            try:
                yield
            finally:
                state["held"] = False
        async def execute(self, statement):
            state["statements"].append(str(statement))
            return type("Result", (), {"scalar_one_or_none": lambda _: credential["row"]})()
    monkeypatch.setattr(tools, "async_session", Session)
    return state


async def test_exact_jpeg_source_above_advisory_size_no_origin(vault, readwrite, usage_log, image_fetch, identity_gate, monkeypatch):
    monkeypatch.setattr(settings, "_public_origin_explicit", False)
    real_publish = vault_fs.publish
    def publish(*a, **kw):
        assert image_fetch["closed"] and identity_gate["held"]
        assert kw["overwrite"] is False
        return real_publish(*a, **kw)
    monkeypatch.setattr(vault_fs, "publish", publish)
    result = await tools.import_chatgpt_image_impl(FILE, "Attachments/photo.jpg", sha(JPEG))
    report = json.loads(result)
    assert (vault / "Attachments/photo.jpg").read_bytes() == JPEG
    assert report == {"status": "imported", "path": "Attachments/photo.jpg", "size_bytes": len(JPEG), "sha256": sha(JPEG), "detected_format": "jpeg", "canonical_extension": ".jpg", "mime": "image/jpeg"}
    assert len(JPEG) > 512 * 1024
    assert all("FOR UPDATE" in sql for sql in identity_gate["statements"])
    assert image_fetch["calls"][0][1] == {"allow_http": False, "max_bytes": settings.max_file_write_bytes, "deadline": 30.0}
    assert "secret" not in result + json.dumps(usage_log)
    assert usage_log[-1]["tool"] == "import_chatgpt_image"
    assert not {"file", "path", "download_url", "file_id", "expected_sha256"} & usage_log[-1]["params"].keys()


async def test_exact_png_and_hex_case(vault, readwrite, image_fetch, identity_gate):
    image_fetch["payload"] = PNG
    result = await tools.import_chatgpt_image_impl(FILE, "Attachments/new.png", sha(PNG).upper())
    assert json.loads(result)["sha256"] == sha(PNG)
    assert (vault / "Attachments/new.png").read_bytes() == PNG


async def test_readonly_refused_before_fetch_or_probe(vault, readonly, image_fetch, monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("must not probe publication")
    monkeypatch.setattr(vault_fs, "check_publication_support", forbidden)
    result = await tools.import_chatgpt_image_impl(FILE, "Attachments/new.jpg", sha(JPEG))
    assert result.marker == "permission_denied" and image_fetch["calls"] == []
    assert list(vault.rglob("*.jpg")) == []


@pytest.mark.parametrize("checksum", ["", "secret", "sha256:" + "0" * 64, "0" * 63, "g" * 64])
async def test_bad_source_checksum_no_fetch(vault, readwrite, image_fetch, checksum):
    result = await tools.import_chatgpt_image_impl(FILE, "Attachments/new.jpg", checksum)
    assert result.marker == "invalid_argument" and image_fetch["calls"] == []
    assert "secret" not in result


@pytest.mark.parametrize("payload,path,checksum,reason", [
    (JPEG, "Attachments/wrong.png", sha(JPEG), "extension_mismatch"),
    (JPEG, "Attachments/hash.jpg", "0" * 64, "checksum_mismatch"),
    (b"PK\x03\x04unsupported", "Attachments/no.jpg", sha(b"PK\x03\x04unsupported"), "unsupported_image_format"),
    (b"plain text", "Attachments/no.png", sha(b"plain text"), "unsupported_image_format"),
])
async def test_validation_before_any_staging(vault, readwrite, image_fetch, monkeypatch, payload, path, checksum, reason):
    image_fetch["payload"] = payload
    def forbidden(*a, **kw):
        raise AssertionError("unverified bytes must not be staged")
    monkeypatch.setattr(transfer, "stream_to_vault", forbidden)
    result = await tools.import_chatgpt_image_impl(FILE, path, checksum)
    report = json.loads(result.prose)
    assert report["reason"] == reason
    if reason == "extension_mismatch":
        assert report["detected_format"] == "jpeg" and report["canonical_extension"] == ".jpg"
    assert not (vault / path).exists()
    assert not (vault / ".transfer-tmp").exists()


@pytest.mark.parametrize("path", ["../outside.jpg", "/outside.jpg", ".private/a.jpg", "Attachments/.hidden.jpg", "Attachments/note.md", "https://secret.example/a.jpg"])
async def test_path_guards_no_fetch(vault, readwrite, image_fetch, path):
    result = await tools.import_chatgpt_image_impl(FILE, path, sha(JPEG))
    assert result.marker in {"unsafe_path", "validation_failed"}
    assert image_fetch["calls"] == []
    assert "secret" not in result


async def test_leaf_and_ancestor_symlinks_refuse(vault, readwrite, image_fetch):
    (vault / "target.jpg").write_bytes(b"incumbent")
    (vault / "Attachments/alias.jpg").symlink_to(vault / "target.jpg")
    (vault / "aliasdir").symlink_to(vault / "Attachments", target_is_directory=True)
    for path in ("Attachments/alias.jpg", "aliasdir/new.jpg"):
        result = await tools.import_chatgpt_image_impl(FILE, path, sha(JPEG))
        assert result.marker in {"unsafe_path", "transfer_unavailable"}
    assert image_fetch["calls"] == []
    assert (vault / "target.jpg").read_bytes() == b"incumbent"


async def test_existing_target_and_repeat_refuse_before_fetch(vault, readwrite, image_fetch, identity_gate):
    first = await tools.import_chatgpt_image_impl(FILE, "Attachments/once.jpg", sha(JPEG))
    assert json.loads(first)["status"] == "imported"
    second = await tools.import_chatgpt_image_impl(FILE, "Attachments/once.jpg", sha(JPEG))
    assert second.marker == "already_exists" and len(image_fetch["calls"]) == 1
    assert (vault / "Attachments/once.jpg").read_bytes() == JPEG


async def test_target_created_during_fetch_never_clobbered(vault, readwrite, image_fetch, identity_gate):
    target = vault / "Attachments/race.jpg"
    image_fetch["before_close"] = lambda: target.write_bytes(b"concurrent incumbent")
    result = await tools.import_chatgpt_image_impl(FILE, "Attachments/race.jpg", sha(JPEG))
    assert result.marker == "concurrent_write"
    assert target.read_bytes() == b"concurrent incumbent"
    assert list((vault / ".transfer-tmp").iterdir()) == []


@pytest.mark.parametrize("change", ["revoked", "downgraded", "root_reassigned"])
async def test_real_identity_gate_revalidates_after_fetch(vault, readwrite, image_fetch, identity_gate, credential, monkeypatch, tmp_path, change):
    def revoke():
        if change == "revoked":
            credential["row"].is_active = False
        elif change == "downgraded":
            credential["row"].permission = "read"
        else:
            monkeypatch.setattr(settings, "vault_path", str(tmp_path / "different"))
    image_fetch["before_close"] = revoke
    result = await tools.import_chatgpt_image_impl(FILE, "Attachments/no.jpg", sha(JPEG))
    assert result.marker == "credential_unusable"
    assert not (vault / "Attachments/no.jpg").exists()
    assert identity_gate["statements"]
    assert list((vault / ".transfer-tmp").iterdir()) == []


@pytest.mark.parametrize("named", [False, True])
async def test_cancel_during_staging_cleans_and_propagates(vault, readwrite, image_fetch, identity_gate, monkeypatch, named):
    if named:
        monkeypatch.setattr(vault_fs, "check_publication_support", lambda *a, **kw: vault_fs.STAGING_MODE_NAMED)
    async def cancel(fd):
        raise asyncio.CancelledError
    monkeypatch.setattr(transfer, "_flush_staged_payload", cancel)
    with pytest.raises(asyncio.CancelledError):
        await tools.import_chatgpt_image_impl(FILE, "Attachments/cancel.jpg", sha(JPEG))
    assert not (vault / "Attachments/cancel.jpg").exists()
    assert list((vault / ".transfer-tmp").iterdir()) == []
    assert native._sensitive_fetch.get() is False


@pytest.mark.parametrize("cancelled", [False, True])
async def test_completion_failure_reports_partial_and_keeps_published_bytes(vault, readwrite, image_fetch, monkeypatch, cancelled):
    @asynccontextmanager
    async def gate(*a, **kw):
        async def fail(*a, **kw):
            if cancelled:
                raise asyncio.CancelledError
            raise RuntimeError(SECRET_URL)
        yield transfer.GateHandle(ok=True, on_complete=fail)
    monkeypatch.setattr(transfer, "lock_identity_for_publish", gate)
    monkeypatch.setattr(tools, "async_session", lambda: type("S", (), {"__aenter__": lambda s: _return(s), "__aexit__": lambda s, *a: _return(False)})())
    result = await tools.import_chatgpt_image_impl(FILE, "Attachments/partial.jpg", sha(JPEG))
    assert result.marker == "partial_completion" and result.disposition == "partial"
    assert (vault / "Attachments/partial.jpg").read_bytes() == JPEG
    assert json.loads(result.prose)["action"] == "inspect_destination_before_retry"
    assert "secret" not in result


async def _return(value):
    return value


async def test_counted_size_cap_and_network_errors_redacted(vault, readwrite, image_fetch, monkeypatch, caplog):
    monkeypatch.setattr(settings, "max_file_write_bytes", 100)
    result = await tools.import_chatgpt_image_impl(FILE, "Attachments/large.jpg", sha(JPEG))
    assert result.marker == "size_limit" and not (vault / "Attachments/large.jpg").exists()
    image_fetch["error"] = RuntimeError(SECRET_URL)
    with caplog.at_level(logging.DEBUG):
        result = await tools.import_chatgpt_image_impl(FILE, "Attachments/fail.jpg", sha(JPEG))
    assert "secret" not in result + caplog.text
    assert result.marker == "fetch_refused"


@pytest.mark.parametrize("probe_enabled,import_enabled", [(False, False), (False, True), (True, False), (True, True)])
async def test_flags_schema_metadata_and_safe_validation(monkeypatch, probe_enabled, import_enabled):
    monkeypatch.setattr(settings, "chatgpt_file_probe_enabled", probe_enabled)
    monkeypatch.setattr(settings, "chatgpt_image_import_enabled", import_enabled)
    from src.mcp_server import native_file_privacy
    installed = []
    monkeypatch.setattr(native_file_privacy, "install_sdk_validation_redaction", lambda: installed.append(True))
    spec = importlib.util.spec_from_file_location("image_registration_test", Path(__file__).resolve().parents[1] / "src/mcp_server/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    listed = {tool.name: tool for tool in await module.mcp.list_tools()}
    assert ("import_chatgpt_image" in listed) is import_enabled
    assert ("inspect_chatgpt_file" in listed) is probe_enabled
    assert len(listed) == 25 + probe_enabled + import_enabled
    assert bool(installed) is (probe_enabled or import_enabled)
    if import_enabled:
        tool = listed["import_chatgpt_image"]
        assert tool.model_dump(by_alias=True)["_meta"] == {"openai/fileParams": ["file"]}
        assert tool.inputSchema["required"] == ["file", "path", "expected_sha256"]
        f = tool.inputSchema["properties"]["file"]
        assert f["required"] == ["download_url", "file_id"]
        assert set(f["properties"]) == {"download_url", "file_id", "mime_type", "file_name"}
        assert all(v["type"] == "string" for v in f["properties"].values())
        assert tool.annotations.model_dump(exclude_none=True) == {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
        for args in ({"file": {"download_url": [SECRET_URL], "file_id": "secret"}, "path": "x.jpg", "expected_sha256": "0"*64}, {"file": FILE, "path": "x.jpg", "expected_sha256": "0"*64, "overwrite": SECRET_URL}, {"file": FILE, "path": "x.jpg", "expected_sha256": "0"*64, SECRET_URL: "anything"}):
            with pytest.raises(Exception) as error:
                await module.mcp.call_tool("import_chatgpt_image", args)
            assert "secret" not in str(error.value)


@pytest.mark.parametrize("redirect", [False, True])
async def test_real_guard_blocks_private_or_redirect_without_publication(vault, readwrite, monkeypatch, redirect):
    async def resolve(*a):
        return ["8.8.8.8"]
    requests = []
    def transport(**kw):
        def response(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://127.0.0.1/secret"})
        return httpx.MockTransport(response)
    monkeypatch.setattr(transfer, "default_resolver", resolve)
    monkeypatch.setattr(transfer, "PinnedTransport", transport)
    file = FILE if redirect else {**FILE, "download_url": "https://127.0.0.1/secret"}
    result = await tools.import_chatgpt_image_impl(file, "Attachments/refused.jpg", sha(JPEG))
    assert result.marker == "fetch_refused" and "secret" not in result
    assert not (vault / "Attachments/refused.jpg").exists()
    assert len(requests) == int(redirect)


@pytest.mark.parametrize("probe_enabled,import_enabled", [(False, False), (True, False), (False, True), (True, True)])
async def test_main_http_privacy_guard_for_either_flag(monkeypatch, probe_enabled, import_enabled):
    # This test exercises the HTTP privacy boundary, not process logging setup.
    # Isolate the first main import so it cannot change neighboring captures.
    from src import logging_setup
    configure = logging_setup.configure_logging
    with monkeypatch.context() as isolated:
        isolated.setattr(logging_setup, "configure_logging", lambda: None)
        import src.main as main
    main.configure_logging = configure
    monkeypatch.setattr(settings, "chatgpt_file_probe_enabled", probe_enabled)
    monkeypatch.setattr(settings, "chatgpt_image_import_enabled", import_enabled)
    async def sdk(scope, receive, send):
        await send({"type": "http.response.start", "status": 400, "headers": [(b"x-security", b"kept")]})
        await send({"type": "http.response.body", "body": b'{"error":{"message":"Validation error: secret-file-id"}}'})
    monkeypatch.setattr(main.mcp.session_manager, "handle_request", sdk)
    messages = []
    async def send(message):
        messages.append(message)
    await main.mcp_handler({"type": "http"}, None, send)
    assert messages[0]["status"] == 400 and dict(messages[0]["headers"])[b"x-security"] == b"kept"
    assert (b"secret" not in messages[1]["body"]) is (probe_enabled or import_enabled)


@pytest.mark.parametrize("error,code", [
    (httpx.ReadTimeout(SECRET_URL), "transfer_timeout"),
    (transfer.Timeout(SECRET_URL), "transfer_timeout"),
    (transfer.SSRFError(SECRET_URL, reason=transfer.FetchFailureReason.HTTP_STATUS, http_status=403), "fetch_refused"),
])
async def test_bounded_fetch_failures_leave_no_staging(vault, readwrite, image_fetch, error, code):
    image_fetch["error"] = error
    result = await tools.import_chatgpt_image_impl(FILE, "Attachments/fail.jpg", sha(JPEG))
    assert result.marker == code and "secret" not in result
    assert image_fetch["closed"] and native._sensitive_fetch.get() is False
    assert not (vault / "Attachments/fail.jpg").exists()
    assert not (vault / ".transfer-tmp").exists()
