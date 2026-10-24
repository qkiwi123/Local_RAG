"""Portable-only Host/Origin checks. Not authentication against local programs."""
from urllib.parse import urlsplit


class LocalOnlyMiddleware:
    def __init__(self, app, port_provider):
        self.app = app
        self.port_provider = port_provider

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        hosts = [value.decode("latin1") for name, value in headers if name.lower() == b"host"]
        origins = [value.decode("latin1") for name, value in headers if name.lower() == b"origin"]
        allowed = False
        try:
            target = urlsplit("http://" + hosts[0]) if len(hosts) == 1 else None
            allowed = bool(target and target.hostname in {"127.0.0.1", "localhost"}
                           and target.port == self.port_provider() and not target.username
                           and not target.password and not target.path and not target.query and not target.fragment)
            if origins:
                origin = urlsplit(origins[0])
                allowed = allowed and len(origins) == 1 and origin.scheme == "http" and (
                    origin.hostname, origin.port) == (target.hostname, target.port) and not (
                    origin.username or origin.password or origin.path or origin.query or origin.fragment)
            fetch_site = next((value.lower() for name, value in headers if name.lower() == b"sec-fetch-site"), b"")
            if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and fetch_site in {b"cross-site", b"same-site"}:
                allowed = False
        except (ValueError, IndexError, AttributeError):
            allowed = False
        if not allowed:
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
            await send({"type": "http.response.body", "body": b"Portable access requires same-origin loopback requests"})
            return
        await self.app(scope, receive, send)
