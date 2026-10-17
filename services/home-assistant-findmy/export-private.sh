#!/usr/bin/env bash
# Private interactive entry point. Never launch this through a recorded tool PTY.
set -euo pipefail
umask 077
ulimit -c 0
task_root=/home/jeremy/.local/share/edsys-findmy/2026-10-17
image=sha256:fdd617d6b391591b208a607a5b12a8b3f19c57c4dc28eb739960d7bb87b528af
helper=edsys-findmy-anisette
if [[ ${1:-} == --check ]]; then
  docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
    --read-only --user "$(id -u):$(id -g)" "$image" --help
  exit
fi
mode=export
if [[ $# == 1 && ${1:-} == --check-signin ]]; then
  mode=init
elif [[ $# == 1 && ${1:-} == --check-helper ]]; then
  mode=helper
elif [[ $# != 0 ]]; then
  echo 'Use no arguments, --check-signin, --check-helper, or --check.' >&2
  exit 2
fi
if [[ $mode != helper && ( ! -t 0 || ! -t 1 ) ]]; then
  echo 'Run in your own interactive 9950x terminal.' >&2
  exit 2
fi
mkdir -p "$task_root/private/export"
chmod 700 "$task_root" "$task_root/private" "$task_root/private/export"
exec 9>"$task_root/private/export.lock"
flock -n 9 || { echo 'Another private export is already running.' >&2; exit 2; }
cleanup() { docker stop --time 3 "$helper" >/dev/null 2>&1 || true; }
trap cleanup EXIT
if [[ $(docker inspect -f '{{.State.Running}}' "$helper") != true ]]; then
  docker start "$helper" >/dev/null
fi
ready=0
for attempt in {1..20}; do
  if docker exec "$helper" curl --fail --silent --max-time 1 --output /dev/null \
      http://127.0.0.1:6969/v3/client_info >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.25
done
if [[ $ready != 1 ]]; then
  echo 'The local authentication helper is not ready. No Apple sign-in was attempted.' >&2
  exit 1
fi
if [[ $mode == helper ]]; then
  echo 'Local authentication helper ready; no Apple sign-in attempted.'
  exit 0
fi
echo 'Find My Linux trial — private Apple sign-in'
echo 'Exporter build: 20261017-windows2 (staged sign-in diagnostics)'
echo 'Apple sign-in uses Windows compatibility metadata; the existing CloudKit peer profile is preserved.'
args=(--device-profile /private/device-profile.toml --anisette-url http://anisette:6969 --no-auth-cache)
if [[ $mode == init ]]; then
  args+=(--check-login-init)
  echo 'Password-free check: enter only your Apple ID. One SRP init request; no password proof or 2FA.'
  echo 'This check does not sign in, join Keychain, or export trackers. No Docker logs are retained.'
else
  echo 'This registers an exporter peer with iCloud Keychain and can export all paired tags.'
  echo 'Its local state can contain broader Keychain secrets; it stays private on this host.'
  echo 'Password, verification code and device passcode are hidden. No Docker logs are retained.'
  echo 'Choose the actual trusted device whose passcode you know; do not guess or retry repeatedly.'
fi
echo
docker run --rm -it --name edsys-findmy-private-export --log-driver none \
  --network edsys-findmy-pilot --cpus 2 --memory 2g --pids-limit 128 \
  --cap-drop ALL --security-opt no-new-privileges --read-only \
  --user "$(id -u):$(id -g)" --ulimit core=0 \
  --tmpfs /tmp:rw,nosuid,noexec,size=64m \
  --env RUST_LOG=off --env RUST_BACKTRACE=0 \
  --mount "type=bind,src=$task_root/private/export,dst=/private" \
  "$image" "${args[@]}"
if [[ $mode == export ]]; then
  "$task_root/ha-checks/venv/bin/python" "$task_root/select-tracker.py"
fi
