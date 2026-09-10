"""Owned loopback HTTP serving for subprocess client qualification."""

from contextlib import contextmanager
import socket
from threading import Thread
import time

import uvicorn


@contextmanager
def serve_http(application, requests=None):
    requests = requests if requests is not None else []

    async def tracked(scope, receive, send):
        if scope["type"] != "http":
            return await application(scope, receive, send)
        record = {"method": scope["method"], "path": scope["path"], "body": b"", "status": None}
        requests.append(record)

        async def read():
            event = await receive()
            if event["type"] == "http.request":
                record["body"] += event.get("body", b"")
            return event

        async def write(event):
            if event["type"] == "http.response.start":
                record["status"] = event["status"]
            await send(event)

        await application(scope, read, write)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(tracked, host="127.0.0.1", port=port,
                                               log_level="error", access_log=False, proxy_headers=False))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        try:
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started, "Owned test API did not start"
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "Owned test API did not stop"
