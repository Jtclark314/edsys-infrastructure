#!/usr/bin/env bash
# Install only into a NEW immutable release directory. Does not register MCP or alter other runtimes.
set -euo pipefail
umask 077
if [[ $# -ne 1 || "$1" != /* || -e "$1" || -L "$1" ]]; then
  printf '%s\n' 'Usage: install.sh /absolute/new-release-directory (must not exist)' >&2
  exit 2
fi
stirling_source_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
stirling_release_dir="$1"
mkdir -p -- "$stirling_release_dir"
cp -- "$stirling_source_dir/bridge.py" "$stirling_source_dir/server.py" "$stirling_source_dir/run.sh" \
  "$stirling_source_dir/LICENSE" "$stirling_source_dir/requirements.lock" "$stirling_release_dir/"
chmod 700 "$stirling_release_dir/run.sh"
uv venv --python /usr/bin/python3 "$stirling_release_dir/venv"
uv pip sync --link-mode copy --python "$stirling_release_dir/venv/bin/python" --require-hashes "$stirling_release_dir/requirements.lock"
"$stirling_release_dir/venv/bin/python" -m py_compile "$stirling_release_dir/bridge.py" "$stirling_release_dir/server.py"
printf 'Installed isolated release: %s\n' "$stirling_release_dir"
