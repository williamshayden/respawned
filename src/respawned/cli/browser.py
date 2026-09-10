"""Launch one local engine with browser and private CLI access."""
import asyncio
import socket
import webbrowser

from respawned.local_connection import publish_local_token


def launch_ui(port: int, *, open_browser: bool = True, show_ui_link: bool = True) -> None:
    import uvicorn
    from respawned.api.app import app
    from respawned.api.session import LocalSession

    manager = LocalSession(port)
    # Bind before creating any capability file or handing out a launch link.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        app.state.local_session = manager
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, proxy_headers=False))

        async def open_when_ready():
            while not server.started and not server.should_exit:
                await asyncio.sleep(0.05)
            if server.started:
                print(f"Respawned engine: {manager.origin}\nLocal CLI access is ready.", flush=True)
                if port != 8000:
                    print(f"For this engine: respawned --api-url {manager.origin} <command>", flush=True)
                if show_ui_link:
                    print(f"Browser link (one use, valid for 5 minutes):\n{manager.launch_url}", flush=True)
                if open_browser:
                    await asyncio.to_thread(webbrowser.open, manager.launch_url)

        async def serve():
            opener = asyncio.create_task(open_when_ready())
            try:
                await server.serve(sockets=[listener])
            finally:
                opener.cancel()
                await asyncio.gather(opener, return_exceptions=True)

        try:
            with publish_local_token(manager.origin, manager.cli_token):
                try:
                    asyncio.run(serve())
                except KeyboardInterrupt:
                    pass
        finally:
            manager.close()
            app.state.local_session = None
