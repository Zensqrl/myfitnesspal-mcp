"""Offline browser test, run inside the browser image under Xvfb. No MFP login."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading

from myfitnesspal_mcp.auth import SESSION_COOKIE
from myfitnesspal_mcp.browser_service import BrowserOwner


class Login(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        # Chromium treats localhost as potentially trustworthy.
        self.send_header("Set-Cookie", f"{SESSION_COOKIE}=synthetic; Secure; HttpOnly; Path=/")
        self.end_headers()
        self.wfile.write(b'<html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
                        b'<title>Synthetic login</title></head><body><h1>Connected fixture</h1></body></html>')

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Login)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix="mfp-browser-test-") as directory:
        owner = BrowserOwner(Path(directory))
        assert owner.login_url == "https://www.myfitnesspal.com/account/login"
        owner.login_url = f"http://localhost:{server.server_port}/"
        try:
            lease = owner.start()["session_id"]
            page = owner.context.pages[0]
            assert page.viewport_size == {"width": 480, "height": 900}
            assert page.evaluate("window.innerWidth") == 480
            assert page.evaluate("navigator.maxTouchPoints") > 0
            assert "Mobile" in page.evaluate("navigator.userAgent")
            assert owner.harvest(lease)["cookies"][SESSION_COOKIE] == "synthetic"
            owner.commit(lease)
            assert owner.refresh()["cookies"][SESSION_COOKIE] == "synthetic"
            owner.forget()
            assert not (Path(directory) / "active").exists()
        finally:
            owner.cancel()
    print("Real Chromium login, cookie harvest, profile promotion, refresh and disconnect passed.")
finally:
    server.shutdown()
