import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def site_server():
    """Serve tests/fixtures on 127.0.0.1:<random>. Extra routes: /go (302 to localhost), /big (3 MB html)."""
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    alt_base = {}

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_HEAD(self):
            if self.path == "/headfail":  # some servers 404 a HEAD while GET works
                self.send_response(404)
                self.end_headers()
                return
            return super().do_HEAD()

        def do_GET(self):
            if self.path == "/headfail":
                body = b"<html><body>ok</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/go":
                self.send_response(302)
                self.send_header("Location", f"{alt_base['url']}/site/index.html")
                self.end_headers()
                return
            if self.path == "/big":
                body = b"<html><body><p>" + b"x" * (3 * 1024 * 1024) + b"</p></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            return super().do_GET()

    import socketserver

    class FastServer(ThreadingHTTPServer):
        def server_bind(self):  # skip the slow reverse-DNS getfqdn() in HTTPServer.server_bind
            socketserver.TCPServer.server_bind(self)
            self.server_name, self.server_port = "localhost", self.server_address[1]

    ThreadingHTTPServer = FastServer  # noqa: N806
    handler = functools.partial(Handler, directory=str(FIXTURES))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    servers = [httpd]
    # A second "domain" for redirect tests: 127.0.0.2 where the OS allows it (Windows, Linux),
    # else localhost (slower on Windows because ::1 is tried first).
    try:
        alt = ThreadingHTTPServer(("127.0.0.2", 0), handler)
        servers.append(alt)
        alt_base["url"] = f"http://127.0.0.2:{alt.server_address[1]}"
    except OSError:
        alt_base["url"] = f"http://localhost:{httpd.server_address[1]}"
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    for s in servers:
        s.shutdown()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test gets its own data dir so nothing touches ~/.prospectscout."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PROSPECTSCOUT_HOME", str(home))
    return home
