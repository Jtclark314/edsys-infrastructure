#!/usr/bin/env bash
# Explicitly invoked installer. No service upgrade or global client config edits.
set -euo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
runtime_dir=/mnt/ai-store/apps/local-coder
client_dir="$runtime_dir/tools"
mountpoint -q /mnt/ai-store || { echo 'AI Store must be mounted' >&2; exit 1; }
if systemctl --user is-active --quiet edsys-local-coder-web.service; then
  python3 "$source_dir/web_state.py" --require-idle
fi
for program in docker node npm uv pwsh Xvfb xvfb-run xauth xdotool xfwm4 dbus-run-session google-chrome mousepad xcalc; do
  command -v "$program" >/dev/null || { echo "Missing dependency: $program" >&2; exit 1; }
done
/usr/bin/python3 -c "import tkinter"
mkdir -p /home/jeremy/.local/bin
docker image inspect ollama/ollama@sha256:910d0d8107b538fb0894e63163eb195de0cc7aefcbc666dc62d3b18f71124592 >/dev/null
docker network inspect 9950x-workhorse_default >/dev/null
docker exec ollama ollama pull qwen3.6:35b-a3b-q8_0
python3 "$source_dir/verify_model.py" --full
docker cp "$source_dir/Modelfile" ollama:/tmp/edsys-qwen36-coder.Modelfile
docker exec ollama ollama create edsys-qwen36-coder -f /tmp/edsys-qwen36-coder.Modelfile
install -d -m 0700 "$client_dir" /mnt/ai-store/local-coder
# The hardened container runs as UID 0 with every capability dropped, so these
# owner-only writable caches must be owned by UID 0 (not the host operator).
sudo -n install -d -o 0 -g 0 -m 0700 \
  /mnt/ai-store/local-coder/ollama-metadata /mnt/ai-store/local-coder/ollama-manifests-v2
install -m 0644 "$source_dir/package.json" "$source_dir/package-lock.json" "$client_dir/"
npm ci --prefix "$client_dir" --ignore-scripts --no-audit --no-fund
python3 "$source_dir/install-client.py"
if [[ ! -x "$runtime_dir/desktop-venv-2.3.0/bin/python" ]]; then
  uv venv --python /usr/bin/python3 "$runtime_dir/desktop-venv-2.3.0"
fi
uv pip sync --python "$runtime_dir/desktop-venv-2.3.0/bin/python" "$source_dir/requirements.lock"
docker compose -f "$source_dir/compose.yaml" config --quiet
docker compose -f "$source_dir/compose.yaml" up -d --pull never --no-build
launcher=/home/jeremy/.local/bin/edsys-code
if [[ -e "$launcher" || -L "$launcher" ]]; then
  [[ "$(readlink -f "$launcher")" == "$source_dir/edsys-code" ]] || { echo 'Existing launcher belongs to another installation' >&2; exit 1; }
else
  ln -s "$source_dir/edsys-code" "$launcher"
fi
ps_launcher=/home/jeremy/.local/bin/edsys-powershell
if [[ -e "$ps_launcher" || -L "$ps_launcher" ]]; then
  [[ "$(readlink -f "$ps_launcher")" == "$source_dir/edsys-powershell" ]] || { echo 'PowerShell helper belongs to another installation' >&2; exit 1; }
else
  ln -s "$source_dir/edsys-powershell" "$ps_launcher"
fi
"$launcher" --version
"$source_dir/install-expansion.sh"
