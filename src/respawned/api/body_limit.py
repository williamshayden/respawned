"""Bound raw request bodies before framework JSON parsing and validation."""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


MAX_REQUEST_BYTES = 2_000_000


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int = MAX_REQUEST_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def reject(status: int, detail: str) -> None:
            await JSONResponse({"detail": detail}, status_code=status,
                               headers={"Cache-Control": "no-store"})(scope, receive, send)

        for name, value in scope.get("headers", ()):
            if name.lower() != b"content-length":
                continue
            length = value.strip().lstrip(b"0") or b"0"
            if not length.isdigit():
                await reject(400, "Invalid Content-Length")
                return
            if len(length) > len(str(self.max_bytes)) or int(length) > self.max_bytes:
                await reject(413, "Request body exceeds 2 MB. Split imports into smaller batches.")
                return

        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                await reject(413, "Request body exceeds 2 MB. Split imports into smaller batches.")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
