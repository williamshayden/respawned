"""HTTP client for Respawned workflows and outbox connectors. No local database access."""
from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import socket
from threading import Timer
import time
from typing import Any, Literal
import unicodedata
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from respawned.local_connection import read_local_token

MAX_REQUEST_BYTES = 2_000_000
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_URL_BYTES = 8192
WORKFLOW_PREFIX = "/v1/workflow"
DEFAULT_API_URL = "http://127.0.0.1:8000"
TOKEN_ENVIRONMENTS = ("RESPAWNED_REVIEW_TOKEN", "RESPAWNED_OUTBOX_TOKEN", "RESPAWNED_PROCESS_TOKEN")


class APIError(Exception):
    """A rejected request or an outcome that must be checked before retrying."""

    def __init__(self, message: str, status_code: int | None = None, ambiguous: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.ambiguous = ambiguous


def _normalize_url(value: str) -> str:
    try:
        if not isinstance(value, str) or not value or any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "?" in value or "#" in value
                or "\\" in value):
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        if parsed.scheme == "http":
            local = parsed.hostname.lower() == "localhost"
            try:
                local = local or ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                pass
            if not local:
                raise ValueError
        if len(value.encode("ascii")) > MAX_URL_BYTES:
            raise ValueError
        host = parsed.hostname.lower()
        netloc = f"[{host}]" if ":" in host else host
        if port is not None:
            netloc += f":{port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))
    except (ValueError, TypeError):
        raise APIError("API URL must use HTTPS or loopback HTTP, without credentials, query, or fragment.") from None


