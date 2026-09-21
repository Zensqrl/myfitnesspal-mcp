"""Rebuild/recreate only the browser service using existing Docker access.

Run on Ubuntu from /opt/myfitnesspal-mcp/app after updating its source files.
Does not read root-owned deployment files or modify credentials/database state.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile


root = Path("/opt/myfitnesspal-mcp/app")
if Path(__file__).resolve().parent.parent != root:
    raise SystemExit("Run the deployed script under /opt/myfitnesspal-mcp/app/scripts")


def run(*args, capture=False, env=None):
    return subprocess.run(args, check=True, text=True, env=env,
                          stdout=subprocess.PIPE if capture else None).stdout


# Inspect only configuration needed for Compose interpolation, never print it.
raw = run("docker", "inspect", "--format", "{{json .Config.Env}}",
          "myfitnesspal-mcp-gateway-1", capture=True)
settings = dict(item.split("=", 1) for item in json.loads(raw) if "=" in item)
env = os.environ.copy()
for name in ("MFP_HOST", "MFP_HA_IP", "MFP_ADMIN_CIDR"):
    env[name] = settings[name]
env.update(MFP_ROOT="/opt/myfitnesspal-mcp", MFP_BIND_IP="127.0.0.1")
old = run("docker", "inspect", "--format", "{{.Image}}",
          "myfitnesspal-mcp-browser-1", capture=True).strip()
run("docker", "image", "tag", old, "myfitnesspal-mcp-browser:before-mobile")
run("docker", "build", "-f", str(root / "deploy/Dockerfile"), "--target", "browser",
    "-t", "myfitnesspal-mcp-browser:mobile-login", str(root))
with tempfile.TemporaryDirectory(prefix="mfp-browser-update-") as temporary:
    override = Path(temporary) / "override.yaml"
    # Gateway is NOT recreated. Omit its root-only env file from parsing while
    # using --no-deps to leave both gateway and app running unchanged.
    def deploy(image):
        override.write_text("services:\n  gateway:\n    env_file: !reset []\n"
                            "  browser:\n    image: " + image + "\n")
        run("docker", "compose", "-p", "myfitnesspal-mcp", "-f",
            str(root / "deploy/compose.yaml"), "-f", str(override), "up", "-d",
            "--no-deps", "--no-build", "--wait", "--wait-timeout", "90", "browser", env=env)
    try:
        deploy("myfitnesspal-mcp-browser:mobile-login")
    except subprocess.CalledProcessError:
        deploy("myfitnesspal-mcp-browser:before-mobile")
        raise
print("Mobile browser deployed; app, gateway and saved state retained.")
