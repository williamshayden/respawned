"""Portable JSON output for terminals, redirected streams, and text captures."""
import io
import json
import unittest
from unittest.mock import patch

from respawned.cli.http import print_json


class JSONOutputTests(unittest.TestCase):
    def test_redirected_json_is_utf8_without_reconfiguring_stdout(self):
        value = {"body": "東京 — café\nSecond line.", "approved": False}
        output = io.BytesIO()
        with io.TextIOWrapper(output, encoding="cp1252") as stream:
            with patch("sys.stdout", stream):
                print_json(value)
            self.assertEqual(stream.encoding, "cp1252")
            raw = output.getvalue()
            self.assertEqual(json.loads(raw.decode("utf-8")), value)
            self.assertIn("東京 — café".encode("utf-8"), raw)
            self.assertTrue(raw.endswith(b"\n"))

    def test_text_only_captures_keep_unicode_json(self):
        value = {"body": "東京 — café"}
        output = io.StringIO()
        with patch("sys.stdout", output):
            print_json(value)
        self.assertEqual(json.loads(output.getvalue()), value)
        self.assertIn(value["body"], output.getvalue())


if __name__ == "__main__":
    unittest.main()
