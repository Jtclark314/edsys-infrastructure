#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1
unset PYTHONPATH PYTHONHOME
stirling_release_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$stirling_release_dir/venv/bin/python" "$stirling_release_dir/server.py"
