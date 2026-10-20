"""Opt-in SDK 1.29 malformed-envelope privacy boundary.

The tool argument model cannot protect errors emitted before tool dispatch.
Do not buffer requests or successful/streaming responses to inspect arguments.
"""
import logging

_SAFE_VALIDATION_BODY = (
    b'{"jsonrpc":"2.0","id":"server-error","error":{"code":-32602,'
    b'"message":"Invalid request parameters (input values redacted)"}}'
)


class _SDKValidationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # SDK session.py uses root logging and formats Pydantic exceptions and
        # complete messages before tool validation. Limit this to those exact
        # SDK diagnostics; keep request types, status and operational logs.
        if record.pathname.replace("\\", "/").endswith("/mcp/shared/session.py"):
            message = record.getMessage()
            if message.startswith(("Failed to validate request:", "Message that failed validation:", "Failed to validate notification:")):
                record.msg = "Invalid MCP request parameters (input values redacted)"
                record.args = ()
                record.exc_info = None
                record.exc_text = None
        return True


_filter = _SDKValidationFilter()


def install_sdk_validation_redaction() -> None:
    root = logging.getLogger()
    if _filter not in root.filters:
        root.addFilter(_filter)


class NativeFilePrivacy:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        pending_start = None

        async def safe_send(message):
            nonlocal pending_start
            if message["type"] == "http.response.start" and message["status"] == 400:
                pending_start = message
                return
            if pending_start is not None:
                if message["type"] == "http.response.body":
                    # SDK 1.29 sends envelope-validation failures as one JSON
                    # body with fixed server-error id/-32602 and a compact
                    # `Validation error:` message. Match its short prefix only;
                    # never parse/copy a potentially large exception payload.
                    body = message.get("body", b"")
                    if (not message.get("more_body", False)
                            and b'"message":"Validation error:' in body[:256]):
                        headers = [(k, v) for k, v in pending_start.get("headers", []) if k.lower() != b"content-length"]
                        headers.append((b"content-length", str(len(_SAFE_VALIDATION_BODY)).encode()))
                        pending_start = {**pending_start, "headers": headers}
                        message = {**message, "body": _SAFE_VALIDATION_BODY}
                await send(pending_start)
                pending_start = None
            await send(message)

        await self.app(scope, receive, safe_send)
