#!/usr/bin/env bash
# Explicitly invoked installer. No service upgrade or global client config edits.
set -euo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
runtime_dir=/mnt/ai-store/apps/local-coder
client_dir="$runtime_dir/opencode-1.18.31"
mountpoint -q /mnt/ai-store || { echo 'AI Store must be mounted' >&2; exit 1; }
for program in docker node npm uv Xvfb xvfb-run xauth xdotool xfwm4 dbus-run-session google-chrome mousepad xcalc; do
  command -v "$program" >/dev/null || { echo "Missing dependency: $program" >&2; exit 1; }
done
/usr/bin/python3 -c "import tkinter"
mkdir -p /home/jeremy/.local/bin
docker image inspect ollama/ollama@sha256:020e4134285e2ef4d8fd801234176de3b4faadc992a3eb06c8e66a2f9d4c4ba2 >/dev/null
docker network inspect 9950x-workhorse_default >/dev/null
docker exec ollama ollama pull qwen3.6:35b-a3b-q8_0
python3 "$source_dir/verify_model.py" --full
docker cp "$source_dir/Modelfile" ollama:/tmp/edsys-qwen36-coder.Modelfile
docker exec ollama ollama create edsys-qwen36-coder -f /tmp/edsys-qwen36-coder.Modelfile
install -d -m 0700 "$client_dir" /mnt/ai-store/local-coder
install -m 0644 "$source_dir/package.json" "$source_dir/package-lock.json" "$client_dir/"
npm ci --prefix "$client_dir" --ignore-scripts --no-audit --no-fund
# Reviewed postinstall only links/copies the pinned platform binary and probes it.
node "$client_dir/node_modules/opencode-ai/postinstall.mjs"
if [[ ! -x "$runtime_dir/desktop-venv/bin/python" ]]; then
  uv venv --python /usr/bin/python3 "$runtime_dir/desktop-venv"
fi
uv pip sync --python "$runtime_dir/desktop-venv/bin/python" "$source_dir/requirements.lock"
docker compose -f "$source_dir/compose.yaml" config --quiet
docker compose -f "$source_dir/compose.yaml" up -d --pull never --no-build
launcher=/home/jeremy/.local/bin/edsys-code
if [[ -e "$launcher" || -L "$launcher" ]]; then
  [[ "$(readlink -f "$launcher")" == "$source_dir/edsys-code" ]] || { echo 'Existing launcher belongs to another installation' >&2; exit 1; }
else
  ln -s "$source_dir/edsys-code" "$launcher"
fi
"$launcher" --version
"$launcher" mcp list
