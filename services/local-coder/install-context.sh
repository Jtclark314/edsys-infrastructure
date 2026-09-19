#!/usr/bin/env bash
# Apply reviewed context/terminal source without touching models or shared AI services.
set -euo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mountpoint -q /mnt/ai-store
command -v pwsh >/dev/null
test -r /home/jeremy/code/EdSys-Master/docs/context-packs/LOCAL_CODER_STARTUP.md
"/mnt/ai-store/apps/local-coder/desktop-venv/bin/python" -m unittest discover -s "$source_dir/tests" -v
node --test "$source_dir/tests/test_context_plugin.mjs"
python3 - "$source_dir" <<'PY'
import json, pathlib, subprocess, sys, urllib.request
source = pathlib.Path(sys.argv[1])
# This is a controlled restart, never an implicit cancellation of a user task.
active = subprocess.run(['systemctl','--user','is-active','--quiet','edsys-local-coder-web.service']).returncode == 0
if active:
    with urllib.request.urlopen('https://9950x.taile832fe.ts.net:8444/session/status',timeout=15) as response:
        sessions=json.load(response)
    if any(value.get('type') != 'idle' for value in sessions.values()):
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
"$source_dir/edsys-code" --tools code mcp list
