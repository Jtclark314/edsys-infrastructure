#!/usr/bin/env bash
set -euo pipefail
source_dir="$(dirname "$(readlink -f "$0")")"
runtime_root="$HOME/.local/share/edsys-media-cleanup"
install -d -m 0700 "$runtime_root"
python3 -m venv "$runtime_root/venv"
"$runtime_root/venv/bin/python" -m pip install --disable-pip-version-check -r "$source_dir/requirements.lock"
install -d -m 0755 "$HOME/.local/bin"
chmod +x "$source_dir/edsys-media-cleanup" "$source_dir/edsys-media-cleanup-mcp"
ln -sfn "$source_dir/edsys-media-cleanup" "$HOME/.local/bin/edsys-media-cleanup"
ln -sfn "$source_dir/edsys-media-cleanup-mcp" "$HOME/.local/bin/edsys-media-cleanup-mcp"
install -d -m 0755 "$HOME/.codex/skills"
if [[ -e "$HOME/.codex/skills/media-cleanup" && ! -L "$HOME/.codex/skills/media-cleanup" ]]; then
  printf '%s\n' 'An unrelated media-cleanup skill already exists; inspect before installation.' >&2
  exit 1
fi
ln -sfn "$source_dir/skills/media-cleanup" "$HOME/.codex/skills/media-cleanup"
printf '%s\n' 'Installed Media Cleanup CLI, MCP launcher, and skill. MCP registration is separate.'
