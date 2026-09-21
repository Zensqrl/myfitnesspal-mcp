"""Exercise the complete gateway/app/browser stack with isolated test state.

Run as root on Ubuntu after scripts/test-containers.sh has built both images.
No real credentials are used. Only loopback ports are published.
"""
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


source = Path(__file__).resolve().parents[1]
test_root = Path("/opt/myfitnesspal-mcp/test-runs")
test_root.mkdir(parents=True, exist_ok=True)
root = Path(tempfile.mkdtemp(prefix="compose-", dir=test_root))
project = "mfp-smoke-" + secrets.token_hex(4)
env = os.environ.copy()
env.update(MFP_ROOT=str(root), MFP_BIND_IP="127.0.0.1", MFP_HOST="127.0.0.1",
           MFP_HA_IP="127.0.0.1", MFP_ADMIN_CIDR="0.0.0.0/0")
compose = ["docker", "compose", "-p", project, "-f", str(source / "deploy/compose.yaml"),
           "-f", str(root / "override.yaml")]


def run(*args, capture=False):
    return subprocess.run(args, env=env, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None, timeout=180).stdout


try:
    for directory in ("secrets", "state/config", "state/data", "state/browser", "state/gateway", "state/gateway-config"):
        path = root / directory
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
        if directory in {"state/config", "state/data", "state/browser"}:
            os.chown(path, 10001, 10001)
    for name in ("browser_token", "gateway_token"):
        path = root / "secrets" / name
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
        os.chown(path, 10001, 10001)
    # Synthetic test password only; no user credential is put on a command line.
    hashed = run("docker", "run", "--rm", "caddy:2.10.2-alpine", "caddy", "hash-password",
                 "--plaintext", "synthetic-test-password", capture=True).strip()
    (root / "secrets/gateway.env").write_text(
        "MFP_ADMIN_HASH='" + hashed + "'\nMFP_GATEWAY_TOKEN=" +
        (root / "secrets/gateway_token").read_text() + "\n")
    (root / "override.yaml").write_text(
        "services:\n  app:\n    image: mfp-onboarding-app:test\n"
        # Synthetic lease only: never visit MyFitnessPal during this test.
        "    entrypoint: [python, -c]\n    command: " + json.dumps([
            "import time, uvicorn; from myfitnesspal_mcp.onboarding import Onboarding; "
            "from myfitnesspal_mcp.web import create_app; m = Onboarding(); "
            "m.begin = lambda: (setattr(m, 'browser_session', 'synthetic'), "
            "setattr(m, 'browser_deadline', time.monotonic() + 60)); "
            "m.cancel = lambda: setattr(m, 'browser_session', None); "
            "uvicorn.run(create_app(m), host='0.0.0.0', port=8484)"
        ]) + "\n"
        "  browser:\n    image: mfp-onboarding-browser:test\n")
    run(*compose, "up", "-d", "--no-build", "--wait", "--wait-timeout", "90")
    gateway = run(*compose, "ps", "-q", "gateway", capture=True).strip()
    inspection = json.loads(run("docker", "inspect", gateway, capture=True))[0]
    network = next(iter(inspection["NetworkSettings"]["Networks"].values()))
    # Engine versions differ in whether published loopback connections preserve
    # 127.0.0.1 or arrive from the bridge gateway. Neither admits peer containers.
    env["MFP_HA_IP"] = "127.0.0.1 " + network["Gateway"]
    run(*compose, "up", "-d", "--no-build", "gateway")
    certificate = root / "state/gateway/caddy/pki/authorities/local/root.crt"
    for _ in range(40):
        if certificate.exists():
            break
        time.sleep(0.25)
    context = ssl.create_default_context(cafile=str(certificate))
    authorization = "Basic " + base64.b64encode(b"admin:synthetic-test-password").decode()

    def request(path, *, method="GET", data=None, admin=True, headers=None):
        options = {"Authorization": authorization} if admin else {}
        options.update(headers or {})
        # Numeric IPs omit TLS SNI: exercise certificate selection and verify
        # both the trusted CA and IP subject alternative name without bypasses.
        req = Request("https://127.0.0.1:8443" + path, method=method,
                      data=json.dumps(data).encode() if data is not None else None, headers=options)
        try:
            return urlopen(req, context=context, timeout=5)
        except HTTPError as error:
            return error

    for _ in range(40):
        try:
            if request("/api/status").status == 200:
                break
        except OSError:
            pass
        time.sleep(0.25)
    assert request("/api/status", admin=False).status == 401
    assert request("/").status == 200
    assert request("/browser/vnc.html").status == 403
    assert request("/api/start", method="POST", data={}).status == 403

    def websocket_status(*, admin=True, origin="https://127.0.0.1:8443", expect_vnc=False):
        with socket.create_connection(("127.0.0.1", 8443), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname="127.0.0.1") as stream:
                headers = ["GET /browser/websockify HTTP/1.1", "Host: 127.0.0.1:8443",
                           "Connection: Upgrade", "Upgrade: websocket",
                           "Sec-WebSocket-Version: 13", "Sec-WebSocket-Protocol: binary",
                           "Sec-WebSocket-Key: " + base64.b64encode(os.urandom(16)).decode(),
                           "Origin: " + origin]
                if admin:
                    headers.append("Authorization: " + authorization)
                stream.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
                received = b""
                while b"\r\n\r\n" not in received:
                    chunk = stream.recv(4096)
                    assert chunk, "Connection closed before response headers"
                    received += chunk
                status = int(received.split(b" ", 2)[1])
                if expect_vnc:
                    assert status == 101, "Viewer upgrade was rejected"
                    payload = received.split(b"\r\n\r\n", 1)[1]
                    while b"RFB " not in payload and len(payload) < 4096:
                        chunk = stream.recv(4096)
                        assert chunk, "No VNC protocol greeting"
                        payload += chunk
                    assert b"RFB " in payload, "Missing VNC protocol greeting"
                return status

    assert websocket_status(admin=False) == 401
    assert websocket_status() == 403  # No active lease.
    action_headers = {"Origin": "https://127.0.0.1:8443", "X-MFP-Request": "1",
                      "Content-Type": "application/json"}
    assert request("/api/start", method="POST", data={}, headers=action_headers).status == 202
    for _ in range(40):
        if json.load(request("/api/status"))["browser_open"]:
            break
        time.sleep(0.25)
    assert request("/browser/vnc.html").status == 200
    assert websocket_status(origin="https://untrusted.invalid") == 403
    assert websocket_status(expect_vnc=True) == 101
    assert request("/api/cancel", method="POST", data={}, headers=action_headers).status == 202
    for _ in range(40):
        if not json.load(request("/api/status"))["browser_open"]:
            break
        time.sleep(0.25)
    assert websocket_status() == 403
    req = Request("http://localhost:8484/mcp", data=json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "smoke", "version": "1"}}
    }).encode(), headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urlopen(req, timeout=5) as response:
        assert response.status == 200
        assert b"serverInfo" in response.read()
    # A second container has a different source IP and cannot access the HA route.
    run(*compose, "exec", "-T", "app", "python", "-c",
        "import httpx; assert httpx.get('http://gateway:8484/mcp').status_code == 403")
    # Test sidecar liveness without opening any upstream website.
    run(*compose, "exec", "-T", "app", "python", "-c",
        "import httpx; assert httpx.get('http://browser:8090/healthz').status_code == 200")
    print("Complete Compose stack passed: TLS, admin auth, CSRF, viewer WebSocket/VNC greeting, lease/origin rejection, MCP and source-IP restriction.")
except Exception:
    # This stack contains synthetic state only. Production log export is not used.
    subprocess.run([*compose, "logs", "--tail", "35"], env=env, check=False)
    raise
finally:
    subprocess.run([*compose, "down", "--remove-orphans"], env=env, check=False)
    # This exact directory was created above solely for synthetic test state.
    if root.parent == test_root and root.name.startswith("compose-"):
        shutil.rmtree(root)
