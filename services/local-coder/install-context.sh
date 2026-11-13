#!/usr/bin/env bash
# Apply reviewed context/terminal source without touching models or shared AI services.
set -euo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mountpoint -q /mnt/ai-store
command -v pwsh >/dev/null
test -r /home/jeremy/code/EdSys-Master/docs/context-packs/LOCAL_CODER_STARTUP.md
"/mnt/ai-store/apps/local-coder/desktop-venv-2.3.0/bin/python" -m unittest discover -s "$source_dir/tests" -v
node --test "$source_dir/tests/test_context_plugin.mjs" "$source_dir/tests/test_context_plugin_v2.mjs"
python3 - "$source_dir" <<'PY'
import json, pathlib, subprocess, sys, urllib.request
source = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(source))
from web_state import status
# This is a controlled restart, never an implicit cancellation of a user task.
active = subprocess.run(['systemctl','--user','is-active','--quiet','edsys-local-coder-web.service']).returncode == 0
if active:
    if not status()['idle']:
        raise SystemExit('OpenCode has active work; let it finish or explicitly stop it before applying context')
destination=pathlib.Path('/home/jeremy/.local/bin/edsys-powershell')
target=source/'edsys-powershell'
if destination.exists() or destination.is_symlink():
    if destination.resolve() != target.resolve():
        raise SystemExit('PowerShell helper belongs to another installation')
else:
    destination.symlink_to(target)
if active:
    subprocess.run(['install','-m','0644',str(source/'edsys-local-coder-web.service'),
                    '/home/jeremy/.config/systemd/user/edsys-local-coder-web.service'],check=True)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','restart','edsys-local-coder-web.service'],check=True)
print('Context source and PowerShell helper ready; model/engine and other services unchanged')
PY
# Do not start a second client against the session database while the web
# process initializes it after restart. Verify the running instance directly.
python3 - "$source_dir" <<'PY'
import json, pathlib, subprocess, sys, time, urllib.request
source = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(source))
from web_state import read_api
active = subprocess.run(['systemctl','--user','is-active','--quiet','edsys-local-coder-web.service']).returncode == 0
if not active:
    subprocess.run([str(source/'edsys-code'),'--tools','code','mcp','list'],check=True)
    raise SystemExit(0)
for attempt in range(20):
    try:
        groups={item['name']:item['status'] for item in read_api('/api/mcp')['data']}
        expected=set(json.loads((source/'opencode.json').read_text())['mcp']['servers'])
        if set(groups)==expected and all(v.get('status')=='connected' for v in groups.values()):
            print('Web MCP connections verified:', ', '.join(sorted(groups)))
            break
    except (OSError, ValueError):
        pass
    time.sleep(1)
else:
    raise SystemExit('Web MCP connections did not become healthy after restart')
PY