def _token(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise APIError("Access tokens must contain printable ASCII without spaces.")
    return value


def _identifier(value: str | int) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or str(value) == "":
        raise APIError("Provide a nonempty record, draft, candidate, or outbox ID.")
    # Dot segments are escaped too, so a reverse proxy cannot reinterpret an ID.
    return quote(str(value), safe="").replace(".", "%2E")


class RespawnedClient:
    """Explicit HTTP operations. No redirects, automatic retries, or provider sends."""

    def __init__(self, api_url: str, token: str | None = None,
                 outbox_token: str | None = None, process_token: str | None = None,
                 timeout: float = 30):
        self._api_url = _normalize_url(api_url)
        self._token = _token(token)
        self._outbox_token = _token(outbox_token)
        self._process_token = _token(process_token)
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 300):
            raise APIError("Timeout must be greater than zero and at most 300 seconds.")
        self.timeout = float(timeout)
        parsed = urlsplit(self._api_url)
        self._scheme, self._host, self._port = parsed.scheme, parsed.hostname, parsed.port
        self._base_path = parsed.path

    @property
    def api_url(self) -> str:
        return self._api_url

    @classmethod
    def from_env(cls, api_url: str | None = None, timeout: float = 30) -> RespawnedClient:
        url = _normalize_url(api_url if api_url is not None else os.environ.get("RESPAWNED_API_URL", DEFAULT_API_URL))
        tokens = [os.environ.get(name) for name in TOKEN_ENVIRONMENTS]
        # Empty .env.example fields retain local discovery. Any actual credential
        # opts into only its explicit scope; restricted callers never gain authority.
        if not any(tokens):
            try:
                tokens[0] = read_local_token(url)
            except (OSError, ValueError, TypeError, AttributeError):
                raise APIError("Cannot read this engine's private local connection. Restart the local engine or set RESPAWNED_REVIEW_TOKEN.") from None
        return cls(url, token=tokens[0], outbox_token=tokens[1], process_token=tokens[2], timeout=timeout)

    def import_records(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", WORKFLOW_PREFIX + "/import", payload)

    def sync(self, limit: int = 10, dry_run: bool = False) -> dict[str, Any]:
        return self._request("POST", WORKFLOW_PREFIX + "/sync", {"limit": limit, "dry_run": dry_run})

    def queue(self) -> dict[str, Any]:
        return self._request("GET", WORKFLOW_PREFIX + "/queue")

    def records(self, limit: int = 50, offset: int = 0, workspace_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", WORKFLOW_PREFIX + "/records?" + self._query(limit=limit, offset=offset, workspace_id=workspace_id))

    def get_record(self, id: str) -> dict[str, Any]:
        return self._request("GET", WORKFLOW_PREFIX + f"/records/{_identifier(id)}")

    def draft(self, record_id: str, body: str | None = None) -> dict[str, Any]:
        return self._request("POST", WORKFLOW_PREFIX + f"/records/{_identifier(record_id)}/draft", None if body is None else {"body": body})

    def draft_candidate(self, candidate_id: str, body: str | None = None) -> dict[str, Any]:
        return self._request("POST", WORKFLOW_PREFIX + f"/candidates/{_identifier(candidate_id)}/draft", None if body is None else {"body": body})

    def get_draft(self, id: str) -> dict[str, Any]:
        return self._request("GET", WORKFLOW_PREFIX + f"/drafts/{_identifier(id)}")

    def edit_draft(self, id: str, body: str, review_token: str) -> dict[str, Any]:
        return self._request("POST", WORKFLOW_PREFIX + f"/drafts/{_identifier(id)}/edit", {"body": body, "review_token": review_token})

    def approve_draft(self, id: str, review_token: str) -> dict[str, Any]:
        return self._request("POST", WORKFLOW_PREFIX + f"/drafts/{_identifier(id)}/approve", {"review_token": review_token})

    def reject_draft(self, id: str, review_token: str) -> dict[str, Any]:
        return self._request("POST", WORKFLOW_PREFIX + f"/drafts/{_identifier(id)}/reject", {"review_token": review_token})

    def inbox(self, limit: int = 50, workspace_id: str | None = None) -> dict[str, Any]:
        return self._request("GET", WORKFLOW_PREFIX + "/inbox?" + self._query(limit=limit, workspace_id=workspace_id))

    def pending_outbox(self, limit: int = 50) -> dict[str, Any]:
        return self._request("GET", "/v1/outbox/pending?" + self._query(limit=limit), scope="outbox")

    def get_outbox(self, id: int) -> dict[str, Any]:
        return self._request("GET", f"/v1/outbox/{_identifier(id)}", scope="outbox")

    def record_receipt(self, id: int, receipt: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/v1/outbox/{_identifier(id)}/receipt", receipt, scope="outbox")

    def export_outbox(self, format: Literal["json", "csv"] = "json", workspace_id: str | None = None) -> dict[str, Any] | str:
        if format not in {"json", "csv"}:
            raise APIError("Export format must be json or csv.")
        return self._request("GET", WORKFLOW_PREFIX + "/outbox/export?" + self._query(format=format, workspace_id=workspace_id), csv=format == "csv")

    def process(self, limit: int = 10) -> dict[str, Any]:
        return self._request("POST", "/v1/process", {"limit": limit}, scope="process")

    @staticmethod
    def _query(**values: Any) -> str:
        return urlencode({key: value for key, value in values.items() if value is not None})

    def _authority(self, scope: str) -> str:
        token = {"outbox": self._outbox_token, "process": self._process_token}.get(scope) or self._token
        if token:
            return token
        if scope == "outbox":
            raise APIError("Set RESPAWNED_OUTBOX_TOKEN or RESPAWNED_REVIEW_TOKEN for outbox access.")
        if scope == "process":
            raise APIError("Set RESPAWNED_PROCESS_TOKEN or RESPAWNED_REVIEW_TOKEN for processing.")
        raise APIError("Engine access is required. Start a local engine with respawned ui, or set RESPAWNED_REVIEW_TOKEN.")

    def _safe_detail(self, value: Any, fallback: str) -> str:
        if isinstance(value, list):
            value = "; ".join(item["msg"] for item in value if isinstance(item, dict) and isinstance(item.get("msg"), str))
        if not isinstance(value, str) or not value:
            value = fallback
        for token in (self._token, self._outbox_token, self._process_token):
            if token:
                value = value.replace(token, "[redacted]")
        value = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
        return " ".join(value.split())[:240]

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 *, scope: str = "workflow", csv: bool = False) -> Any:
        token = self._authority(scope)
        mutation = method not in {"GET", "HEAD"}
        body = None
        if payload is not None:
            if not isinstance(payload, dict):
                raise APIError("Request data must be a JSON object.")
            try:
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError, UnicodeError):
                raise APIError("Request data must contain JSON values.") from None
            if len(body) > MAX_REQUEST_BYTES:
                raise APIError("Request data exceeds 2 MB. Split imports into smaller batches.")
        target = self._base_path + path
        if len(target.encode("utf-8")) > MAX_URL_BYTES:
            raise APIError("Request URL is too long. Use a shorter record or workspace ID.")
        headers = {"Authorization": f"Bearer {token}", "Accept": "text/csv" if csv else "application/json", "Accept-Encoding": "identity"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        kind = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        connection = kind(self._host, self._port, timeout=self.timeout)
        deadline = time.monotonic() + self.timeout
        timer = None
        status = None
        try:
            connection.connect()
            transport = connection.sock
            assert transport is not None

            def budget() -> None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                transport.settimeout(remaining)

            def expire() -> None:
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            budget()
            timer = Timer(max(0, deadline - time.monotonic()), expire)
            timer.daemon = True
            timer.start()
            connection.request(method, target, body=body, headers=headers)
            budget()
            response = connection.getresponse()
            status = response.status
            try:
                if 300 <= status < 400:
                    raise APIError(f"HTTP {status}: redirects are refused. Check RESPAWNED_API_URL.", status, mutation)
                if response.getheader("Content-Encoding", "identity").strip().lower() != "identity":
                    raise APIError("The engine returned an unsupported content encoding.", status, mutation)
                if response.length is not None and response.length > MAX_RESPONSE_BYTES:
                    raise APIError("The engine response exceeds 4 MiB. Request a smaller page.", status, mutation)
                raw = bytearray()
                while not response.isclosed():
                    budget()
                    chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise APIError("The engine response exceeds 4 MiB. Request a smaller page.", status, mutation)
                if response.length is not None and response.length > 0:
                    raise APIError("The engine response ended early. Check the current state before retrying.", status, mutation)
                if 200 <= status < 300 and csv:
                    if response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "text/csv":
                        raise APIError("The engine did not return CSV.", status, mutation)
                    try:
                        return bytes(raw).decode("utf-8")
                    except UnicodeError:
                        raise APIError("The engine returned unreadable CSV.", status, mutation) from None
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise APIError(f"HTTP {status}: the engine did not return JSON.", status, mutation) from None
                if not 200 <= status < 300:
                    detail = result.get("detail") if isinstance(result, dict) else None
                    fallback = {401: "Engine access was not accepted. Check the configured token.", 403: "This credential cannot perform this operation.", 404: "Endpoint not found. Check the engine URL and update the server to Respawned 2.0.", 409: "The record changed. Fetch the current state before continuing.", 422: "The request fields were not accepted."}.get(status, "The engine could not complete this request.")
                    raise APIError(f"HTTP {status}: {self._safe_detail(detail, fallback)}", status, mutation and status >= 500)
                if not isinstance(result, dict):
                    raise APIError("The engine response must be a JSON object.", status, mutation)
                return result
            finally:
                response.close()
        except (OSError, http.client.HTTPException):
            message = ("Request failed or timed out. Check the engine state before retrying; no retry was made." if mutation
                       else "Could not reach the engine. Start respawned ui or respawned serve, or check RESPAWNED_API_URL.")
            raise APIError(message, status, mutation) from None
        finally:
            if timer is not None:
                timer.cancel()
            connection.close()
