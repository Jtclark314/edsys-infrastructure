#!/usr/bin/env bash
# Explicitly authorized private web deployment; preserves other Serve routes.
set -euo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mountpoint -q /mnt/ai-store
mkdir -p /mnt/ai-store/local-coder/web /home/jeremy/projects/local-ai-lab /home/jeremy/.config/systemd/user
python3 - <<'PY'
import json, os, pathlib, secrets, subprocess
root = pathlib.Path('/mnt/ai-store/local-coder/web')
status = json.loads(subprocess.check_output(['tailscale', 'status', '--json']))
login = status['User'][str(status['Self']['UserID'])]['LoginName']
config = root / 'proxy.json'
if config.exists():
    data = json.loads(config.read_text())
    assert data['ownerLogin'] == login, 'Existing owner differs; review private configuration'
else:
    data = {'ownerLogin': login, 'publicHost': '9950x.taile832fe.ts.net:8444',
            'upstreamPort': 4096, 'backendPassword': secrets.token_urlsafe(48)}
    config.write_text(json.dumps(data))
    config.chmod(0o600)
(root / 'server.env').write_text('OPENCODE_SERVER_USERNAME=opencode\nOPENCODE_SERVER_PASSWORD=' + data['backendPassword'] + '\n')
(root / 'server.env').chmod(0o600)
serve = subprocess.check_output(['tailscale', 'serve', 'status', '--json'])
snapshot = root / 'serve-before.json'
if not snapshot.exists(): snapshot.write_bytes(serve)
current = json.loads(serve)
existing = current.get('Web', {}).get(data['publicHost'])
assert existing in (None, {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:4097'}}}), 'Serve port is already owned'
PY
install -m 0644 "$source_dir/edsys-local-coder-web.service" "$source_dir/edsys-local-coder-web-proxy.service" /home/jeremy/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now edsys-local-coder-web.service edsys-local-coder-web-proxy.service
tailscale serve --bg --https=8444 http://127.0.0.1:4097
