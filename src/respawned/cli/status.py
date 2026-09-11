"""Check public engine diagnostics using HTTP, without local credentials or state."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import os

from respawned.cli.http import configure_connection, print_json, run
from respawned.client import DEFAULT_API_URL, RespawnedClient


def status_exit_code(result: dict) -> int:
    if result["health"]["status"] != "ok":
        return 1
    if result["compatibility"] == "different_major":
        return 3
    if result["readiness"]["status"] != "ready":
        return 1
    if result["compatibility"] == "unknown":
        return 4
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respawned status", description=__doc__)
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print diagnostic results as JSON")
    configure_connection(parser)
    args = parser.parse_args(argv)

    def status():
        # Diagnostics must still work with stale local capabilities or malformed
        # credential environment variables. The public reads never send tokens.
        url = args.api_url if args.api_url is not None else os.environ.get("RESPAWNED_API_URL", DEFAULT_API_URL)
        result = RespawnedClient(url, timeout=args.timeout).status()
        if args.json_output:
            print_json(result)
        else:
            print(f"Engine: {result['api_url']}")
            print(f"Client version: {result['client_version']}")
            print(f"Engine version: {result['engine_version'] or 'not reported'}")
            for name in ("health", "readiness"):
                check = result[name]
                print(f"{name.capitalize()}: {check['status']}")
                if check["detail"]:
                    print(f"  {check['detail']}")
            print(f"Compatibility: {result['compatibility']}")
            print(result["compatibility_detail"])
            print("Workflow access: not checked")
        return status_exit_code(result)

    return run(status)


if __name__ == "__main__":
    raise SystemExit(main())
