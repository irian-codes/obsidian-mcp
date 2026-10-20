"""Opt-in, memory-only native ChatGPT file integrity diagnostic.

No file metadata is evidence of format, and no remote value reaches a log or
error. Keep this separate from vault publication and durable transfer tokens.
"""
from __future__ import annotations

from contextvars import ContextVar
from enum import StrEnum
import hashlib
import io
import json
import logging
import struct
import time
import zipfile

import httpx
from typing_extensions import NotRequired, TypedDict

from src.services import transfer
from src.services.tool_outcomes import body_refusal

MAX_BYTES = 4 * 1024 * 1024
MAX_ZIP_MEMBERS = 64
FETCH_SECONDS = 15.0


class ChatGPTFile(TypedDict):
    download_url: str
    file_id: str
    mime_type: NotRequired[str]
    file_name: NotRequired[str]


# HTTPX INFO logs URLs; httpcore DEBUG traces may include presigned paths and
# headers. Filter only this task's fetch, without raising global logger floors
# or hiding concurrent ordinary requests. These are the SDK transport loggers.
_sensitive_fetch: ContextVar[bool] = ContextVar("native_file_sensitive_fetch", default=False)


class _SensitiveFetchFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _sensitive_fetch.get()


for _name in (
    "httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
    "httpcore.proxy", "httpcore.socks",
):
    logging.getLogger(_name).addFilter(_SensitiveFetchFilter())


class _InvalidZip(Exception):
    pass


class ProbePhase(StrEnum):
    ARGUMENTS = "arguments"
    URL_POLICY = "url_policy"
    ADDRESS_POLICY = "address_policy"
    DNS = "dns"
    NETWORK = "network"
    RESPONSE = "response"
    DOWNLOAD = "download"
    ZIP_INSPECTION = "zip_inspection"
    INSPECTION = "inspection"


