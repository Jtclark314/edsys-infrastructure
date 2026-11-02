#!/usr/bin/env bash
# Installs only a NEW isolated immutable release; never edits registration or production.
set -euo pipefail
umask 077
if [[ $# -ne 1 || "$1" != /* || -e "$1" || -L "$1" ]]; then
  printf '%s\n' 'Usage: install.sh /absolute/new-release-directory (must not exist)' >&2
  exit 2
fi
changedetection_source_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
changedetection_release_dir="$1"
mkdir -p -- "$changedetection_release_dir"
for changedetection_file in bridge.py server.py run.sh requirements.lock api-spec.json catalog-source.json LICENSE LICENSE.upstream NOTICE; do
  cp -- "$changedetection_source_dir/$changedetection_file" "$changedetection_release_dir/"
done
chmod 700 "$changedetection_release_dir/run.sh"
uv venv --python /usr/bin/python3 "$changedetection_release_dir/venv"
uv pip sync --link-mode copy --python "$changedetection_release_dir/venv/bin/python" --require-hashes "$changedetection_release_dir/requirements.lock"
"$changedetection_release_dir/venv/bin/python" -m py_compile "$changedetection_release_dir/bridge.py" "$changedetection_release_dir/server.py"
printf 'Installed isolated release: %s\n' "$changedetection_release_dir"
