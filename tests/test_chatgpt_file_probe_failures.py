"""Closed failure diagnostics: real guarded paths, fake network, no credentials."""
from contextlib import asynccontextmanager
import json
import logging
import ssl
from types import SimpleNamespace

import httpx
import pytest

from src.services import chatgpt_file_probe as probe, transfer
from tests.test_chatgpt_file_probe import FILE, SECRET_URL, PNG, zip_bytes, enabled, usage, text_call


def diagnostic(result):
    # BodyOutcome's existing refusal sentinel remains the final protocol line.
    report = json.loads(result.prose)
    assert result.endswith(f'MCP-REFUSAL {{"code":"{result.marker}"}}')
    assert set(report) <= {
        "status", "phase", "reason", "http_status", "download_ms", "inspection_ms", "elapsed_ms",
    }
    assert report["status"] == "refused"
    assert "secret" not in result
    assert "files.example.net" not in result
    assert all(type(report[key]) is float and report[key] >= 0 for key in ("download_ms", "inspection_ms", "elapsed_ms"))
    return report


@pytest.fixture
def network(monkeypatch):
    state = {"responses": [], "calls": [], "addresses": ["8.8.8.8"], "error": None}
    async def resolve(*a):
        if isinstance(state["addresses"], Exception):
            raise state["addresses"]
        return state["addresses"]
    def make_transport(**kwargs):
        assert kwargs["address"] == "8.8.8.8"
        assert kwargs["retries"] == 0
        def reply(request):
            state["calls"].append(request)
            if state["error"] is not None:
                raise state["error"]
            return state["responses"][min(len(state["calls"])-1, len(state["responses"])-1)]
        return httpx.MockTransport(reply)
    monkeypatch.setattr(transfer, "default_resolver", resolve)
    monkeypatch.setattr(transfer, "PinnedTransport", make_transport)
    return state


@pytest.mark.parametrize("url,phase,reason", [
    ("http://files.example.net/secret", "url_policy", "url_policy_refused"),
    ("https://[bad/secret", "url_policy", "url_policy_refused"),
    ("https://127.0.0.1/secret", "address_policy", "address_policy_refused"),
    ("https://169.254.169.254/secret", "address_policy", "address_policy_refused"),
])
async def test_policy_refusals_classified_before_egress(network, url, phase, reason):
    report = diagnostic(await probe.inspect_file({**FILE, "download_url": url}))
    assert (report["phase"], report["reason"]) == (phase, reason)
    assert "http_status" not in report
    assert network["calls"] == []


@pytest.mark.parametrize("answers,reason,phase", [
    (OSError(SECRET_URL), "dns_failed", "dns"),
    ([], "dns_failed", "dns"),
    (["secret-invalid-address"], "dns_failed", "dns"),
    (["8.8.8.8", "127.0.0.1"], "address_policy_refused", "address_policy"),
    (TimeoutError(SECRET_URL), "timeout", "download"),
])
async def test_dns_and_all_address_failures(network, answers, reason, phase):
    network["addresses"] = answers
    result = await probe.inspect_file(FILE)
    report = diagnostic(result)
    assert (report["phase"], report["reason"]) == (phase, reason)
    assert result.marker == ("transfer_timeout" if reason == "timeout" else "fetch_refused")
    assert network["calls"] == []


@pytest.mark.parametrize("status,headers,reason", [
    (403, {"www-authenticate": SECRET_URL}, "http_status"),
    (404, {}, "http_status"),
    (503, {}, "http_status"),
    (200, {"content-encoding": "secret-encoding"}, "content_encoding_refused"),
    (200, {"content-length": "secret-length"}, "content_length_invalid"),
    (302, {}, "redirect_location_missing"),
    (302, {"location": SECRET_URL}, "redirect_limit"),
])
async def test_response_failure_metadata_without_remote_material(network, caplog, status, headers, reason):
    network["responses"] = [httpx.Response(status, headers=headers, content=b"secret response body")]
    with caplog.at_level(logging.DEBUG):
        report = diagnostic(await probe.inspect_file(FILE))
    assert (report["phase"], report["reason"], report["http_status"]) == ("response", reason, status)
    assert "secret" not in caplog.text
    assert "files.example.net" not in caplog.text


