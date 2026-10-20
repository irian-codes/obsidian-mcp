"""Native PNG/JPEG import: verify source bytes before anchored no-clobber write."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import PurePosixPath
import re
import time
from types import SimpleNamespace

import httpx

from src.config import MAX_IMPORT_URL_CHARS, settings
from src.services import chatgpt_file_probe as native, transfer, vault_fs
from src.services.tool_outcomes import body_refusal

_EXTENSIONS = {"png": ".png", "jpeg": ".jpg"}
_REASONS = frozenset({
    "invalid_arguments", "invalid_checksum", "checksum_mismatch", "invalid_extension",
    "extension_mismatch", "unsupported_image_format", "already_exists", "unsafe_path",
    "vault_unavailable", "size_limit", "publication_busy", "publication_timeout",
    "credential_unusable", "concurrent_target", "unsupported_filesystem", "io_failure",
    "unexpected_failure",
})


def refuse(reason: str, code: str, *, detected_format: str | None = None) -> str:
    report = {"status": "refused", "reason": reason if reason in _REASONS else "unexpected_failure"}
    if detected_format in _EXTENSIONS:
        report.update(detected_format=detected_format, canonical_extension=_EXTENSIONS[detected_format])
    return body_refusal(json.dumps(report, separators=(",", ":")), code)


async def import_image(file, root: str, rel: str, expected_sha256: str, *, before_publish) -> str:
    start = time.monotonic()
    if (
        not isinstance(file, dict)
        or not isinstance(file.get("download_url"), str)
        or len(file["download_url"]) > MAX_IMPORT_URL_CHARS
        or not isinstance(file.get("file_id"), str)
        or not file["file_id"]
    ):
        return refuse("invalid_arguments", "invalid_argument")
    if not isinstance(expected_sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None:
        return refuse("invalid_checksum", "invalid_argument")
    expected_sha256 = expected_sha256.lower()
    cap = settings.max_file_write_bytes
    token = native._sensitive_fetch.set(True)
    phase = "fetch"
    record = None
    try:
        # Keep one bounded bytearray, no whole-payload copies or base64. Close
        # the network connection before any publication; late network cleanup
        # therefore cannot turn a completed write into an ordinary fetch error.
        payload = bytearray()
        async with transfer.fetch_url_guarded(
            file["download_url"], allow_http=False, max_bytes=cap,
            deadline=transfer.DEFAULT_FETCH_DEADLINE,
        ) as fetched:
            async for chunk in fetched.chunks:
                if len(payload) + len(chunk) > cap:
                    raise transfer.TooLarge
                payload.extend(chunk)
        digest = hashlib.sha256(payload).hexdigest()
        detected = native._format(bytes(payload[:16]))
        if detected not in _EXTENSIONS:
            return refuse("unsupported_image_format", "validation_failed")
        if not hmac.compare_digest(digest, expected_sha256):
            return refuse("checksum_mismatch", "validation_failed")
        suffix = PurePosixPath(rel).suffix.lower()
        if suffix not in ({".jpg", ".jpeg"} if detected == "jpeg" else {".png"}):
            return refuse("extension_mismatch", "validation_failed", detected_format=detected)

        phase = "publication"
        vault_fs.check_publication_support(root)
        vault_fs.check_destination_mount(root, rel, overwrite=False)
        row = SimpleNamespace(vault_root=root, path=rel, overwrite=False, expected_fingerprint=None)
        record = {
            "path": rel, "size_bytes": len(payload), "sha256": digest,
            "detected_format": detected, "canonical_extension": _EXTENSIONS[detected],
            "mime": "image/png" if detected == "png" else "image/jpeg",
        }
        async def verified_chunks():
            for offset in range(0, len(payload), 64 * 1024):
                yield bytes(memoryview(payload)[offset:offset + 64 * 1024])

        await transfer.stream_to_vault(
            row, verified_chunks(), max_bytes=cap,
            deadline=start + transfer.DEFAULT_FETCH_DEADLINE,
            idle_timeout=30.0, before_publish=before_publish,
        )
        return json.dumps({"status": "imported", **record}, separators=(",", ":"))
    except transfer.PostPublishFailure:
        # Shared publisher guarantees this is the only failure after the file
        # lands, including cancellation during its completion transaction.
        return body_refusal(json.dumps({
            "status": "partial", "reason": "publication_confirmation_failed",
            **(record or {}), "action": "inspect_destination_before_retry",
        }, separators=(",", ":")), "partial_completion", disposition="partial")
    except transfer.PrePublishAborted:
        return refuse("credential_unusable", "credential_unusable")
    except transfer.QueueTimeout:
        return refuse("publication_busy", "transfer_busy")
    except transfer.TooLarge:
        return refuse("size_limit", "size_limit")
    except (transfer.Timeout, httpx.TimeoutException, TimeoutError):
        if phase == "publication":
            return refuse("publication_timeout", "transfer_timeout")
        return native._failure(native.ProbePhase.DOWNLOAD, transfer.FetchFailureReason.TIMEOUT, "transfer_timeout", start=start)
    except transfer.SSRFError as exc:
        reason = exc.reason if type(exc.reason) is transfer.FetchFailureReason else native.ProbeFailureReason.UNEXPECTED
        return native._failure(
            native._FETCH_PHASES.get(reason, native.ProbePhase.DOWNLOAD), reason,
            "transfer_timeout" if reason is transfer.FetchFailureReason.TIMEOUT else "fetch_refused",
            start=start, http_status=exc.http_status if type(reason) is transfer.FetchFailureReason else None,
        )
    except httpx.HTTPError as exc:
        reason = transfer.http_failure_reason(exc)
        return native._failure(native._FETCH_PHASES[reason], reason, "fetch_refused", start=start)
    except vault_fs.Conflict:
        return refuse("concurrent_target", "concurrent_write")
    except vault_fs.UnsafePath:
        return refuse("unsafe_path", "unsafe_path")
    except vault_fs.UnsupportedFilesystem:
        return refuse("unsupported_filesystem", "unsupported_filesystem")
    except OSError:
        return refuse("io_failure", "io_failure")
    except Exception:
        # Never send signed URLs, remote exception strings or native IDs into
        # _tracked/SDK traceback formatting. Cancellation remains BaseException.
        return refuse("unexpected_failure", "fetch_refused" if phase == "fetch" else "io_failure")
    finally:
        native._sensitive_fetch.reset(token)
