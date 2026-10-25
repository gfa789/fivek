import os
os.environ["TAILSCALE_OWNER"] = "owner@example.com"
"""Offline checks against the real app using temporary, empty storage."""
import http.client
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fivek.config import Config
from fivek.webapp import LocalServer
from private_proxy import PrivateProxy


class PrivateAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.app = LocalServer(Config(data_dir=Path(cls.temp.name)), 0)
        cls.proxy = PrivateProxy(0, cls.app.server_port)
        cls.threads = []
        for server in (cls.app, cls.proxy):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.proxy, cls.app):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join()
        cls.temp.cleanup()

    def request(self, method="GET", path="/", headers=None, body=None):
        client = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=5)
        client.request(method, path, body, headers or {})
        response = client.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        client.close()
        return result

    def owner(self, **extra):
        return {"Tailscale-User-Login": "owner@example.com", "Host": "pc.example.ts.net", **extra}

    def test_missing_identity_blocks_all_app_paths(self):
        for path in ("/", "/api/status", "/report.json", "/training-plan.ics", "/assets/dashboard.js"):
            self.assertEqual(self.request(path=path)[0], 403)

    def test_wrong_identity_blocks_get_and_post(self):
        for method in ("GET", "HEAD", "POST"):
            self.assertEqual(self.request(method, headers={"Tailscale-User-Login": "other@example.com"})[0], 403)

    def test_owner_get_rewrites_host_and_preserves_app_headers(self):
        status, body, headers = self.request(headers=self.owner())
        self.assertEqual(status, 200)
        self.assertIn(self.app.app.token.encode(), body)
        self.assertIn("Content-Security-Policy", headers)

    def test_head_has_no_body(self):
        status, body, _ = self.request("HEAD", headers=self.owner())
        self.assertEqual((status, body), (200, b""))

    def test_owner_post_requires_existing_csrf_token(self):
        headers = self.owner(**{"Content-Type": "application/json"})
        self.assertEqual(self.request("POST", "/api/cancel", headers, b"{}")[0], 403)
        headers["X-FiveK-Token"] = self.app.app.token
        self.assertEqual(self.request("POST", "/api/cancel", headers, b"{}")[0], 202)

    def test_duplicate_identity_rejected(self):
        client = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=5)
        client.putrequest("GET", "/")
        client.putheader("Tailscale-User-Login", "owner@example.com")
        client.putheader("Tailscale-User-Login", "other@example.com")
        client.endheaders()
        response = client.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        client.close()

    def test_oversized_or_chunked_body_rejected(self):
        # Reject an oversized declaration immediately, without waiting for its body.
        self.assertEqual(self.request("POST", headers=self.owner(**{"Content-Length": "8193"}))[0], 413)
        self.assertEqual(self.request("POST", headers=self.owner(**{"Transfer-Encoding": "chunked"}))[0], 400)

    def test_listeners_remain_loopback_only(self):
        self.assertEqual(self.proxy.server_address[0], "127.0.0.1")
        self.assertEqual(self.app.server_address[0], "127.0.0.1")


class MountedAccessTests(PrivateAccessTests):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {"FIVEK_BASE_PATH": "/running/"})
        cls.environment.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.environment.stop()

    def test_owner_get_rewrites_host_and_preserves_app_headers(self):
        status, body, headers = self.request(path="/", headers=self.owner())
        self.assertEqual(status, 200)
        self.assertIn(self.app.app.token.encode(), body)
        for target in (b'/running/assets/dashboard.js', b'/running/assets/dashboard.css'):
            self.assertIn(target, body)
        self.assertNotIn(b'__BASE_PATH__', body)
        self.assertIn("Content-Security-Policy", headers)

    def test_owner_post_requires_existing_csrf_token(self):
        headers = self.owner(**{"Content-Type": "application/json"})
        self.assertEqual(self.request("POST", "/api/cancel", headers, b"{}")[0], 403)
        headers["X-FiveK-Token"] = self.app.app.token
        self.assertEqual(self.request("POST", "/api/cancel", headers, b"{}")[0], 202)

    def test_prefix_is_stripped_by_tailscale(self):
        for path in ("/api/status?check=1", "/assets/dashboard.js", "/assets/dashboard.css", "/running/api/status") :
            self.assertEqual(self.request(path=path, headers=self.owner())[0], 200)

    def test_base_path_validation(self):
        from fivek.config import base_path
        for value in ('//', '/running', '/<script>/', '/running/?x=1'):
            with patch.dict(os.environ, {"FIVEK_BASE_PATH": value}):
                with self.assertRaises(ValueError):
                    base_path()


if __name__ == "__main__":
    unittest.main()

