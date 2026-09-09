"""Explicit CLI-launched browser sessions; ordinary API serving stays bearer-only."""

from collections.abc import Callable
import hmac
import secrets
from threading import Lock
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

COOKIE = "respawned_local_session"
BOOTSTRAP_SECONDS = 300
SESSION_SECONDS = 12 * 60 * 60


class LocalSession:
    """One launch capability and one revocable, process-local browser session."""

    def __init__(self, port: int, *, clock: Callable[[], float] = time.monotonic):
        if not 1 <= port <= 65535:
            raise ValueError("UI port must be between 1 and 65535")
        # Cookies are scoped to hostname and path, not port. Separate local
        # engines must not overwrite or clear each other's browser connection.
        self.cookie_name = f"{COOKIE}_{port}"
        self.authority = "127.0.0.1" + (f":{port}" if port != 80 else "")
        self.origin = f"http://{self.authority}"
        self.clock = clock
        self.launch_secret = secrets.token_urlsafe(32)
        self.launch_expires = clock() + BOOTSTRAP_SECONDS
        self.session: str | None = None
        self.session_expires = 0.0
        self.lock = Lock()

    @property
    def launch_url(self) -> str:
        return f"{self.origin}/#login={self.launch_secret}"

    def check_origin(self, request: Request, *, mutation: bool = False) -> None:
        # Exact authority defeats DNS rebinding; forwarded headers never grant trust.
        if request.headers.get("host") != self.authority:
            raise HTTPException(403, "Use the loopback address opened by respawned ui")
        origin = request.headers.get("origin")
        if origin is not None and origin != self.origin:
            raise HTTPException(403, "Cross-origin browser access is not allowed")
        if mutation and (origin != self.origin or request.headers.get("x-respawned-request") != "1"):
            raise HTTPException(403, "A same-origin browser request is required")

    def authenticated(self, request: Request) -> bool:
        self.check_origin(request, mutation=request.method not in {"GET", "HEAD", "OPTIONS"})
        supplied = request.cookies.get(self.cookie_name, "")
        with self.lock:
            if self.clock() >= self.session_expires:
                self.session = None
            return bool(self.session and hmac.compare_digest(supplied.encode(), self.session.encode()))

    def exchange(self, secret: str) -> str:
        with self.lock:
            if not self.launch_secret or self.clock() >= self.launch_expires or not hmac.compare_digest(secret.encode(), self.launch_secret.encode()):
                raise HTTPException(401, "This launch link has expired or was already used. Restart respawned ui for a new link.")
            self.launch_secret = ""
            self.session = secrets.token_urlsafe(32)
            self.session_expires = self.clock() + SESSION_SECONDS
            return self.session

    def clear(self) -> None:
        with self.lock:
            self.session = None
            self.session_expires = 0.0


def local_session(request: Request) -> LocalSession | None:
    return getattr(request.app.state, "local_session", None)


class LaunchRequest(BaseModel):
    secret: str = Field(min_length=1, max_length=128)


def create_session_router() -> APIRouter:
    router = APIRouter(prefix="/v1/ui/session")

    @router.get("")
    def status(request: Request, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        manager = local_session(request)
        return {"authenticated": bool(manager and manager.authenticated(request)), "local_launcher": manager is not None}

    @router.post("")
    def exchange(payload: LaunchRequest, request: Request, response: Response) -> dict:
        manager = local_session(request)
        if manager is None:
            raise HTTPException(404, "Local browser sessions require respawned ui")
        manager.check_origin(request, mutation=True)
        session = manager.exchange(payload.secret)
        # Loopback HTTP cannot use Secure cookies. HttpOnly, host-only scope,
        # Strict SameSite, exact Origin and the custom header protect this mode.
        response.set_cookie(manager.cookie_name, session, httponly=True, samesite="strict", path="/v1/ui", max_age=SESSION_SECONDS)
        response.headers["Cache-Control"] = "no-store"
        return {"authenticated": True, "local_launcher": True}

    @router.delete("", status_code=204)
    def logout(request: Request, response: Response) -> None:
        manager = local_session(request)
        if manager is None or not manager.authenticated(request):
            raise HTTPException(401, "No active local browser session")
        manager.clear()
        response.delete_cookie(manager.cookie_name, path="/v1/ui", httponly=True, samesite="strict")
        response.headers["Cache-Control"] = "no-store"

    return router