class ProbeFailureReason(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    SIZE_LIMIT = "size_limit"
    ZIP_VALIDATION = "zip_validation_failed"
    ZIP_SIGNATURE = "zip_signature_missing"
    UNEXPECTED = "unexpected_failure"


_FETCH_PHASES = {
    transfer.FetchFailureReason.URL_POLICY: ProbePhase.URL_POLICY,
    transfer.FetchFailureReason.ADDRESS_POLICY: ProbePhase.ADDRESS_POLICY,
    transfer.FetchFailureReason.DNS: ProbePhase.DNS,
    transfer.FetchFailureReason.NETWORK: ProbePhase.NETWORK,
    transfer.FetchFailureReason.TLS: ProbePhase.NETWORK,
    transfer.FetchFailureReason.TIMEOUT: ProbePhase.DOWNLOAD,
    transfer.FetchFailureReason.HTTP_STATUS: ProbePhase.RESPONSE,
    transfer.FetchFailureReason.REDIRECT_LOCATION: ProbePhase.RESPONSE,
    transfer.FetchFailureReason.REDIRECT_LIMIT: ProbePhase.RESPONSE,
    transfer.FetchFailureReason.CONTENT_ENCODING: ProbePhase.RESPONSE,
    transfer.FetchFailureReason.CONTENT_LENGTH: ProbePhase.RESPONSE,
}


def _failure(
    phase: ProbePhase, reason: transfer.FetchFailureReason | ProbeFailureReason,
    code: str, *, start: float, inspection_start: float | None = None,
    http_status: int | None = None,
) -> str:
    """Output only closed labels, measured durations and validated status.

    Exception messages/args/causes are never rendered. Recheck metadata here,
    even though the shared fetcher validates its own error constructor.
    """
    if type(phase) is not ProbePhase:
        phase = ProbePhase.DOWNLOAD
    if type(reason) not in (transfer.FetchFailureReason, ProbeFailureReason):
        reason = ProbeFailureReason.UNEXPECTED
    end = time.monotonic()
    report = {
        "status": "refused", "phase": phase.value, "reason": reason.value,
        "download_ms": round(max(0.0, (inspection_start if inspection_start is not None else end) - start) * 1000, 2)
        if phase is not ProbePhase.ARGUMENTS else 0.0,
        "inspection_ms": round(max(0.0, end - inspection_start) * 1000, 2)
        if inspection_start is not None else 0.0,
        "elapsed_ms": round(max(0.0, end - start) * 1000, 2),
    }
    if type(http_status) is int and 100 <= http_status <= 599:
        report["http_status"] = http_status
    return body_refusal(json.dumps(report, separators=(",", ":")), code)


def _format(signature: bytes) -> str:
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if signature.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if signature.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "zip"
    return "unknown"


def _vet_zip_directory(payload: bytes) -> None:
    # Vet the canonical, single-disk non-ZIP64 envelope before ZipFile creates
    # an object for every central-directory entry. A lying entry count alone
    # does not bound Python's parser. This diagnostic refuses exotic layouts.
    end = payload.rfind(b"PK\x05\x06", max(0, len(payload) - 65557))
    if end < 0 or end + 22 > len(payload):
        raise _InvalidZip
    disk, directory_disk, disk_count, count, size, start, comment = struct.unpack_from(
        "<4H2IH", payload, end + 4,
    )
    if (disk or directory_disk or disk_count != count or count > MAX_ZIP_MEMBERS
            or end + 22 + comment != len(payload) or start + size != end):
        raise _InvalidZip
    cursor = start
    for _ in range(count):
        if cursor + 46 > end or payload[cursor:cursor + 4] != b"PK\x01\x02":
            raise _InvalidZip
        name, extra, note = struct.unpack_from("<3H", payload, cursor + 28)
        cursor += 46 + name + extra + note
    if cursor != end:
        raise _InvalidZip


def _zip_summary(payload: bytes, member_index: int | None) -> dict:
    """Bound directory parsing by download size; never extract paths to disk."""
    _vet_zip_directory(payload)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise _InvalidZip
        if sum(item.file_size for item in members) > MAX_BYTES:
            raise _InvalidZip
        if any(
            item.flag_bits & 1
            or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            for item in members
        ):
            raise _InvalidZip
        summary = {"member_count": len(members), "validation": "directory_only"}
        if member_index is None:
            return summary
        if member_index >= len(members) or members[member_index].is_dir():
            raise _InvalidZip
        size = 0
        digest = hashlib.sha256()
        signature = b""
        with archive.open(members[member_index]) as stream:
            while chunk := stream.read(64 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise _InvalidZip
                signature = (signature + chunk[:16])[:16]
                digest.update(chunk)
        summary.update(
            validation="selected_member_crc_checked", member_index=member_index,
            member_size_bytes=size, member_sha256=digest.hexdigest(),
            member_signature_hex=signature.hex(), member_detected_format=_format(signature),
        )
        return summary


async def inspect_file(file: ChatGPTFile, member_index: int | None = None) -> str:
    start = time.monotonic()
    inspection_start = None
    # Also validate direct Python callers; do not stringify an invalid value.
    if (
        not isinstance(file, dict)
        or not isinstance(file.get("download_url"), str)
        or not isinstance(file.get("file_id"), str)
        or not file["file_id"]
        or (member_index is not None and (type(member_index) is not int or member_index < 0))
    ):
        return _failure(ProbePhase.ARGUMENTS, ProbeFailureReason.INVALID_ARGUMENTS, "invalid_argument", start=start)
    token = _sensitive_fetch.set(True)
    try:
        payload = bytearray()
        async with transfer.fetch_url_guarded(
            file["download_url"], allow_http=False, max_bytes=MAX_BYTES,
            deadline=FETCH_SECONDS,
        ) as fetched:
            async for chunk in fetched.chunks:
                if len(payload) + len(chunk) > MAX_BYTES:
                    raise transfer.TooLarge
                payload.extend(chunk)
        inspection_start = time.monotonic()
        signature = bytes(payload[:16])
        detected = _format(signature)
        report = {
            "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
            "signature_hex": signature.hex(), "detected_format": detected,
            "format_basis": "signature_only",
        }
        if detected == "zip":
            try:
                report["zip"] = _zip_summary(payload, member_index)
            except (zipfile.BadZipFile, RuntimeError, ValueError, NotImplementedError, _InvalidZip):
                return _failure(ProbePhase.ZIP_INSPECTION, ProbeFailureReason.ZIP_VALIDATION, "validation_failed", start=start, inspection_start=inspection_start)
        elif member_index is not None:
            return _failure(ProbePhase.INSPECTION, ProbeFailureReason.ZIP_SIGNATURE, "invalid_argument", start=start, inspection_start=inspection_start)
        end = time.monotonic()
        report["download_ms"] = round((inspection_start - start) * 1000, 2)
        report["inspection_ms"] = round((end - inspection_start) * 1000, 2)
        report["elapsed_ms"] = round((end - start) * 1000, 2)
        return json.dumps(report, separators=(",", ":"))
    except transfer.TooLarge:
        return _failure(ProbePhase.DOWNLOAD, ProbeFailureReason.SIZE_LIMIT, "fetch_refused", start=start)
    except (transfer.Timeout, httpx.TimeoutException, TimeoutError):
        return _failure(ProbePhase.DOWNLOAD, transfer.FetchFailureReason.TIMEOUT, "transfer_timeout", start=start)
    except transfer.SSRFError as exc:
        reason = exc.reason if type(exc.reason) is transfer.FetchFailureReason else ProbeFailureReason.UNEXPECTED
        # Status is useful only alongside recognized fetch metadata. Never
        # trust an arbitrary exception attribute as a diagnostic value.
        status = exc.http_status if type(reason) is transfer.FetchFailureReason else None
        return _failure(
            _FETCH_PHASES.get(reason, ProbePhase.DOWNLOAD), reason,
            "transfer_timeout" if reason is transfer.FetchFailureReason.TIMEOUT else "fetch_refused",
            start=start, http_status=status,
        )
    except httpx.HTTPError as exc:
        # Stream errors can occur after the shared fetcher yielded the body.
        reason = transfer.http_failure_reason(exc)
        return _failure(_FETCH_PHASES[reason], reason, "fetch_refused", start=start)
    except Exception:
        # The shared fetcher's exceptions can contain signed URLs. Never allow
        # them into _tracked/SDK traceback formatting, even unexpected failures.
        # Cancellation is BaseException and must still unwind/close connections.
        return _failure(
            ProbePhase.INSPECTION if inspection_start is not None else ProbePhase.DOWNLOAD,
            ProbeFailureReason.UNEXPECTED, "fetch_refused", start=start, inspection_start=inspection_start,
        )
    finally:
        _sensitive_fetch.reset(token)
