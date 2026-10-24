"""Linux services can bind again immediately after serving a request."""
import os
import http.client
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from fivek.config import Config
from fivek.webapp import LocalServer


@unittest.skipIf(os.name == 'nt', 'Windows uses exclusive socket binding')
class LinuxRestartTests(unittest.TestCase):
    def test_immediate_restart_after_request(self):
        with TemporaryDirectory() as directory:
            config = Config(data_dir=Path(directory))
            first = LocalServer(config, 0)
            port = first.server_port
            thread = threading.Thread(target=first.serve_forever, daemon=True)
            thread.start()
            try:
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                conn.request('GET', '/api/status')
                self.assertEqual(conn.getresponse().status, 200)
                conn.close()
            finally:
                first.shutdown(); first.server_close(); thread.join()
            second = LocalServer(config, port)
            second.server_close()
