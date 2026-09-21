"""Deploy only the app with image rollback; run from the Ubuntu project root.

Uses existing Docker access without opening root-owned deployment env files.
Does not restart browser/gateway or change credentials or archive mounts.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path('/opt/myfitnesspal-mcp/app')
if Path(__file__).resolve().parent.parent != root:
    raise SystemExit('Run the deployed script under /opt/myfitnesspal-mcp/app/scripts')


def run(*args, capture=False, env=None):
    return subprocess.run(args, check=True, text=True, env=env,
                          stdout=subprocess.PIPE if capture else None).stdout


def settings(container):
    raw = run('docker', 'inspect', '--format', '{{json .Config.Env}}', container, capture=True)
    return dict(item.split('=', 1) for item in json.loads(raw) if '=' in item)


gateway = settings('myfitnesspal-mcp-gateway-1')
app = settings('myfitnesspal-mcp-app-1')
env = os.environ.copy()
for name in ('MFP_HOST', 'MFP_HA_IP', 'MFP_ADMIN_CIDR'):
    env[name] = gateway[name]
env.update(MFP_ROOT='/opt/myfitnesspal-mcp', MFP_BIND_IP='127.0.0.1',
           MFP_TIMEZONE=app.get('TZ', 'America/New_York'))
old = run('docker', 'inspect', '--format', '{{.Image}}',
          'myfitnesspal-mcp-app-1', capture=True).strip()
run('docker', 'image', 'tag', old, 'myfitnesspal-mcp-app:before-activity')
run('docker', 'build', '-f', str(root / 'deploy/Dockerfile'), '--target', 'app',
    '-t', 'myfitnesspal-mcp-app:activity-v1', str(root))
with tempfile.TemporaryDirectory(prefix='mfp-app-update-') as temporary:
    override = Path(temporary) / 'override.yaml'

    def deploy(image):
        override.write_text('services:\n  gateway:\n    env_file: !reset []\n'
                            '  app:\n    image: ' + image + '\n', encoding='utf-8')
        run('docker', 'compose', '-p', 'myfitnesspal-mcp', '-f',
            str(root / 'deploy/compose.yaml'), '-f', str(override), 'up', '-d',
            '--no-deps', '--no-build', '--wait', '--wait-timeout', '90', 'app', env=env)

    try:
        deploy('myfitnesspal-mcp-app:activity-v1')
    except subprocess.CalledProcessError:
        deploy('myfitnesspal-mcp-app:before-activity')
        raise
print('Activity app deployed; browser, gateway and saved state retained.')
