#!/usr/bin/env bash
set -u
set -o pipefail

# Read-only evidence gate for the 9950x system-NVMe migration. This script
# intentionally never partitions, formats, mounts, clones, repairs, or resizes
# a disk. Device names are discovered at runtime because NVMe numbering can
# change when a drive moves between motherboard sockets.

MODE="${1:-inventory}"
case "${MODE}" in
  inventory|preclone|postboot|acceptance) ;;
  *)
    echo "Usage: $0 [inventory|preclone|postboot|acceptance]" >&2
    exit 2
    ;;
esac

FAILURES=0
WARNINGS=0

pass() { printf 'PASS: %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf 'FAIL: %s\n' "$*"; FAILURES=$((FAILURES + 1)); }

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "required command is missing: $1"
    return 1
  fi
}

bytes_for_device() {
  blockdev --getsize64 "$1" 2>/dev/null || printf '0\n'
}

disk_for_mount() {
  local target="$1"
  local source
  local parent
  source="$(findmnt -rn -o SOURCE --target "${target}" 2>/dev/null | head -n1)"
  [[ "${source}" == /dev/* ]] || return 1
  parent="$(lsblk -dnro PKNAME "${source}" 2>/dev/null | head -n1)"
  [[ -n "${parent}" ]] || return 1
  printf '/dev/%s\n' "${parent}"
}

controller_for_disk() {
  local disk_base
  disk_base="$(basename "$1")"
  if [[ "${disk_base}" =~ ^(nvme[0-9]+)n[0-9]+$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

link_value_for_disk() {
  local disk="$1"
  local attribute="$2"
  local controller
  local pci_path
  local pci_id
  controller="$(controller_for_disk "${disk}")" || return 1
  pci_path="$(readlink -f "/sys/class/nvme/${controller}/device" 2>/dev/null)" || return 1
  pci_id="$(basename "${pci_path}")"
  cat "/sys/bus/pci/devices/${pci_id}/${attribute}" 2>/dev/null
}

print_nvme_inventory() {
  local disk
  local controller
  local model
  local serial
  local size
  local current_speed
  local current_width
  local max_speed
  local max_width

  echo "## NVMe inventory"
  while IFS= read -r disk; do
    [[ -n "${disk}" ]] || continue
    controller="$(controller_for_disk "${disk}" 2>/dev/null || true)"
    model="$(lsblk -dno MODEL "${disk}" 2>/dev/null | xargs)"
    serial="$(lsblk -dno SERIAL "${disk}" 2>/dev/null | xargs)"
    size="$(lsblk -dno SIZE "${disk}" 2>/dev/null | xargs)"
    current_speed="$(link_value_for_disk "${disk}" current_link_speed 2>/dev/null || true)"
    current_width="$(link_value_for_disk "${disk}" current_link_width 2>/dev/null || true)"
    max_speed="$(link_value_for_disk "${disk}" max_link_speed 2>/dev/null || true)"
    max_width="$(link_value_for_disk "${disk}" max_link_width 2>/dev/null || true)"
    printf '%s | size=%s | model=%s | serial=%s | link=%s x%s | device-max=%s x%s\n' \
      "${disk}" "${size:-unknown}" "${model:-unknown}" "${serial:-unknown}" \
      "${current_speed:-unknown}" "${current_width:-unknown}" \
      "${max_speed:-unknown}" "${max_width:-unknown}"
    lsblk -nrpo NAME,FSTYPE,UUID,MOUNTPOINTS "${disk}" 2>/dev/null | sed 's/^/  /'
    if [[ -n "${controller}" ]] && command -v nvme >/dev/null 2>&1; then
      if sudo -n true >/dev/null 2>&1; then
        sudo -n nvme smart-log "/dev/${controller}" 2>/dev/null \
          | grep -E '^(critical_warning|temperature|available_spare[[:space:]]|percentage_used|power_on_hours|unsafe_shutdowns|media_errors|num_err_log_entries)' \
          | sed 's/^/  /' || warn "SMART data unavailable for ${disk}"
      else
        warn "passwordless sudo unavailable; SMART data skipped for ${disk}"
      fi
    fi
  done < <(lsblk -dnpo NAME,TYPE,TRAN | awk '$2 == "disk" && $3 == "nvme" {print $1}')
}

check_stable_identifiers() {
  if grep -Eq '^[[:space:]]*/dev/nvme' /etc/fstab; then
    fail "/etc/fstab contains an unstable /dev/nvme reference"
  else
    pass "/etc/fstab has no direct /dev/nvme references"
  fi

  if awk '$1 !~ /^#/ && $2 == "/" && $1 ~ /^UUID=/' /etc/fstab | grep -q .; then
    pass "root filesystem is declared by UUID"
  else
    fail "root filesystem is not declared by UUID in /etc/fstab"
  fi

  if awk '$1 !~ /^#/ && $2 == "/boot/efi" && $1 ~ /^UUID=/' /etc/fstab | grep -q .; then
    pass "EFI filesystem is declared by UUID"
  else
    fail "EFI filesystem is not declared by UUID in /etc/fstab"
  fi

  if awk '$1 !~ /^#/ && $2 == "/mnt/ai-store" && $1 ~ /^UUID=/' /etc/fstab | grep -q .; then
    pass "AI Store is declared by UUID"
  else
    fail "AI Store is not declared by UUID in /etc/fstab"
  fi

  if tr ' ' '\n' </proc/cmdline | grep -Eq '^root=UUID='; then
    pass "kernel root argument uses UUID"
  else
    fail "kernel root argument does not use UUID"
  fi

  if [[ -f /boot/efi/EFI/ubuntu/shimx64.efi && -f /boot/efi/EFI/BOOT/BOOTX64.EFI ]]; then
    pass "Ubuntu and fallback EFI loaders are present"
  else
    fail "one or more expected EFI loaders are missing"
  fi

  if command -v efibootmgr >/dev/null 2>&1 && sudo -n efibootmgr -v 2>/dev/null | grep -q 'Ubuntu.*HD(1,GPT'; then
    pass "UEFI Ubuntu entry uses a GPT partition reference"
  else
    warn "UEFI Ubuntu GPT boot entry could not be confirmed"
  fi
}

check_mount_contract() {
  local target
  for target in / /boot/efi /mnt/ai-store; do
    if findmnt -rn --target "${target}" >/dev/null 2>&1; then
      pass "mounted: ${target}"
    else
      fail "required mount missing: ${target}"
    fi
  done

  for target in \
    /EdSys-Share \
    /Foothills-Inbox \
    /home/jeremy/projects/foothills \
    /srv/app-foundry/workspaces \
    /home/jeremy/stacks/_snapshots; do
    if findmnt -rn --target "${target}" >/dev/null 2>&1; then
      pass "AI Store bind mounted: ${target}"
    else
      fail "AI Store bind missing: ${target}"
    fi
  done
}

check_duplicate_uuids() {
  local duplicates
  duplicates="$(lsblk -nrpo UUID 2>/dev/null | awk 'NF' | sort | uniq -d)"
  if [[ -n "${duplicates}" ]]; then
    fail "duplicate filesystem UUIDs are visible; do not continue with both clones attached"
  else
    pass "no duplicate filesystem UUIDs are visible"
  fi
}

check_backup_freshness() {
  local status_file="/var/lib/edsys-backup/status.json"
  local status
  local timestamp
  local age
  if [[ ! -r "${status_file}" ]]; then
    warn "backup status is unreadable: ${status_file}"
    return
  fi
  status="$(jq -r '.status // "unknown"' "${status_file}" 2>/dev/null || true)"
  timestamp="$(jq -r '.timestamp // ""' "${status_file}" 2>/dev/null || true)"
  if [[ "${status}" != "success" || -z "${timestamp}" ]]; then
    fail "latest EdSys backup status is not successful"
    return
  fi
  age=$(( $(date +%s) - $(date -d "${timestamp}" +%s) ))
  if (( age <= 129600 )); then
    pass "latest local EdSys backup succeeded within 36 hours (${timestamp})"
  else
    fail "latest local EdSys backup is older than 36 hours (${timestamp})"
  fi

  if systemctl show edsys-offsite-sync.service -p Result --value 2>/dev/null | grep -qx success; then
    pass "latest offsite-sync service result is success"
  else
    fail "latest offsite-sync service result is not success"
  fi
}

check_service_health() {
  local service
  local failed_units
  for service in docker.service tailscaled.service; do
    if systemctl is-active --quiet "${service}"; then
      pass "service active: ${service}"
    else
      fail "service not active: ${service}"
    fi
  done

  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    pass "NVIDIA GPU is visible"
  else
    fail "NVIDIA GPU is not visible"
  fi

  failed_units="$(systemctl --failed --no-legend --plain 2>/dev/null | awk 'NF {print $1}')"
  if [[ -z "${failed_units}" ]]; then
    pass "systemd has no failed units"
  else
    warn "systemd failed units: $(tr '\n' ' ' <<<"${failed_units}")"
  fi
}

check_preclone_layout() {
  local root_disk
  local ai_disk
  local count
  local candidate=""
  local disk
  local size
  local mounted
  local gib=$((1024 * 1024 * 1024))

  root_disk="$(disk_for_mount / 2>/dev/null || true)"
  ai_disk="$(disk_for_mount /mnt/ai-store 2>/dev/null || true)"
  count="$(lsblk -dnpo NAME,TYPE,TRAN | awk '$2 == "disk" && $3 == "nvme" {count++} END {print count+0}')"
  if [[ "${count}" == 3 ]]; then
    pass "exactly three NVMe disks are visible for pre-clone staging"
  else
    fail "preclone expects exactly three NVMe disks; found ${count}"
  fi

  while IFS= read -r disk; do
    [[ "${disk}" == "${root_disk}" || "${disk}" == "${ai_disk}" ]] && continue
    candidate="${disk}"
  done < <(lsblk -dnpo NAME,TYPE,TRAN | awk '$2 == "disk" && $3 == "nvme" {print $1}')

  if [[ -z "${candidate}" ]]; then
    fail "could not identify the unassigned clone target"
    return
  fi
  size="$(bytes_for_device "${candidate}")"
  if (( size >= 850 * gib && size <= 1100 * gib )); then
    pass "unassigned clone target is approximately 1 TB: ${candidate}"
  else
    fail "unassigned clone target is outside the expected 850-1100 GiB range: ${candidate}"
  fi
  printf 'TARGET-CANDIDATE: %s | model=%s | serial=%s | bytes=%s\n' \
    "${candidate}" \
    "$(lsblk -dno MODEL "${candidate}" | xargs)" \
    "$(lsblk -dno SERIAL "${candidate}" | xargs)" \
    "${size}"

  mounted="$(lsblk -nrpo MOUNTPOINTS "${candidate}" | awk 'NF')"
  if [[ -z "${mounted}" ]]; then
    pass "clone target has no mounted filesystem"
  else
    fail "clone target has a mounted filesystem; stop and unmount it before cloning"
  fi
}

check_postboot_layout() {
  local root_disk
  local ai_disk
  local count
  local root_bytes
  local ai_bytes
  local root_fs_bytes
  local root_width
  local ai_width
  local gib=$((1024 * 1024 * 1024))

  root_disk="$(disk_for_mount / 2>/dev/null || true)"
  ai_disk="$(disk_for_mount /mnt/ai-store 2>/dev/null || true)"
  count="$(lsblk -dnpo NAME,TYPE,TRAN | awk '$2 == "disk" && $3 == "nvme" {count++} END {print count+0}')"
  if [[ "${count}" == 2 ]]; then
    pass "exactly two NVMe disks are visible after migration"
  else
    fail "postboot expects exactly two NVMe disks; found ${count}"
  fi
  if [[ -n "${root_disk}" && -n "${ai_disk}" && "${root_disk}" != "${ai_disk}" ]]; then
    pass "root and AI Store use distinct physical disks"
  else
    fail "root and AI Store physical-disk mapping is invalid"
  fi

  root_bytes="$(bytes_for_device "${root_disk:-/dev/none}")"
  ai_bytes="$(bytes_for_device "${ai_disk:-/dev/none}")"
  if (( root_bytes >= 850 * gib )); then
    pass "root backing disk is approximately 1 TB or larger"
  else
    fail "root is not backed by the expected replacement disk"
  fi
  if (( ai_bytes >= 1700 * gib )); then
    pass "AI Store remains on the approximately 2 TB disk"
  else
    fail "AI Store is not backed by the expected approximately 2 TB disk"
  fi

  root_width="$(link_value_for_disk "${root_disk}" current_link_width 2>/dev/null || true)"
  ai_width="$(link_value_for_disk "${ai_disk}" current_link_width 2>/dev/null || true)"
  if [[ "${root_width}" == 4 ]]; then
    pass "replacement system NVMe negotiated x4"
  else
    fail "replacement system NVMe did not negotiate x4 (reported ${root_width:-unknown})"
  fi
  if [[ "${ai_width}" == 4 ]]; then
    pass "AI Store NVMe negotiated x4 in M2B"
  else
    fail "AI Store NVMe did not negotiate x4 (reported ${ai_width:-unknown})"
  fi

  root_fs_bytes="$(findmnt -bnro SIZE --target / 2>/dev/null | head -n1)"
  if (( ${root_fs_bytes:-0} >= 800 * gib )); then
    pass "root filesystem has been expanded to at least 800 GiB"
  elif [[ "${MODE}" == acceptance ]]; then
    fail "root filesystem has not been expanded to at least 800 GiB"
  else
    warn "root filesystem still needs expansion after the first successful boot"
  fi
}

echo "# 9950x NVMe migration check"
echo "mode=${MODE}"
echo "timestamp=$(date --iso-8601=seconds)"
echo "hostname=$(hostname -s)"
echo

if [[ "$(hostname -s)" != 9950x ]]; then
  fail "this helper must run on hostname 9950x"
fi

for command_name in awk blockdev date findmnt grep jq lsblk readlink sort systemctl uniq xargs; do
  require_command "${command_name}" || true
done

print_nvme_inventory
echo
echo "## Stable-identifier contract"
check_stable_identifiers
echo
echo "## Mount contract"
check_mount_contract
echo
echo "## Clone-identity guard"
check_duplicate_uuids
echo
echo "## Backup freshness"
check_backup_freshness
echo
echo "## Host health"
check_service_health

case "${MODE}" in
  preclone)
    echo
    echo "## Pre-clone layout gate"
    check_preclone_layout
    ;;
  postboot|acceptance)
    echo
    echo "## Post-boot layout gate"
    check_postboot_layout
    ;;
esac

echo
printf 'SUMMARY: failures=%d warnings=%d\n' "${FAILURES}" "${WARNINGS}"
(( FAILURES == 0 ))
