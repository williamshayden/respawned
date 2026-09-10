#!/usr/bin/env python3
"""Read Respawned's outbox and record confirmed external sends. Python 3.12+."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import sys
from threading import Timer
import time
from typing import Any
from urllib.parse import urlsplit


MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_RECEIPT_BYTES = 16 * 1024


class OutboxError(Exception):
    """An invalid request, HTTP error, or uncertain transport result."""


class OutboxClient:
    """Explicit API calls; no sending, retries, work claims, or saved state."""

    def __init__(self, base_url: str | None = None, token: str | None = None,
                 *, timeout: float = 30):
        base_url = base_url or os.environ.get("RESPAWNED_API_URL", "http://127.0.0.1:8000")
        try:
            if any(ord(char) < 33 for char in base_url):
                raise ValueError
            parsed = urlsplit(base_url)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment):
                raise ValueError
            port = parsed.port
            if parsed.scheme == "http":
                is_local = parsed.hostname.lower() == "localhost"
                try:
                    is_local = is_local or ipaddress.ip_address(parsed.hostname).is_loopback
                except ValueError:
                    pass
                if not is_local:
                    raise ValueError
        except ValueError as exc:
            raise OutboxError("API URL must use HTTPS or loopback HTTP, without credentials, query, or fragment") from exc
        token = token if token is not None else os.environ.get("RESPAWNED_OUTBOX_TOKEN", "")
        if not token or any(ord(char) < 33 or ord(char) > 126 for char in token):
            raise OutboxError("Set RESPAWNED_OUTBOX_TOKEN to the server's outbox credential")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 300:
            raise OutboxError("Timeout must be greater than zero and at most 300 seconds")
        self._scheme, self._host, self._port = parsed.scheme, parsed.hostname, port
        self._base_path, self._token = parsed.path.rstrip("/"), token
        self.timeout = timeout

    @staticmethod
    def _id(value: int) -> int:
        if type(value) is not int or not 1 <= value <= 9223372036854775807:
            raise OutboxError("Outbox ID must be a positive 64-bit integer")
        return value

    def pending(self, limit: int = 50) -> dict[str, Any]:
        """Fetch approved pending snapshots. Fetching does not claim work."""
        if type(limit) is not int or not 1 <= limit <= 200:
            raise OutboxError("Limit must be an integer from 1 to 200")
        return self._request("GET", f"/v1/outbox/pending?limit={limit}")

    def get(self, outbox_id: int) -> dict[str, Any]:
        """Inspect a snapshot and its receipt, if recorded."""
        return self._request("GET", f"/v1/outbox/{self._id(outbox_id)}")

    def receipt(self, outbox_id: int, receipt: dict[str, Any]) -> dict[str, Any]:
        """Record an existing provider confirmation. This does not send."""
        if not isinstance(receipt, dict) or set(receipt) != {"sender", "provider_message_id", "sent_at"}:
            raise OutboxError("Receipt requires sender, provider_message_id, and sent_at only")
        return self._request("POST", f"/v1/outbox/{self._id(outbox_id)}/receipt", receipt)

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None
        if payload is not None:
            try:
                body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise OutboxError("Receipt must contain JSON values") from exc
            if len(body) > MAX_RECEIPT_BYTES:
                raise OutboxError("Receipt exceeds 16 KiB")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json",
                   "Accept-Encoding": "identity"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection_type = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        connection = connection_type(self._host, self._port, timeout=self.timeout)
        deadline = time.monotonic() + self.timeout
        timer = None
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
                # A peer trickling response headers must not keep the socket alive.
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            budget()
            timer = Timer(max(0, deadline - time.monotonic()), expire)
            timer.daemon = True
            timer.start()
            connection.request(method, self._base_path + path, body=body, headers=headers)
            budget()
            response = connection.getresponse()
            try:
                if 300 <= response.status < 400:
                    raise OutboxError(f"HTTP {response.status}: redirects are refused; check RESPAWNED_API_URL")
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise OutboxError("Server returned an unsupported content encoding")
                raw = bytearray()
                while not response.isclosed():
                    budget()
                    chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise OutboxError("Server response exceeds 4 MiB")
                try:
                    result = json.loads(raw)
                except (ValueError, UnicodeError) as exc:
                    raise OutboxError(f"HTTP {response.status}: server did not return JSON") from exc
                if not 200 <= response.status < 300:
                    detail = result.get("detail") if isinstance(result, dict) else None
                    if isinstance(detail, str):
                        detail = " ".join(detail.replace(self._token, "[redacted]").split())[:200]
                    else:
                        detail = response.reason
                    raise OutboxError(f"HTTP {response.status}: {detail}")
                if not isinstance(result, dict):
                    raise OutboxError("Server response must be a JSON object")
                return result
            finally:
                response.close()
        except (OSError, http.client.HTTPException) as exc:
            raise OutboxError("Request failed or timed out; outcome may be unknown. No retry was made") from exc
        finally:
            if timer is not None:
                timer.cancel()
            connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pending = commands.add_parser("pending", help="Fetch approved pending messages as JSON")
    pending.add_argument("--limit", type=int, default=50)
    get = commands.add_parser("get", help="Inspect one message and its receipt")
    get.add_argument("id", type=int)
    receipt = commands.add_parser("receipt", help="Record a confirmed send from a JSON file")
    receipt.add_argument("id", type=int)
    receipt.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    try:
        client = OutboxClient()
        if args.command == "pending":
            result = client.pending(args.limit)
        elif args.command == "get":
            result = client.get(args.id)
        else:
            try:
                with args.file.open("rb") as stream:
                    raw = stream.read(MAX_RECEIPT_BYTES + 1)
                if len(raw) > MAX_RECEIPT_BYTES:
                    raise OutboxError("Receipt file exceeds 16 KiB")
                data = json.loads(raw)
            except (OSError, ValueError, UnicodeError) as exc:
                raise OutboxError("Cannot read receipt file as JSON") from exc
            result = client.receipt(args.id, data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except OutboxError as exc:
        print(f"outbox: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
