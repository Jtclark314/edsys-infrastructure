#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'This pinned build requires Linux x86_64.' >&2; exit 1; }
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
bridge_root="$HOME/.local/share/edsys-gemini-mcp"
bridge_runtime="$bridge_root/runtime"
node_bin="$(readlink -f -- "$(command -v node)")"
"$node_bin" -e 'if (+process.versions.node.split(".")[0] < 24) process.exit(1)'
mkdir -p -- "$bridge_runtime" "$bridge_root/work" "$bridge_root/rollback" "$HOME/.local/bin"
chmod 700 "$bridge_root"
stage_dir="$(mktemp -d "$bridge_root/install.XXXXXXXX")"
trap 'rm -rf -- "$stage_dir"' EXIT
archive="$stage_dir/cli.tar.gz"
curl --fail --silent --show-error --location \
  'https://storage.googleapis.com/antigravity-public/antigravity-cli/1.2.14-4571742832820224/linux-x64/cli_linux_x64.tar.gz' \
  --output "$archive"
printf '%s  %s\n' 'fd771dfc74ddd07b61c8b0a6fd7a238f53a3a098a51052583a01d97ab84ee60db741ce5f041e87a9da3c1a9aabe95b053113374437df1e231773552781edaf09' "$archive" | sha512sum --check --status
mkdir "$stage_dir/native"
python3 - "$archive" "$stage_dir/native" <<'PY'
import sys,tarfile
with tarfile.open(sys.argv[1]) as archive:
    archive.extractall(sys.argv[2],filter='data')
PY
printf '%s  %s\n' '0d0d3eba22daf29504dd290151c7ed9a4d33b0c6aa0acfc5da27bc3b01d2f029' "$stage_dir/native/antigravity" | sha256sum --check --status
mkdir -p "$bridge_root/antigravity-1.2.14"
if [[ ! -e "$bridge_root/antigravity-1.2.14/antigravity" ]]; then
  install -m 700 "$stage_dir/native/antigravity" "$bridge_root/antigravity-1.2.14/antigravity"
fi
backup_dir="$bridge_root/rollback/$(date +%Y%m%d-%H%M%S)-install"
mkdir "$backup_dir"
for name in package.json package-lock.json server.mjs runner.mjs login.mjs; do
  if [[ -f "$bridge_runtime/$name" ]]; then cp -p -- "$bridge_runtime/$name" "$backup_dir/"; fi
  install -m 600 "$source_dir/$name" "$bridge_runtime/$name"
done
npm ci --ignore-scripts --no-audit --no-fund --prefix "$bridge_runtime"
mkdir -p "$HOME/.gemini/antigravity-cli"
if [[ ! -e "$HOME/.gemini/antigravity-cli/settings.json" ]]; then
  install -m 600 "$source_dir/settings.example.json" "$HOME/.gemini/antigravity-cli/settings.json"
fi
if [[ ! -e "$bridge_runtime/provider.json" ]]; then
  printf '%s\n' '{"model":"gemini-3.1-pro-high"}' > "$bridge_runtime/provider.json"
fi
for name in mcp login; do
  entry=server
  [[ "$name" == login ]] && entry=login
  printf '#!/bin/sh\numask 077\nexec "%s" "%s/%s.mjs" "$@"\n' "$node_bin" "$bridge_runtime" "$entry" > "$stage_dir/edsys-gemini-$name"
  install -m 755 "$stage_dir/edsys-gemini-$name" "$HOME/.local/bin/edsys-gemini-$name"
done
"$node_bin" --input-type=module -e 'import(process.argv[1]).then(m => { m.checkPolicy(); console.log("Installed; local policy checks passed. Provider sign-in and a live MCP request are separate acceptance steps."); })' "$bridge_runtime/runner.mjs"
