"""Explicit browser-origin opt-in for bearer-authenticated remote engines."""

import os
from urllib.parse import urlsplit

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send


def parse_ui_origins(value: str) -> list[str]:
    """Require exact HTTP(S) origins, never URLs, wildcards, or credential strings."""
    origins = []
    for item in value.split(","):
        origin = item.strip()
        if not origin:
            continue
        try:
            parsed = urlsplit(origin)
            valid = (
                parsed.scheme in {"http", "https"} and parsed.hostname
                and parsed.username is None and parsed.password is None
                and not parsed.path and not parsed.query and not parsed.fragment
                and "*" not in origin and "\\" not in origin
                and not any(char.isspace() for char in origin)
                and origin.isascii() and (parsed.port is None or 1 <= parsed.port <= 65535)
                and origin == f"{parsed.scheme}://{parsed.netloc}"
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("RESPAWNED_UI_ORIGINS must contain comma-separated exact HTTP(S) origins without paths, credentials, or wildcards")
        if origin not in origins:
            origins.append(origin)
    return origins


class RemoteUIMiddleware:
    """CORS is disabled by default and can never extend local cookie sessions."""

    def __init__(self, app: ASGIApp, *, origins: list[str] | None = None):
        self.app = app
        selected = parse_ui_origins(os.environ.get("RESPAWNED_UI_ORIGINS", "")) if origins is None else origins
        self.remote = CORSMiddleware(
            app, allow_origins=selected, allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Respawned-Request"],
            expose_headers=["Content-Disposition"],
        ) if selected else app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        application = scope.get("app")
        # The CLI attaches its LocalSession after module import. Check each
        # request, including preflights, before considering remote CORS opt-in.
        local = application is not None and getattr(application.state, "local_session", None) is not None
        await (self.app if local else self.remote)(scope, receive, send)