@pytest.mark.parametrize("location,phase,reason", [
    ("http://files.example.net/secret", "url_policy", "url_policy_refused"),
    ("https://127.0.0.1/secret", "address_policy", "address_policy_refused"),
])
async def test_redirect_revalidation_classifies_original_refusal(network, location, phase, reason):
    network["responses"] = [httpx.Response(302, headers={"location": location})]
    report = diagnostic(await probe.inspect_file(FILE))
    assert (report["phase"], report["reason"]) == (phase, reason)
    assert len(network["calls"]) == 1


def tls_error():
    error = httpx.ConnectError(SECRET_URL)
    error.__cause__ = ssl.SSLCertVerificationError("secret TLS certificate detail")
    return error


@pytest.mark.parametrize("error,phase,reason,code", [
    (httpx.ConnectError(SECRET_URL), "network", "network_failed", "fetch_refused"),
    (httpx.RemoteProtocolError(SECRET_URL), "network", "network_failed", "fetch_refused"),
    (tls_error(), "network", "tls_failed", "fetch_refused"),
    (httpx.ConnectTimeout(SECRET_URL), "download", "timeout", "transfer_timeout"),
    (RuntimeError(SECRET_URL), "download", "unexpected_failure", "fetch_refused"),
])
async def test_request_failure_classification(network, error, phase, reason, code):
    network["error"] = error
    result = await probe.inspect_file(FILE)
    report = diagnostic(result)
    assert (report["phase"], report["reason"], result.marker) == (phase, reason, code)
    assert "http_status" not in report


@pytest.mark.parametrize("error,phase,reason,code", [
    (httpx.ReadError(SECRET_URL), "network", "network_failed", "fetch_refused"),
    (httpx.ReadTimeout(SECRET_URL), "download", "timeout", "transfer_timeout"),
    (transfer.Timeout(SECRET_URL), "download", "timeout", "transfer_timeout"),
    (RuntimeError(SECRET_URL), "download", "unexpected_failure", "fetch_refused"),
])
async def test_failure_during_body_stream_preserves_classification(network, error, phase, reason, code):
    class FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"secret partial response"
            raise error
    network["responses"] = [httpx.Response(200, stream=FailingStream())]
    result = await probe.inspect_file(FILE)
    report = diagnostic(result)
    assert (report["phase"], report["reason"], result.marker) == (phase, reason, code)
    assert "sha256" not in report


async def test_declared_and_counted_sizes_have_safe_reason(network):
    network["responses"] = [httpx.Response(200, headers={"content-length": str(probe.MAX_BYTES + 1)})]
    first = diagnostic(await probe.inspect_file(FILE))
    network["responses"] = [httpx.Response(200, content=b"x" * (probe.MAX_BYTES + 1))]
    second = diagnostic(await probe.inspect_file(FILE))
    assert first["reason"] == second["reason"] == "size_limit"
    assert first["phase"] == second["phase"] == "download"


async def test_zip_validation_and_missing_signature(network):
    network["responses"] = [httpx.Response(200, content=b"PK\x03\x04secret corrupt ZIP")]
    report = diagnostic(await probe.inspect_file(FILE, 0))
    assert (report["phase"], report["reason"]) == ("zip_inspection", "zip_validation_failed")
    network["responses"] = [httpx.Response(200, content=PNG)]
    report = diagnostic(await probe.inspect_file(FILE, 0))
    assert (report["phase"], report["reason"]) == ("inspection", "zip_signature_missing")


@pytest.mark.parametrize("reason,status,expected_reason,expected_status", [
    ("secret-reason", 403, "unexpected_failure", None),
    (None, "secret-status", "unexpected_failure", None),
    (transfer.FetchFailureReason.HTTP_STATUS, True, "http_status", None),
    (transfer.FetchFailureReason.HTTP_STATUS, 600, "http_status", None),
    (transfer.FetchFailureReason.HTTP_STATUS, "secret-status", "http_status", None),
    (transfer.FetchFailureReason.HTTP_STATUS, 403, "http_status", 403),
])
async def test_exception_metadata_rechecked_at_output_boundary(monkeypatch, reason, status, expected_reason, expected_status):
    error = transfer.SSRFError(SECRET_URL)
    error.reason = reason
    error.http_status = status
    @asynccontextmanager
    async def fail(*a, **kw):
        raise error
        yield
    monkeypatch.setattr(transfer, "fetch_url_guarded", fail)
    report = diagnostic(await probe.inspect_file(FILE))
    assert report["reason"] == expected_reason
    assert report.get("http_status") == expected_status


