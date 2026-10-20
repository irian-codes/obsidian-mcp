"""Offline native file binding, exact bytes, egress refusal and secret hygiene."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import importlib.util
import io
import json
import logging
from pathlib import Path
import struct

import httpx
import pytest

from src.config import settings
from src.mcp_server import tools
from src.mcp_server import auth
from src.mcp_server.server import mcp as default_mcp
from src.services import chatgpt_file_probe as probe, transfer

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 255 + bytes(range(248))
SECRET_URL = "https://files.example.net/original?secret=credential"
FILE = {"download_url": SECRET_URL, "file_id": "secret-file-id", "file_name": "secret-name", "mime_type": "misleading/type"}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "chatgpt_file_probe_enabled", True)
    path = Path(__file__).resolve().parents[1] / "src/mcp_server/server.py"
    spec = importlib.util.spec_from_file_location("probe_test_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.mcp


@pytest.fixture
def usage(monkeypatch):
    rows = []
    async def capture(name, params, duration, size):
        rows.append((name, params))
    monkeypatch.setattr(tools, "_log_usage", capture)
    return rows


@pytest.fixture
def fetch(monkeypatch):
    state = {"payload": PNG, "calls": []}
    @asynccontextmanager
    async def fake(url, **kwargs):
        state["calls"].append((url, kwargs))
        async def chunks():
            for start in range(0, len(state["payload"]), 3000):
                yield state["payload"][start:start + 3000]
        yield transfer.FetchResult(chunks(), SECRET_URL, "image/jpeg")
    monkeypatch.setattr(transfer, "fetch_url_guarded", fake)
    return state


async def text_call(server, args):
    content = await server.call_tool("inspect_chatgpt_file", args)
    return content[0][0].text


async def test_tools_list_default_off_and_enabled_exact_native_schema(enabled):
    assert "inspect_chatgpt_file" not in {t.name for t in await default_mcp.list_tools()}
    tool = next(t for t in await enabled.list_tools() if t.name == "inspect_chatgpt_file")
    assert tool.model_dump(by_alias=True)["_meta"] == {"openai/fileParams": ["file"]}
    schema = tool.inputSchema
    assert schema["required"] == ["file"]
    f = schema["properties"]["file"]
    assert f["type"] == "object"
    assert f["required"] == ["download_url", "file_id"]
    assert set(f["properties"]) == {"download_url", "file_id", "mime_type", "file_name"}
    assert all(x["type"] == "string" for x in f["properties"].values())
    assert (tool.annotations.readOnlyHint, tool.annotations.destructiveHint,
            tool.annotations.idempotentHint, tool.annotations.openWorldHint) == (True, False, True, True)


async def test_png_original_bytes_tracked_readonly_no_writes(enabled, usage, fetch, monkeypatch):
    # Fail if diagnostic opens any vault/storage file, including temp staging.
    def forbidden(*a, **kw):
        raise AssertionError("filesystem operation")
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("os.open", forbidden)
    monkeypatch.setattr("os.write", forbidden)
    token = auth.current_permission.set("read")
    try:
        result = json.loads(await text_call(enabled, {"file": FILE}))
    finally:
        auth.current_permission.reset(token)
    assert result["size_bytes"] == 65536
    assert result["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert result["detected_format"] == "png"
    assert result["format_basis"] == "signature_only"
    assert result["signature_hex"] == PNG[:16].hex()
    assert result["elapsed_ms"] >= 0
    assert result["download_ms"] >= 0 and result["inspection_ms"] >= 0
    assert fetch["calls"][0][1] == {"allow_http": False, "max_bytes": probe.MAX_BYTES, "deadline": probe.FETCH_SECONDS}
    assert len(usage) == 1 and usage[0][0] == "inspect_chatgpt_file"
    assert "secret" not in json.dumps(usage)
    assert "secret" not in json.dumps(result)


def zip_bytes(payload=PNG):
    import zipfile
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("secret-name.png", payload)
    return stream.getvalue()


async def test_zip_original_and_member_hash(enabled, usage, fetch):
    fetch["payload"] = zip_bytes()
    result = json.loads(await text_call(enabled, {"file": FILE, "zip_member_index": 0}))
    assert result["sha256"] == hashlib.sha256(fetch["payload"]).hexdigest()
    assert result["zip"]["member_sha256"] == hashlib.sha256(PNG).hexdigest()
    assert result["zip"]["member_size_bytes"] == 65536
    assert result["zip"]["member_detected_format"] == "png"
    assert result["zip"]["member_signature_hex"] == PNG[:16].hex()
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("change", ["corrupt", "encrypted", "oversized", "bad_index", "many"])
async def test_zip_bounds(change, fetch):
    payload = bytearray(zip_bytes())
    index = 0
    cd = payload.index(b"PK\x01\x02")
    if change == "corrupt":
        payload[40] ^= 0xFF
    elif change == "encrypted":
        payload[cd+8] |= 1
    elif change == "oversized":
        struct.pack_into("<I", payload, cd+24, probe.MAX_BYTES + 1)
    elif change == "bad_index":
        index = 1
    elif change == "many":
        import zipfile
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            for i in range(65):
                z.writestr(str(i), b"")
        payload = stream.getvalue()
    fetch["payload"] = payload
    result = await probe.inspect_file(FILE, index)
    assert result.marker == "validation_failed"
    assert "secret" not in result


@pytest.mark.parametrize("file", [
    {"download_url": SECRET_URL},
    {"download_url": [SECRET_URL], "file_id": "secret-file-id"},
    {"download_url": SECRET_URL, "file_id": {"secret": "secret-file-id"}},
])
async def test_sdk_validation_redacts_native_values(enabled, usage, file):
    with pytest.raises(Exception) as error:
        await enabled.call_tool("inspect_chatgpt_file", {"file": file})
    assert "secret" not in str(error.value)
    assert "files.example" not in str(error.value)
    assert usage == []


async def test_sdk_unknown_argument_redacted(enabled):
    with pytest.raises(Exception) as error:
        await enabled.call_tool("inspect_chatgpt_file", {"file": FILE, "extra": SECRET_URL})
    assert "secret" not in str(error.value)
    assert "files.example" not in str(error.value)


async def test_stream_size_cap_without_content_length(fetch):
    fetch["payload"] = b"x" * (probe.MAX_BYTES + 1)
    result = await probe.inspect_file(FILE)
    assert result.marker == "fetch_refused"


@pytest.mark.parametrize("exc", [transfer.Timeout(SECRET_URL), httpx.ReadTimeout(SECRET_URL), RuntimeError(SECRET_URL)])
async def test_all_fetch_errors_redacted(monkeypatch, caplog, exc):
    @asynccontextmanager
    async def fail(*a, **kw):
        logging.getLogger("httpx").info(SECRET_URL)
        logging.getLogger("httpcore.http11").debug(SECRET_URL)
        raise exc
        yield
    monkeypatch.setattr(transfer, "fetch_url_guarded", fail)
    with (
        caplog.at_level(logging.DEBUG),
        caplog.at_level(logging.DEBUG, logger="httpx"),
        caplog.at_level(logging.DEBUG, logger="httpcore.http11"),
    ):
        result = await probe.inspect_file(FILE)
        # Filters must reset even on errors: unrelated request logging survives.
        logging.getLogger("httpx").info("ordinary-request")
    assert "secret" not in result + caplog.text
    assert "ordinary-request" in caplog.text


@pytest.mark.parametrize("url", ["http://files.example.net/a", "https://127.0.0.1/a", "https://169.254.169.254/a", "https://[::1]/a", "https://user:secret@example.net/a", "https://[bad/secret"])
async def test_invalid_or_private_url_no_connection(monkeypatch, url):
    def forbidden(**kw):
        raise AssertionError("must refuse before transport")
    monkeypatch.setattr(transfer, "PinnedTransport", forbidden)
    result = await probe.inspect_file({"download_url": url, "file_id": "secret"})
    assert result.marker == "fetch_refused"
    assert "secret" not in result


@pytest.mark.parametrize("answers", [["8.8.8.8", "127.0.0.1"], ["169.254.169.254"], ["::ffff:127.0.0.1"]])
async def test_dns_answers_all_checked(monkeypatch, answers):
    async def resolve(*a):
        return answers
    def forbidden(**kw):
        raise AssertionError("must refuse before transport")
    monkeypatch.setattr(transfer, "default_resolver", resolve)
    monkeypatch.setattr(transfer, "PinnedTransport", forbidden)
    assert (await probe.inspect_file(FILE)).marker == "fetch_refused"


async def test_redirect_to_private_refused_and_no_credentials_forwarded(monkeypatch):
    requests = []
    async def resolve(*a):
        return ["8.8.8.8"]
    def transport(**kwargs):
        assert kwargs["address"] == "8.8.8.8"
        def reply(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://127.0.0.1/secret"})
        return httpx.MockTransport(reply)
    monkeypatch.setattr(transfer, "default_resolver", resolve)
    monkeypatch.setattr(transfer, "PinnedTransport", transport)
    result = await probe.inspect_file(FILE)
    assert result.marker == "fetch_refused"
    assert len(requests) == 1
    assert not {"authorization", "cookie"} & set(requests[0].headers)


async def test_total_fetch_deadline(monkeypatch):
    async def resolve(*a):
        await asyncio.sleep(1)
        return ["8.8.8.8"]
    monkeypatch.setattr(probe, "FETCH_SECONDS", .01)
    monkeypatch.setattr(transfer, "default_resolver", resolve)
    assert (await probe.inspect_file(FILE)).marker == "transfer_timeout"


async def test_real_auth_middleware_requires_bearer_then_tracks(enabled, usage, fetch, monkeypatch):
    # Existing middleware harness supplies only database rows; the actual auth,
    # context binding, diagnostic SDK dispatch and tracking all run here.
    from tests.test_mcp_auth_failure_budget import _Session, _key
    from src.auth.session import current_principal
    monkeypatch.setattr(auth, "async_session", lambda: _Session(api_key=_key(user_id=None)))
    monkeypatch.setattr(settings, "multi_user_mode", False)
    responses = []
    calls = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        responses.append(message)
    async def downstream(scope, receive, send):
        calls.append(current_principal.get())
        await text_call(enabled, {"file": FILE})
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    app = auth.APIKeyMiddleware(downstream)
    scope = {"type": "http", "method": "POST", "path": "/mcp/", "headers": [], "client": ("203.0.113.7", 1234)}
    await app(scope, receive, send)
    assert responses[0]["status"] == 401
    assert calls == [] and usage == [] and fetch["calls"] == []
    responses.clear()
    await app({**scope, "headers": [(b"authorization", b"Bearer omcp_synthetic")]}, receive, send)
    assert responses[0]["status"] == 200
    assert calls == [("api_key", 3)]
    assert len(usage) == 1 and "secret" not in json.dumps(usage)
    assert current_principal.get() is None


async def test_cancellation_restores_log_context(monkeypatch):
    @asynccontextmanager
    async def cancel(*a, **kw):
        raise asyncio.CancelledError
        yield
    monkeypatch.setattr(transfer, "fetch_url_guarded", cancel)
    with pytest.raises(asyncio.CancelledError):
        await probe.inspect_file(FILE)
    assert probe._sensitive_fetch.get() is False


async def test_zip_directory_false_count_refused_before_object_construction(fetch, monkeypatch):
    data = bytearray(zip_bytes())
    end = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, end + 8, 0, 0)
    fetch["payload"] = data
    def forbidden(*a, **kw):
        raise AssertionError("ZipFile must not be constructed")
    monkeypatch.setattr(probe.zipfile, "ZipFile", forbidden)
    result = await probe.inspect_file(FILE, 0)
    assert result.marker == "validation_failed"


async def test_jpeg_derivative_detected_independent_of_name_mime(fetch):
    fetch["payload"] = b"\xff\xd8\xff\xe0" + b"synthetic-jpeg-signature"
    result = json.loads(await probe.inspect_file({**FILE, "mime_type": "image/png", "file_name": "wrong.png"}))
    assert result["detected_format"] == "jpeg"
    assert result["sha256"] == hashlib.sha256(fetch["payload"]).hexdigest()


async def test_actual_http_malformed_envelopes_redact_logs_and_responses(enabled, caplog):
    from src.mcp_server.native_file_privacy import NativeFilePrivacy
    app = enabled.streamable_http_app()
    async with enabled.session_manager.run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=NativeFilePrivacy(app)), base_url="http://localhost") as client:
            with caplog.at_level(logging.DEBUG):
                headers = {"accept": "application/json, text/event-stream"}
                # JSON-RPC model itself invalid: transport emits a 400 with
                # Pydantic input values unless the response guard replaces it.
                response = await client.post("/", headers=headers, json={"jsonrpc": "wrong", "id": 1, "method": "tools/call", "params": {"name": "inspect_chatgpt_file", "arguments": {"file": FILE}}})
                assert response.status_code == 400
                assert "input values redacted" in response.text
                assert "secret" not in response.text
                # Valid JSON-RPC, malformed MCP envelope: SDK session emits
                # root WARNING/DEBUG before any registered tool can run.
                response = await client.post("/", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": None, "arguments": {"file": FILE}}})
                assert "secret" not in response.text
    assert "secret" not in caplog.text
    assert "Invalid MCP request parameters (input values redacted)" in caplog.text


@pytest.mark.parametrize("status,bodies", [(200, [b"data: one\n\n", b"data: two\n\n"]), (400, [b'{"error":"ordinary refusal"}'])])
async def test_privacy_guard_preserves_other_responses(status, bodies):
    from src.mcp_server.native_file_privacy import NativeFilePrivacy
    messages = [{"type": "http.response.start", "status": status, "headers": [(b"x-security", b"preserve")]}]
    messages += [{"type": "http.response.body", "body": b, "more_body": i < len(bodies)-1} for i, b in enumerate(bodies)]
    async def app(scope, receive, send):
        for m in messages:
            await send(m)
    seen = []
    async def send(m):
        seen.append(m)
    await NativeFilePrivacy(app)({}, None, send)
    assert seen == messages


async def test_privacy_guard_preserves_headers_and_corrects_length():
    from src.mcp_server.native_file_privacy import NativeFilePrivacy
    raw = b'{"jsonrpc":"2.0","id":"server-error","error":{"code":-32602,"message":"Validation error: secret"}}'
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 400, "headers": [(b"content-length", str(len(raw)).encode()), (b"x-security", b"preserve")]})
        await send({"type": "http.response.body", "body": raw})
    seen = []
    async def send(m):
        seen.append(m)
    await NativeFilePrivacy(app)({}, None, send)
    headers = dict(seen[0]["headers"])
    assert headers[b"x-security"] == b"preserve"
    assert int(headers[b"content-length"]) == len(seen[1]["body"])
    assert "secret" not in seen[1]["body"].decode()
