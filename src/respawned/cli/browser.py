"""Launch the local browser as an authenticated companion to the CLI."""

import asyncio
import socket
import webbrowser


def launch_ui(port: int, *, open_browser: bool = True) -> None:
    import uvicorn

    from respawned.api.app import app
    from respawned.api.session import LocalSession

    manager = LocalSession(port)
    # Bind before handing out credentials: an occupied port must never receive
    # our launch capability. No host option or forwarded-header trust is allowed.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        app.state.local_session = manager
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, proxy_headers=False))

        async def open_when_ready() -> None:
            while not server.started and not server.should_exit:
                await asyncio.sleep(0.05)
            if server.started:
                print(f"Open Respawned (one-use link, valid for 5 minutes):\n{manager.launch_url}", flush=True)
                if open_browser:
                    await asyncio.to_thread(webbrowser.open, manager.launch_url)

        async def run() -> None:
            opener = asyncio.create_task(open_when_ready())
            try:
                await server.serve(sockets=[listener])
            finally:
                opener.cancel()
                await asyncio.gather(opener, return_exceptions=True)

        try:
            asyncio.run(run())
        finally:
            manager.clear()
            app.state.local_session = None
