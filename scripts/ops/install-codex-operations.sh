#!/usr/bin/env bash
set -euo pipefail

enable_timers=false

usage() {
  cat <<'EOF'
Usage: sudo install-codex-operations.sh [--enable]

Install the root-owned report runner and the authoritative systemd services for
the daily Morning Brief, weekly Codex maintenance, and weekly grounded-RAG
quality gate. Existing live files are backed up outside Git before replacement.
File replacement is atomic, and a failed replacement transaction automatically
restores the prior files.

--enable also enables and starts all three timers. It refuses unless the private
/etc/edsys-secrets/edsys-rag-eval.env and all scheduled-job prerequisites pass
validation. A missing runtime prerequisite later causes a failed unit, not a
silently skipped run.
EOF
}

while (($#)); do
  case "$1" in
    --enable)
      enable_timers=true
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root (for example: sudo $0)." >&2
  exit 2
fi

source_installer="$(readlink -f "${BASH_SOURCE[0]}")"
repo_root="$(cd "$(dirname "${source_installer}")/../.." && pwd)"
source_runner="${repo_root}/scripts/ops/edsys-codex-report-runner.py"
source_unit_dir="${repo_root}/scripts/ops/systemd"
live_runner="/usr/local/libexec/edsys-codex-report-runner.py"
secret_dir="/etc/edsys-secrets"
secret_env="${secret_dir}/edsys-rag-eval.env"
backup_base="/var/backups/edsys-codex-operations"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir=""
validation_dir=""
unit_changed=false
anything_changed=false
transaction_active=false

units=(
  edsys-morning-brief.service
  edsys-morning-brief.timer
  edsys-weekly-codex-maintenance.service
  edsys-weekly-codex-maintenance.timer
  edsys-rag-golden-eval.service
  edsys-rag-golden-eval.timer
)

timers=(
  edsys-morning-brief.timer
  edsys-weekly-codex-maintenance.timer
  edsys-rag-golden-eval.timer
)

declare -a transaction_targets=()
declare -a transaction_backups=()
declare -a transaction_existed=()
declare -a temporary_targets=()

restore_install_transaction() {
  local index target backup existed rollback_failed=false

  echo "Installation failed; restoring the prior tracked files." >&2
  for ((index = ${#transaction_targets[@]} - 1; index >= 0; index--)); do
    target="${transaction_targets[index]}"
    backup="${transaction_backups[index]}"
    existed="${transaction_existed[index]}"
    if ! rm -f -- "${target}"; then
      printf 'Could not remove failed installation target: %s\n' "${target}" >&2
      rollback_failed=true
      continue
    fi
    if [[ "${existed}" == true ]] && ! cp -a -- "${backup}" "${target}"; then
      printf 'Could not restore installation target: %s\n' "${target}" >&2
      rollback_failed=true
    fi
  done

  if [[ "${unit_changed}" == true ]] && ! systemctl daemon-reload; then
    echo "Could not reload systemd after restoring the prior unit files." >&2
    rollback_failed=true
  fi
  if [[ "${rollback_failed}" == true ]]; then
    printf 'Automatic rollback was incomplete; review %s immediately.\n' \
      "${backup_dir}" >&2
  else
    printf 'Prior tracked files restored. Evidence retained at %s\n' \
      "${backup_dir}" >&2
  fi
}

cleanup() {
  local rc=$?
  local temporary
  trap - EXIT
  set +e

  for temporary in "${temporary_targets[@]}"; do
    rm -f -- "${temporary}"
  done
  if [[ -n "${validation_dir}" && -d "${validation_dir}" ]]; then
    rm -rf -- "${validation_dir}"
  fi
  if ((rc != 0)) && [[ "${transaction_active}" == true ]]; then
    restore_install_transaction
  elif ((rc != 0)) && [[ -n "${backup_dir}" && -d "${backup_dir}" ]]; then
    printf 'Installation or activation failed; review rollback material at %s\n' \
      "${backup_dir}" >&2
  fi
  exit "${rc}"
}
trap cleanup EXIT

require_private_eval_env() {
  local directory_owner directory_mode file_owner file_mode link_count

  if [[ ! -d "${secret_dir}" || -L "${secret_dir}" ]]; then
    echo "Missing regular private secret directory: ${secret_dir}" >&2
    return 1
  fi
  directory_owner="$(stat -c '%u:%g' "${secret_dir}")"
  directory_mode="$(stat -c '%a' "${secret_dir}")"
  if [[ "${directory_owner}" != "0:0" || "${directory_mode}" != "700" ]]; then
    printf 'Refusing %s: expected uid:gid 0:0 mode 700; found %s mode %s.\n' \
      "${secret_dir}" "${directory_owner}" "${directory_mode}" >&2
    return 1
  fi

  if [[ ! -f "${secret_env}" || -L "${secret_env}" ]]; then
    echo "Missing regular private environment file: ${secret_env}" >&2
    echo "Create it from scripts/ops/edsys-rag-eval.env.example without committing the value." >&2
    return 1
  fi
  file_owner="$(stat -c '%u:%g' "${secret_env}")"
  file_mode="$(stat -c '%a' "${secret_env}")"
  link_count="$(stat -c '%h' "${secret_env}")"
  if [[ "${file_owner}" != "0:0" || "${file_mode}" != "600" || "${link_count}" != "1" ]]; then
    printf 'Refusing %s: expected uid:gid 0:0 mode 600 and one link; found %s mode %s links %s.\n' \
      "${secret_env}" "${file_owner}" "${file_mode}" "${link_count}" >&2
    return 1
  fi

  python3 - "${secret_env}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
if path.stat().st_size > 65536:
    raise SystemExit("Private eval environment is unexpectedly large.")
try:
    lines = path.read_text(encoding="utf-8").splitlines()
except (OSError, UnicodeError) as exc:
    raise SystemExit(f"Private eval environment is unreadable: {type(exc).__name__}") from exc

allowed = {"PORTAL_USERNAME", "PORTAL_PASSWORD"}
seen: set[str] = set()
for line_number, raw_line in enumerate(lines, start=1):
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit(f"Invalid private eval environment assignment at line {line_number}.")
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if key not in allowed:
        raise SystemExit(f"Unsupported private eval environment key at line {line_number}.")
    if key in seen:
        raise SystemExit(f"Duplicate private eval environment key at line {line_number}.")
    if not value or value in {'""', "''"}:
        raise SystemExit(f"Empty private eval environment value at line {line_number}.")
    seen.add(key)

missing = sorted(allowed - seen)
if missing:
    raise SystemExit("Private eval environment is missing required key names.")
PY
}

calendar_value() {
  local unit_path="$1"
  awk -F= '$1 == "OnCalendar" { print substr($0, index($0, "=") + 1) }' \
    "${unit_path}"
}

assert_calendar() {
  local unit_name="$1"
  local expected="$2"
  local actual
  actual="$(calendar_value "${source_unit_dir}/${unit_name}")"
  if [[ "${actual}" != "${expected}" ]]; then
    printf 'Unexpected OnCalendar in %s: expected %s.\n' "${unit_name}" "${expected}" >&2
    return 1
  fi
  systemd-analyze calendar "${actual}" >/dev/null
}

backup_existing() {
  local target="$1"
  local name="$2"
  local existed=false
  local backup="${backup_dir}/${name}"

  if [[ -e "${target}" || -L "${target}" ]]; then
    cp -a -- "${target}" "${backup}"
    existed=true
  fi
  transaction_targets+=("${target}")
  transaction_backups+=("${backup}")
  transaction_existed+=("${existed}")
  printf '%s\t%s\t%s\n' "${target}" "${name}" "${existed}" \
    >>"${backup_dir}/install-manifest.tsv"
}

install_if_changed() {
  local source="$1"
  local target="$2"
  local mode="$3"
  local name="$4"
  local kind="${5:-file}"
  local target_dir temporary

  if [[ -f "${target}" && ! -L "${target}" ]] &&
    cmp -s -- "${source}" "${target}" &&
    [[ "$(stat -c '%a' "${target}")" == "${mode}" ]] &&
    [[ "$(stat -c '%u:%g' "${target}")" == "0:0" ]]; then
    return 0
  fi
  if [[ -d "${target}" && ! -L "${target}" ]]; then
    printf 'Refusing to replace directory with file: %s\n' "${target}" >&2
    return 1
  fi

  backup_existing "${target}" "${name}"
  target_dir="$(dirname "${target}")"
  if [[ ! -d "${target_dir}" || -L "${target_dir}" ]]; then
    printf 'Installation target parent is not a safe directory: %s\n' "${target_dir}" >&2
    return 1
  fi
  temporary="$(mktemp "${target_dir}/.${name}.new.XXXXXX")"
  temporary_targets+=("${temporary}")
  install -m "${mode}" -o root -g root "${source}" "${temporary}"
  mv -fT -- "${temporary}" "${target}"
  anything_changed=true
  if [[ "${kind}" == "unit" ]]; then
    unit_changed=true
  fi
}

for command_name in awk getent mountpoint python3 shellcheck systemd-analyze systemctl; do
  command -v "${command_name}" >/dev/null || {
    printf 'Required installer command is unavailable: %s\n' "${command_name}" >&2
    exit 2
  }
done
getent passwd jeremy >/dev/null || {
  echo "Required service account is unavailable: jeremy" >&2
  exit 2
}
getent group jeremy >/dev/null || {
  echo "Required service group is unavailable: jeremy" >&2
  exit 2
}

for source_path in "${source_runner}" "${units[@]/#/${source_unit_dir}/}"; do
  if [[ ! -f "${source_path}" || -L "${source_path}" ]]; then
    printf 'Deployment source is not a regular non-symlink file: %s\n' "${source_path}" >&2
    exit 2
  fi
done

validation_dir="$(mktemp -d)"
chmod 0700 "${validation_dir}"
PYTHONPYCACHEPREFIX="${validation_dir}/pycache" python3 -m py_compile "${source_runner}"
shellcheck "${source_installer}"
systemd-analyze verify "${units[@]/#/${source_unit_dir}/}"
assert_calendar edsys-morning-brief.timer '*-*-* 06:00:00 America/New_York'
assert_calendar edsys-weekly-codex-maintenance.timer 'Mon *-*-* 07:30:00 America/New_York'
assert_calendar edsys-rag-golden-eval.timer 'Mon *-*-* 05:00:00 America/New_York'

if [[ "${enable_timers}" == true ]]; then
  golden_wrapper="/srv/edsys/EdSys-Master/tools/rag-eval/edsys-rag-golden-eval.py"
  golden_queries="/srv/edsys/EdSys-Master/data/rag-golden-queries.yml"
  portal_repo="/home/jeremy/code/edsys-ai-portal"

  require_private_eval_env
  python3 -c 'import yaml' >/dev/null
  for required_file in \
    /srv/edsys/EdSys-Master/tools/codex-hub/edsys-morning-brief.py \
    /srv/edsys/EdSys-Master/tools/codex-hub/edsys-weekly-codex-maintenance.py \
    /srv/edsys/EdSys-Master/tools/codex-hub/edsys-rag-memory-hygiene-check.py \
    "${golden_wrapper}" \
    "${golden_queries}" \
    "${portal_repo}/tools/rag_eval_runner.py"; do
    [[ -f "${required_file}" && ! -L "${required_file}" ]] || {
      printf 'Scheduled-job prerequisite is not a regular non-symlink file: %s\n' \
        "${required_file}" >&2
      exit 2
    }
  done
  [[ -x "${portal_repo}/.venv/bin/python" ]] || {
    echo "The Portal virtual-environment Python is unavailable." >&2
    exit 2
  }
  [[ -x /home/jeremy/.local/bin/codex ]] || {
    echo "The active standalone Codex CLI is unavailable at /home/jeremy/.local/bin/codex." >&2
    exit 2
  }
  mountpoint -q /mnt/ai-store || {
    echo "AI Store is not mounted at /mnt/ai-store; refusing to enable the eval timer." >&2
    exit 2
  }
  /usr/bin/python3 "${golden_wrapper}" validate-queries \
    --queries "${golden_queries}" >"${validation_dir}/validate-queries.json"
  /usr/bin/python3 "${golden_wrapper}" run \
    --queries "${golden_queries}" \
    --portal-repo "${portal_repo}" \
    --portal-url http://192.168.50.50:3020 \
    --judge heuristic \
    --model edsys-grounded-local \
    --results-dir /mnt/ai-store/rag/evals \
    --state-dir /var/lib/edsys-rag-eval \
    --dry-run >"${validation_dir}/golden-eval-dry-run.json"
fi

for managed_directory in \
  /usr/local/libexec \
  "${secret_dir}" \
  /var/lib/edsys-codex-reports \
  /var/lib/edsys-rag-eval \
  "${backup_base}" \
  /mnt/ai-store/rag/evals; do
  if [[ -L "${managed_directory}" ]]; then
    printf 'Refusing symlinked managed directory: %s\n' "${managed_directory}" >&2
    exit 2
  fi
done

install -d -m 0755 -o root -g root /usr/local/libexec
install -d -m 0700 -o root -g root "${secret_dir}"
install -d -m 0700 -o jeremy -g jeremy /var/lib/edsys-codex-reports
install -d -m 0700 -o jeremy -g jeremy /var/lib/edsys-rag-eval
if mountpoint -q /mnt/ai-store; then
  install -d -m 0700 -o jeremy -g jeremy /mnt/ai-store/rag/evals
fi

install -d -m 0700 -o root -g root "${backup_base}"
backup_dir="$(mktemp -d "${backup_base}/${stamp}.XXXXXX")"
chmod 0700 "${backup_dir}"
install -m 0600 -o root -g root /dev/null "${backup_dir}/install-manifest.tsv"

transaction_active=true
install_if_changed "${source_runner}" "${live_runner}" 755 edsys-codex-report-runner.py
for unit in "${units[@]}"; do
  install_if_changed \
    "${source_unit_dir}/${unit}" \
    "/etc/systemd/system/${unit}" \
    644 \
    "${unit}" \
    unit
done

if [[ "${unit_changed}" == true ]]; then
  systemctl daemon-reload
fi
transaction_active=false

if [[ "${enable_timers}" == true ]]; then
  systemctl enable --now "${timers[@]}"
  for timer in "${timers[@]}"; do
    systemctl is-enabled --quiet "${timer}"
    systemctl is-active --quiet "${timer}"
  done
fi

if [[ "${anything_changed}" == true ]]; then
  printf 'Installed EdSys Codex operations controls. Rollback files: %s\n' "${backup_dir}"
else
  rm -rf -- "${backup_dir}"
  backup_dir=""
  echo "EdSys Codex operations controls already match source; no files changed."
fi

if [[ "${enable_timers}" == true ]]; then
  echo "Authoritative Morning Brief, weekly maintenance, and golden-eval timers are enabled."
else
  echo "Timers were not enabled. Provision the private eval environment, then rerun with --enable."
fi
