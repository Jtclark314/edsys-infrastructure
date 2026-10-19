#!/usr/bin/env bash
set -euo pipefail
source_dir="$(dirname "$(readlink -f "$0")")"
runtime_root="${XDG_DATA_HOME:-$HOME/.local/share}/edsys-codex-operations"
install -d -m 0700 "$runtime_root"
python3 -m venv "$runtime_root/venv"
"$runtime_root/venv/bin/python" -m pip install --disable-pip-version-check -r "$source_dir/requirements.lock"
install -d -m 0755 "$HOME/.local/bin"
chmod +x "$source_dir/edsys-operations-mcp"
ln -sfn "$source_dir/edsys-operations-mcp" "$HOME/.local/bin/edsys-operations-mcp"
printf '%s\n' 'Installed EdSys Operations stdio launcher; Codex registration is a separate step.'