async def test_safe_failure_timings_separate_download_and_inspection(network, monkeypatch):
    network["responses"] = [httpx.Response(200, content=zip_bytes())]
    times = iter([0.0, 4.0, 5.0])
    monkeypatch.setattr(probe, "time", SimpleNamespace(monotonic=lambda: next(times)))
    def fail(*a):
        raise LookupError(SECRET_URL)
    monkeypatch.setattr(probe, "_zip_summary", fail)
    report = diagnostic(await probe.inspect_file(FILE, 0))
    assert report["phase"] == "inspection" and report["reason"] == "unexpected_failure"
    assert report["download_ms"] == 4000.0
    assert report["inspection_ms"] == 1000.0
    assert report["elapsed_ms"] == 5000.0


async def test_zero_monotonic_inspection_start_not_treated_as_missing(network, monkeypatch):
    network["responses"] = [httpx.Response(200, content=b"PK\x03\x04bad")]
    times = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(probe, "time", SimpleNamespace(monotonic=lambda: next(times)))
    report = diagnostic(await probe.inspect_file(FILE, 0))
    assert report["download_ms"] == 0.0 and report["inspection_ms"] == 1000.0


async def test_invalid_arguments_have_no_download_time():
    report = diagnostic(await probe.inspect_file({"file_id": "secret"}))
    assert (report["phase"], report["reason"]) == ("arguments", "invalid_arguments")
    assert report["download_ms"] == report["inspection_ms"] == 0.0


def test_shared_error_text_args_and_type_preserved_with_optional_metadata():
    error = transfer.SSRFError("legacy failure text", reason=transfer.FetchFailureReason.HTTP_STATUS, http_status=403)
    assert error.args == ("legacy failure text",) and str(error) == "legacy failure text"
    assert isinstance(error, transfer.TransferError)
    assert error.reason is transfer.FetchFailureReason.HTTP_STATUS and error.http_status == 403
    assert transfer.SSRFError("legacy").reason is None


def test_http_error_cause_classification_bounded_without_text_parsing():
    error = httpx.ConnectError(SECRET_URL)
    error.__cause__ = error
    assert transfer.http_failure_reason(error) is transfer.FetchFailureReason.NETWORK
    class UnrenderableSSL(ssl.SSLError):
        def __str__(self):
            raise AssertionError("must not inspect exception text")
    error.__cause__ = UnrenderableSSL()
    assert transfer.http_failure_reason(error) is transfer.FetchFailureReason.TLS


async def test_sdk_refusal_returns_diagnostic_but_usage_stores_only_outcome(network, enabled, usage):
    network["responses"] = [httpx.Response(403, content=b"secret body")]
    result = await text_call(enabled, {"file": FILE})
    report = json.loads(result.splitlines()[0])
    assert report["phase"] == "response" and report["reason"] == "http_status" and report["http_status"] == 403
    assert result.endswith('MCP-REFUSAL {"code":"fetch_refused"}')
    assert len(usage) == 1 and usage[0][0] == "inspect_chatgpt_file"
    assert usage[0][1]["error"] == "fetch_refused"
    assert "secret" not in result + json.dumps(usage)
    assert not {"phase", "reason", "http_status", "file", "download_url", "file_id"} & usage[0][1].keys()


@pytest.mark.parametrize("status", [600, 999])
async def test_nonstandard_remote_status_preserves_shared_error_and_omits_metadata(network, status):
    network["responses"] = [httpx.Response(status)]
    with pytest.raises(transfer.SSRFError) as caught:
        async with transfer.fetch_url_guarded(SECRET_URL, allow_http=False, max_bytes=probe.MAX_BYTES):
            pytest.fail("non-200 response must not yield")
    assert str(caught.value) == f"Expected HTTP 200, got {status}"
    assert caught.value.args == (f"Expected HTTP 200, got {status}",)
    assert caught.value.reason is transfer.FetchFailureReason.HTTP_STATUS
    assert caught.value.http_status is None
    report = diagnostic(await probe.inspect_file(FILE))
    assert report["phase"] == "response" and report["reason"] == "http_status"
    assert "http_status" not in report
