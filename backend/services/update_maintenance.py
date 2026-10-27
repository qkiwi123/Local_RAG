"""Keep the candidate installation read-only until update validation finishes."""
from pathlib import Path
import json


class UpdateMaintenanceMiddleware:
    def __init__(self, app, root: Path):
        self.app, self.root = app, root

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"} and not scope["path"].startswith("/api/local-update/"):
            marker = self.root / "state/update-active.json"
            if marker.exists():
                await send({"type": "http.response.start", "status": 503,
                            "headers": [(b"content-type", b"application/json; charset=utf-8")]})
                await send({"type": "http.response.body", "body": json.dumps({"detail": "项目正在更新与验证，请完成后再修改或发送。"}, ensure_ascii=False).encode()})
                return
        await self.app(scope, receive, send)
