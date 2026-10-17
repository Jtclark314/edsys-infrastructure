#!/usr/bin/env bash
# Private interactive entry point. Never launch this through a recorded tool PTY.
set -euo pipefail
umask 077
ulimit -c 0
task_root=/home/jeremy/.local/share/edsys-findmy/2026-10-17
image=sha256:6727a5460db694b4b88bf765e2e13abec4deb73230e1adf88d84a1dcd5182725
helper=edsys-findmy-anisette
if [[ ${1:-} == --check ]]; then
  docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
    --read-only --user "$(id -u):$(id -g)" "$image" --help
  exit
fi
if [[ $# != 0 || ! -t 0 || ! -t 1 ]]; then
  echo 'Run without arguments in your own interactive 9950x terminal.' >&2
  exit 2
fi
mkdir -p "$task_root/private/export"
chmod 700 "$task_root" "$task_root/private" "$task_root/private/export"
exec 9>"$task_root/private/export.lock"
flock -n 9 || { echo 'Another private export is already running.' >&2; exit 2; }
if [[ $(docker inspect -f '{{.State.Running}}' "$helper") != true ]]; then
  docker start "$helper" >/dev/null
fi
cleanup() { docker stop --time 3 "$helper" >/dev/null 2>&1 || true; }
trap cleanup EXIT
echo 'Find My Linux trial — private Apple sign-in'
echo 'This registers an exporter peer with iCloud Keychain and can export all paired tags.'
echo 'Its local state can contain broader Keychain secrets; it stays private on this host.'
echo 'Password, verification code and device passcode are hidden. No Docker logs are retained.'
echo 'Choose the actual trusted device whose passcode you know; do not guess or retry repeatedly.'
echo
docker run --rm -it --name edsys-findmy-private-export --log-driver none \
  --network edsys-findmy-pilot --cpus 2 --memory 2g --pids-limit 128 \
  --cap-drop ALL --security-opt no-new-privileges --read-only \
  --user "$(id -u):$(id -g)" --ulimit core=0 \
  --tmpfs /tmp:rw,nosuid,noexec,size=64m \
  --env RUST_LOG=off --env RUST_BACKTRACE=0 \
  --mount "type=bind,src=$task_root/private/export,dst=/private" \
  "$image" --device-profile /private/device-profile.toml \
  --anisette-url http://anisette:6969 --no-auth-cache
"$task_root/ha-checks/venv/bin/python" "$task_root/select-tracker.py"
