"""Opt-in, memory-only native ChatGPT file integrity diagnostic.

No file metadata is evidence of format, and no remote value reaches a log or
error. Keep this separate from vault publication and durable transfer tokens.
"""
from __future__ import annotations

from contextvars import ContextVar
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
    # Also validate direct Python callers; do not stringify an invalid value.
    if (
        not isinstance(file, dict)
        or not isinstance(file.get("download_url"), str)
        or not isinstance(file.get("file_id"), str)
        or not file["file_id"]
        or (member_index is not None and (type(member_index) is not int or member_index < 0))
    ):
        return body_refusal("Invalid native file diagnostic arguments.", "invalid_argument")
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
                return body_refusal("ZIP inspection refused: corrupt, encrypted, unsupported, or diagnostic bounds exceeded.", "validation_failed")
        elif member_index is not None:
            return body_refusal("ZIP member requested for a file without a ZIP signature.", "invalid_argument")
        end = time.monotonic()
        report["download_ms"] = round((inspection_start - start) * 1000, 2)
        report["inspection_ms"] = round((end - inspection_start) * 1000, 2)
        report["elapsed_ms"] = round((end - start) * 1000, 2)
        return json.dumps(report, separators=(",", ":"))
    except transfer.TooLarge:
        return body_refusal("Native file exceeds the 4 MiB diagnostic limit.", "fetch_refused")
    except (transfer.Timeout, httpx.TimeoutException, TimeoutError):
        return body_refusal("Native file diagnostic download timed out.", "transfer_timeout")
    except Exception:
        # The shared fetcher's exceptions can contain signed URLs. Never allow
        # them into _tracked/SDK traceback formatting, even unexpected failures.
        # Cancellation is BaseException and must still unwind/close connections.
        return body_refusal("Native file diagnostic fetch or inspection refused.", "fetch_refused")
    finally:
        _sensitive_fetch.reset(token)
