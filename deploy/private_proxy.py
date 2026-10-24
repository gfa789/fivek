"""Loopback bridge for Tailscale Serve; checks the identity Serve supplies."""
from __future__ import annotations

import argparse
import os
import http.client
import socket
from pathlib import Path
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from fivek.config import base_path

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade"}


class PrivateProxy(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def __init__(self, port=8876, upstream_port=8765, owner=None, mount=None):
        self.upstream_port, self.owner = upstream_port, owner or os.environ["TAILSCALE_OWNER"]
        self.mount = mount if mount is not None else base_path()
        super().__init__(("127.0.0.1", port), Handler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Don't record identity, URLs, health data, or verification codes.

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def reject(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def page(self, status, body="", *, location=None):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def forward(self):
        identities = self.headers.get_all("Tailscale-User-Login", [])
        if identities != [self.server.owner]:
            return self.reject(403)
        if not self.path.startswith("/") or self.path.startswith("//"):
            return self.reject(400)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1 or self.headers.get("Transfer-Encoding"):
            return self.reject(400)
        try:
            length = int(lengths[0]) if lengths else 0
            if not 0 <= length <= 8192:
                return self.reject(413)
            body = self.rfile.read(length) if length else None
            if body is not None and len(body) != length:
                return self.reject(400)
        except (ValueError, TimeoutError):
            return self.reject(400)
        forward_path = self.path
        if self.server.mount != "/":
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                if self.command not in {"GET", "HEAD"}:
                    return self.reject(405)
                return self.page(200, Path(__file__).with_name("homepage.html").read_text(encoding="utf-8"))
            if parsed.path == self.server.mount.rstrip("/"):
                destination = self.server.mount + ("?" + parsed.query if parsed.query else "")
                return self.page(308, location=destination)
            if not parsed.path.startswith(self.server.mount):
                return self.reject(404)
            forward_path = self.path[len(self.server.mount)-1:]
        connection_headers = {s.strip().lower() for s in self.headers.get("Connection", "").split(",")}
        excluded = HOP_HEADERS | connection_headers | {"host", "content-length"}
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() not in excluded and not key.lower().startswith("tailscale-")}
        headers["Host"] = f"127.0.0.1:{self.server.upstream_port}"
        upstream = http.client.HTTPConnection("127.0.0.1", self.server.upstream_port, timeout=60)
        try:
            upstream.request(self.command, forward_path, body=body, headers=headers)
            response = upstream.getresponse()
        except (OSError, http.client.HTTPException):
            upstream.close()
            return self.reject(502)
        try:
            self.send_response(response.status)
            excluded_response = HOP_HEADERS | {s.strip().lower() for s in response.getheader("Connection", "").split(",")}
            for key, value in response.getheaders():
                if key.lower() not in excluded_response:
                    self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                while chunk := response.read(65536):
                    self.wfile.write(chunk)
        except (OSError, http.client.HTTPException):
            pass
        finally:
            upstream.close()

    do_GET = do_HEAD = do_POST = forward


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--upstream-port", type=int, default=8765)
    args = parser.parse_args()
    with PrivateProxy(args.port, args.upstream_port) as server:
        server.serve_forever()
