"""Private local CLI discovery must not widen an engine's authority."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from respawned.local_connection import (
    FORMAT, _check_owned, _state_directory, _windows_security,
    publish_local_token, read_local_token,
)


class LocalConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="respawned-private-test-")
        self.root = Path(self.temporary.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.root.name.startswith("respawned-private-test-"))
        self.previous = os.environ.get("RESPAWNED_STATE_DIR")
        os.environ["RESPAWNED_STATE_DIR"] = str(self.root)
        self.url = "http://127.0.0.1:18379"

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("RESPAWNED_STATE_DIR", None)
        else:
            os.environ["RESPAWNED_STATE_DIR"] = self.previous
        # The exact owned root was resolved and verified in setUp.
        self.temporary.cleanup()

    def test_publish_is_private_and_removed_on_normal_exit(self):
        with publish_local_token(self.url, "disposable-test-capability") as path:
            self.assertEqual(read_local_token(self.url), "disposable-test-capability")
            self.assertEqual(read_local_token(self.url + "/"), "disposable-test-capability")
            _check_owned(path.parent, directory=True)
            _check_owned(path)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertFalse(path.exists())

    def test_local_capability_is_not_available_to_other_targets(self):
        with publish_local_token(self.url, "disposable-test-capability"):
            for url in (
                "https://respawned.example.com", "http://localhost:18379",
                "http://127.0.0.2:18379", "http://127.0.0.1:18380",
                self.url + "/proxy", self.url + "?next=external", self.url + "#fragment",
                "http://@127.0.0.1:18379",
            ):
                with self.subTest(url=url):
                    self.assertIsNone(read_local_token(url))

    def test_default_port_forms_share_one_canonical_origin(self):
        origin = "http://127.0.0.1"
        forms = (origin, origin + "/", origin + ":80", origin + ":80/")
        for published_url in forms:
            with self.subTest(published_url=published_url):
                with publish_local_token(published_url, "default-port-capability") as path:
                    self.assertEqual(path.name, "80.json")
                    self.assertEqual(json.loads(path.read_text())["url"], origin)
                    for requested_url in forms:
                        with self.subTest(requested_url=requested_url):
                            self.assertEqual(read_local_token(requested_url), "default-port-capability")
                self.assertFalse(path.exists())

    def test_existing_default_port_origin_can_be_read_and_republished(self):
        origin = "http://127.0.0.1"
        with publish_local_token(origin, "first-capability") as path:
            payload = json.loads(path.read_text())
            payload["url"] = origin + ":80/"
            path.write_text(json.dumps(payload))
            self.assertEqual(read_local_token(origin), "first-capability")
            with publish_local_token(origin + "/", "replacement-capability") as replacement:
                self.assertEqual(replacement, path)
                self.assertEqual(json.loads(path.read_text())["url"], origin)
                self.assertEqual(read_local_token(origin + ":80"), "replacement-capability")
        self.assertFalse(path.exists())

    def test_default_port_normalization_does_not_allow_other_targets(self):
        with publish_local_token("http://127.0.0.1", "default-port-capability") as path:
            before = path.read_bytes()
            for url in (
                "https://127.0.0.1:80", "http://localhost:80", "http://127.0.0.2:80",
                "http://127.0.0.1/proxy", "http://127.0.0.1:80/proxy", "http://127.0.0.1//",
                "http://127.0.0.1?next=external", "http://127.0.0.1#fragment", "http://user@127.0.0.1",
            ):
                with self.subTest(url=url):
                    self.assertIsNone(read_local_token(url))
                    with self.assertRaises(ValueError):
                        with publish_local_token(url, "must-not-be-written"):
                            self.fail("Another target was accepted")
                    self.assertEqual(path.read_bytes(), before)

    def test_cleanup_does_not_remove_another_instance(self):
        with publish_local_token(self.url, "disposable-test-capability") as path:
            payload = json.loads(path.read_text())
            payload["instance"] = "replacement-instance"
            path.write_text(json.dumps(payload))
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text())["instance"], "replacement-instance")

    def test_cleanup_leaves_changed_unreadable_data(self):
        with publish_local_token(self.url, "disposable-test-capability") as path:
            path.write_text("{changed")
        self.assertEqual(path.read_text(), "{changed")

    def test_unrelated_data_is_not_replaced(self):
        with publish_local_token(self.url, "disposable-test-capability") as path:
            payload = {"format": "unrelated", "instance": "unrelated"}
            path.write_text(json.dumps(payload))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            with publish_local_token(self.url, "replacement"):
                self.fail("Unrelated data was overwritten")
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_json_shapes_are_rejected(self):
        with publish_local_token(self.url, "disposable-test-capability") as path:
            original = path.read_text()
            try:
                for invalid in ("[]", "null", '"string"', '{"format":"other"}'):
                    path.write_text(invalid)
                    with self.assertRaises(ValueError):
                        read_local_token(self.url)
            finally:
                path.write_text(original)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_broad_permissions_are_rejected(self):
        with publish_local_token(self.url, "disposable-test-capability") as path:
            try:
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    read_local_token(self.url)
            finally:
                path.chmod(0o600)
            try:
                path.parent.chmod(0o755)
                with self.assertRaises(ValueError):
                    read_local_token(self.url)
            finally:
                path.parent.chmod(0o700)

    @unittest.skipIf(os.name == "nt", "POSIX symlink creation")
    def test_symlink_storage_is_not_followed(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        _state_directory().symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            with publish_local_token(self.url, "must-not-be-written"):
                self.fail("A symlink directory was accepted")
        self.assertEqual(list(target.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows access lists")
    def test_windows_read_rejects_an_everyone_grant(self):
        with publish_local_token(self.url, "disposable-test-capability") as path:
            try:
                subprocess.run(["icacls", str(path), "/grant", "*S-1-1-0:R"],
                               capture_output=True, text=True, check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                with self.assertRaises(ValueError):
                    read_local_token(self.url)
            finally:
                _windows_security(path, secure=True)
            self.assertEqual(read_local_token(self.url), "disposable-test-capability")


if __name__ == "__main__":
    unittest.main()
