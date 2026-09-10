"""Argument and file handling shared by the HTTP command clients."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile

from respawned.client import APIError, RespawnedClient


def timeout_seconds(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Timeout must be a number from 1 to 300 seconds") from exc
    if not math.isfinite(number) or not 1 <= number <= 300:
        raise argparse.ArgumentTypeError("Timeout must be a number from 1 to 300 seconds")
    return number


def configure_connection(parser: argparse.ArgumentParser, *, defaults: bool = True) -> None:
    parser.add_argument("--api-url", default=None if defaults else argparse.SUPPRESS,
                        help="Engine URL (default: RESPAWNED_API_URL or http://127.0.0.1:8000)")
    parser.add_argument("--timeout", type=timeout_seconds, default=180 if defaults else argparse.SUPPRESS,
                        help="HTTP request timeout in seconds, 1..300 (default: 180)")


def client_from_args(args) -> RespawnedClient:
    return RespawnedClient.from_env(api_url=args.api_url, timeout=args.timeout)


def print_json(value) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    stream = sys.stdout
    binary = getattr(stream, "buffer", None)
    if binary is None:
        stream.write(text)
    else:
        # Redirected Windows stdout can use an ANSI code page. JSON consumers
        # receive UTF-8 without changing the caller's stream configuration.
        stream.flush()
        binary.write(text.encode("utf-8"))
        binary.flush()


def read_text(path: str | Path, *, limit: int = 2_000_000) -> str:
    try:
        if str(path) == "-":
            source = getattr(sys.stdin, "buffer", sys.stdin)
            raw = source.read(limit + 1)
        else:
            with Path(path).open("rb") as source:
                raw = source.read(limit + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if len(raw) > limit:
            raise APIError(f"Input exceeds {limit:,} bytes")
        return raw.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise APIError(f"Cannot read UTF-8 input from {path}") from exc


def read_json(path: str | Path, *, limit: int = 2_000_000):
    try:
        return json.loads(read_text(path, limit=limit))
    except ValueError as exc:
        raise APIError("Input must be valid JSON") from exc


def write_output(path: Path, content: str) -> None:
    """A failed download or write must not replace an existing export."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".respawned-export-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(action) -> int:
    try:
        result = action()
        return 0 if result is None else result
    except (APIError, OSError) as exc:
        print(f"respawned: {exc}", file=sys.stderr)
        if getattr(exc, "ambiguous", False):
            print("The result is unknown. Inspect the engine before retrying this write.", file=sys.stderr)
        return 1
