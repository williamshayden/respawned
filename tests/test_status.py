"""Engine diagnostics work over HTTP without client database or credential access."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from respawned.client import APIError, RespawnedClient, TOKEN_ENVIRONMENTS
from respawned.cli.status import status_exit_code


SOURCE = Path(__file__).resolve().parents[1] / "src"
CLIENT_VERSION = version("respawned")


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="respawned-status-test-")
        self.root = Path(self.temporary.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.root.name.startswith("respawned-status-test-"))
        state = self.state = SimpleNamespace(
            requests=[], mode="normal", health_code=200, readiness_code=200,
            health={"status": "ok", "engine_version": CLIENT_VERSION},
            readiness={"status": "ready"},
        )

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                state.requests.append((self.path, dict(self.headers)))
                if state.mode == "redirect":
                    self.send_response(307)
                    self.send_header("Location", state.url + "/trap")
                    self.end_headers()
                    return
                if state.mode == "slow":
                    self.send_response(200)
                    self.end_headers()
                    try:
                        for _ in range(20):
                            self.wfile.write(b" ")
                            self.wfile.flush()
                            time.sleep(0.05)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                if self.path in {"/healthz", "/engine/healthz"}:
                    code, payload = state.health_code, state.health
                elif self.path in {"/readyz", "/engine/readyz"}:
                    code, payload = state.readiness_code, state.readiness
                else:
                    code, payload = 404, {"detail": "Unknown test endpoint"}
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        state.url = f"http://127.0.0.1:{self.server.server_port}"
        self.url = state.url + "/engine"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def client(self, **kwargs):
        return RespawnedClient(self.url, timeout=1, **kwargs)

    def run_cli(self, *arguments, missing_tokens=False, api_url=None):
        environment = os.environ | {
            "PYTHONPATH": os.pathsep.join(filter(None, [str(SOURCE), os.environ.get("PYTHONPATH")])),
            "PYTHONDONTWRITEBYTECODE": "1",
            "RESPAWNED_API_URL": api_url or self.url,
            "RESPAWNED_STATE_DIR": str(self.root / "broken-state"),
            "DB_HOST": "never-connect.invalid", "DB_PORT": "not-a-port",
            "RESPAWNED_POLICY_PATH": "must-not-open.invalid",
            "RESPAWNED_MODEL_API_KEY": "private-model-test-secret",
        }
        for name in TOKEN_ENVIRONMENTS:
            environment.pop(name, None)
            if not missing_tokens:
                environment[name] = "invalid\nprivate-test-token"
        connections = self.root / "broken-state" / "connections"
        connections.mkdir(parents=True, exist_ok=True, mode=0o700)
        (connections / f"{self.server.server_port}.json").write_text("{invalid local capability", encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-m", "respawned", "status", "--timeout", "1", *arguments],
            cwd=self.root, env=environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", timeout=5,
        )

    def assert_no_credentials(self):
        for _path, headers in self.state.requests:
            self.assertNotIn("Authorization", headers)
            self.assertNotIn("Cookie", headers)

    def test_sdk_reports_ready_without_sending_any_configured_credentials(self):
        result = self.client(token="operator-secret", outbox_token="outbox-secret", process_token="process-secret").status()
        self.assertEqual(result, {
            "api_url": self.url, "client_version": CLIENT_VERSION, "engine_version": CLIENT_VERSION,
            "compatibility": "same_major",
            "compatibility_detail": "Client and engine satisfy the same-major compatibility policy.",
            "health": {"status": "ok", "detail": None},
            "readiness": {"status": "ready", "detail": None}, "workflow_access": "not_checked",
        })
        self.assertEqual(status_exit_code(result), 0)
        self.assertEqual([path for path, _headers in self.state.requests], ["/engine/healthz", "/engine/readyz"])
        self.assert_no_credentials()

    def test_compatibility_accepts_same_major_without_requiring_exact_minor_or_patch(self):
        with patch("respawned.client.package_version", return_value="2.1.0"):
            for engine_version in ("2.0.0", "2.99.7", "2.1.1", "2.2.0rc1", "2.1.0.post1"):
                with self.subTest(engine_version=engine_version):
                    self.state.health["engine_version"] = engine_version
                    result = self.client().status()
                    self.assertEqual(result["engine_version"], engine_version)
                    self.assertEqual(result["compatibility"], "same_major")
                    self.assertEqual(status_exit_code(result), 0)
            self.state.health["engine_version"] = "3.0.0"
            result = self.client().status()
            self.assertEqual(result["compatibility"], "different_major")
            self.assertIn("Update the client or engine", result["compatibility_detail"])
            self.assertEqual(status_exit_code(result), 3)

    def test_older_or_invalid_engine_versions_are_explicitly_unknown(self):
        for engine_version in (None, True, "", "2.1.0\nprivate-value", "x" * 65):
            with self.subTest(engine_version=engine_version):
                self.state.health["engine_version"] = engine_version
                result = self.client().status()
                self.assertIsNone(result["engine_version"])
                self.assertEqual(result["compatibility"], "unknown")
                self.assertIn("Older 2.0 engines", result["compatibility_detail"])
                self.assertEqual(result["readiness"]["status"], "ready")
                self.assertEqual(status_exit_code(result), 4)
                self.assertNotIn("private-value", json.dumps(result))

    def test_database_or_policy_readiness_failure_is_reported_separately(self):
        self.state.readiness_code = 503
        self.state.readiness = {"detail": "Database unavailable"}
        result = self.client().status()
        self.assertEqual(result["health"]["status"], "ok")
        self.assertEqual(result["readiness"], {"status": "not_ready", "detail": "HTTP 503: Database unavailable"})
        self.assertEqual(result["engine_version"], CLIENT_VERSION)
        self.assertEqual(status_exit_code(result), 1)
        self.assertEqual(len(self.state.requests), 2)

    def test_offline_engine_returns_diagnostics_without_retry(self):
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{reserved.getsockname()[1]}"
            result = RespawnedClient(url, timeout=0.2).status()
        self.assertEqual(result["health"]["status"], "unreachable")
        self.assertEqual(result["readiness"]["status"], "not_checked")
        self.assertIn("Start respawned ui or respawned serve", result["health"]["detail"])
        self.assertEqual(status_exit_code(result), 1)

    def test_health_failures_and_redirects_skip_readiness(self):
        for mode, code, body in (
            ("normal", 503, {"detail": "Server unavailable"}),
            ("normal", 200, {"status": "wrong-application"}),
            ("redirect", 307, {}),
        ):
            with self.subTest(mode=mode, code=code):
                self.state.mode, self.state.health_code, self.state.health = mode, code, body
                self.state.requests.clear()
                result = self.client().status()
                self.assertEqual(result["health"]["status"], "error")
                self.assertEqual(result["readiness"]["status"], "not_checked")
                self.assertEqual(status_exit_code(result), 1)
                self.assertEqual(len(self.state.requests), 1)

    def test_status_transport_has_a_deadline_and_does_not_retry(self):
        self.state.mode = "slow"
        started = time.monotonic()
        result = RespawnedClient(self.url, timeout=0.15).status()
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(status_exit_code(result), 1)
        self.assertEqual(result["readiness"]["status"], "not_checked")
        self.assertEqual(len(self.state.requests), 1)

    def test_public_transport_cannot_be_used_for_workflow_or_write_requests(self):
        client = self.client()
        for method, path in (("POST", "/healthz"), ("GET", "/v1/workflow/queue")):
            with self.subTest(method=method, path=path):
                with self.assertRaises(APIError):
                    client._request(method, path, public=True)
        with self.assertRaises(APIError):
            client.queue()
        self.assertEqual(self.state.requests, [])

    def test_cli_json_ignores_malformed_credentials_and_broken_local_state(self):
        for missing_tokens in (False, True):
            with self.subTest(missing_tokens=missing_tokens):
                result = self.run_cli("--json", missing_tokens=missing_tokens, api_url=self.state.url)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                report = json.loads(result.stdout)
                self.assertEqual(report["api_url"], self.state.url)
                self.assertEqual(report["client_version"], CLIENT_VERSION)
                self.assertEqual(report["engine_version"], CLIENT_VERSION)
                self.assertNotIn("private-", result.stdout)
        self.assert_no_credentials()

    def test_cli_exit_codes_preserve_structured_status_on_failures(self):
        cases = (
            ({"status": "ok"}, 200, {"status": "ready"}, 4),
            ({"status": "ok", "engine_version": "999.0.0"}, 200, {"status": "ready"}, 3),
            ({"status": "ok", "engine_version": CLIENT_VERSION}, 503, {"detail": "Database unavailable"}, 1),
        )
        for health, readiness_code, readiness, expected in cases:
            with self.subTest(expected=expected):
                self.state.health, self.state.readiness_code, self.state.readiness = health, readiness_code, readiness
                result = self.run_cli("--json")
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(status_exit_code(json.loads(result.stdout)), expected)
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            result = self.run_cli("--json", api_url=f"http://127.0.0.1:{reserved.getsockname()[1]}")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["health"]["status"], "unreachable")

    def test_cli_human_output_and_explicit_connection_override(self):
        result = self.run_cli("--api-url", self.url, api_url="https://must-not-contact.invalid")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"Engine: {self.url}", result.stdout)
        self.assertIn(f"Client version: {CLIENT_VERSION}", result.stdout)
        self.assertIn(f"Engine version: {CLIENT_VERSION}", result.stdout)
        self.assertIn("Readiness: ready", result.stdout)
        self.assertIn("same-major compatibility policy", result.stdout)
        self.assertIn("Workflow access: not checked", result.stdout)
        self.assertEqual(len(self.state.requests), 2)

    def test_status_imports_no_database_server_or_model_modules(self):
        script = (
            "import sys; from respawned.cli.status import main; "
            "assert not any(name.startswith(('sqlalchemy', 'psycopg2', 'fastapi', 'openai', "
            "'respawned.db', 'respawned.core', 'respawned.api', 'respawned.llm')) for name in sys.modules)"
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=self.root,
                                env=os.environ | {"PYTHONPATH": os.pathsep.join(filter(None, [str(SOURCE), os.environ.get("PYTHONPATH")]))},
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
