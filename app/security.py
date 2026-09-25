"""HTTP boundary for the local, unauthenticated editor.

The process token is intentionally short-lived: restarting the app invalidates
old pages. Browser writes carry it in a header (or a normal form field).
"""

from __future__ import annotations

import hmac
import ipaddress
import re
import secrets
from urllib.parse import parse_qs, urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

CSRF_TOKEN = secrets.token_urlsafe(32)
CSRF_HEADER = b"x-write-csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_HOST_RE = re.compile(r"^(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?$", re.IGNORECASE)
_MAX_FORM_BODY = 8192


def _single_header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    values = [value for key, value in headers if key.lower() == name]
    if len(values) != 1:
        return None
    try:
        return values[0].decode("ascii")
    except UnicodeDecodeError:
        return None


def _loopback_client(scope: Scope) -> bool:
    client = scope.get("client")
    if not client:
        return False
    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


def _host(value: str | None, scheme: str) -> tuple[str, int] | None:
    if value is None:
        return None
    match = _HOST_RE.fullmatch(value)
    if match is None:
        return None
    port = int(match[2]) if match[2] else (443 if scheme == "https" else 80)
    if not 1 <= port <= 65535:
        return None
    return match[1].strip("[]").lower(), port


def _url_origin(value: str | None, *, referer: bool) -> tuple[str, str, int] | None:
    if not value or value != value.strip() or any(c in value for c in "\r\n\\"):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        if not referer and (parsed.path or parsed.query or parsed.fragment):
            return None
        if not referer and value != f"{parsed.scheme}://{parsed.netloc}":
            return None
        if parsed.fragment:
            return None
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    if hostname is None or not 1 <= port <= 65535:
        return None
    return parsed.scheme, hostname.lower(), port


def _same_origin(value: str | None, expected: tuple[str, str, int], *, referer: bool = False) -> bool:
    return _url_origin(value, referer=referer) == expected


async def _form_token(receive: Receive, headers: list[tuple[bytes, bytes]]) -> tuple[str | None, Receive]:
    """Read only small ordinary form bodies, then replay them for FastAPI."""
    content_type = _single_header(headers, b"content-type") or ""
    if content_type.split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        return None, receive
    size = _single_header(headers, b"content-length")
    if size is not None:
        try:
            if int(size) > _MAX_FORM_BODY:
                return None, receive
        except ValueError:
            return None, receive
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return None, receive
        body.extend(message.get("body", b""))
        if len(body) > _MAX_FORM_BODY:
            return None, receive
        if not message.get("more_body", False):
            break
    try:
        fields = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=32)
        tokens = fields.get("_csrf", [])
        token = tokens[0] if len(tokens) == 1 else None
    except (UnicodeDecodeError, ValueError):
        token = None
    replayed = False

    async def replay() -> dict:
        nonlocal replayed
        if not replayed:
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}
        return await receive()

    return token, replay


class LocalRequestGuard:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def reject(status: int, message: bytes) -> None:
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                    (b"content-length", str(len(message)).encode())]})
            await send({"type": "http.response.body", "body": message})

        headers = scope.get("headers", [])
        scheme = scope.get("scheme", "http")
        authority = _host(_single_header(headers, b"host"), scheme)
        if authority is None or not _loopback_client(scope):
            await reject(400, b"Local access only")
            return

        if scope["method"].upper() not in SAFE_METHODS:
            expected = (scheme, *authority)
            origin = _single_header(headers, b"origin")
            referer = _single_header(headers, b"referer")
            # Presence matters: duplicate or malformed values must never become
            # indistinguishable from absent values.
            names = {key.lower() for key, _ in headers}
            if (b"origin" in names and not _same_origin(origin, expected)) or (
                b"referer" in names and not _same_origin(referer, expected, referer=True)
            ):
                await reject(403, b"Cross-origin request blocked")
                return
            fetch_site = _single_header(headers, b"sec-fetch-site")
            if b"sec-fetch-site" in names and fetch_site != "same-origin":
                await reject(403, b"Cross-site request blocked")
                return
            token = _single_header(headers, CSRF_HEADER)
            if token is None and CSRF_HEADER not in names:
                token, receive = await _form_token(receive, headers)
            if token is None or not token.isascii() or not hmac.compare_digest(token, CSRF_TOKEN):
                await reject(403, b"Missing or invalid request token")
                return

        async def send_with_cache_policy(message: dict) -> None:
            if message["type"] == "http.response.start":
                response_headers = message.get("headers", [])
                is_html = any(key.lower() == b"content-type" and value.lower().startswith(b"text/html")
                              for key, value in response_headers)
                has_cache_policy = any(key.lower() == b"cache-control" for key, _ in response_headers)
                if is_html and not has_cache_policy:
                    message = {**message, "headers": [*response_headers, (b"cache-control", b"no-store")]}
            await send(message)

        await self.app(scope, receive, send_with_cache_policy)
