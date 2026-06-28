#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${HEALTHCHECKS_CONTAINER:-workhorse-healthchecks}"
BASE_URL="${HEALTHCHECKS_SITE_ROOT:-http://127.0.0.1:3014}"
OUT_DIR="${HEALTHCHECKS_ENV_DIR:-/etc/edsys-healthchecks}"
TMP_MAP="$(mktemp)"
trap 'rm -f "$TMP_MAP"' EXIT
ADMIN_ENV="$OUT_DIR/admin.env"
ADMIN_USER="edsys-local-admin"
ADMIN_EMAIL="local-admin@edsys.local"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "Healthchecks container $CONTAINER is not running" >&2
  exit 1
fi

# Create or reuse a private local admin password without printing it.
WRITE_ADMIN_ENV=false
if sudo test -f "$ADMIN_ENV"; then
  ADMIN_PASSWORD="$(sudo awk -F= '/^HEALTHCHECKS_LOCAL_ADMIN_PASSWORD=/{print substr($0, index($0, "=") + 1)}' "$ADMIN_ENV")"
else
  ADMIN_PASSWORD="$(openssl rand -hex 24)"
  WRITE_ADMIN_ENV=true
fi
if [[ -z "$ADMIN_PASSWORD" ]]; then
  ADMIN_PASSWORD="$(openssl rand -hex 24)"
  WRITE_ADMIN_ENV=true
fi

# Create a local admin/project/checks. The first stdin line is the private
# admin password; the Python output contains private ping UUIDs. Keep output
# in a 0600 temp file and never print it.
{
  printf '%s\n' "$ADMIN_PASSWORD"
  cat <<'PY'
import os
from datetime import timedelta
from django.contrib.auth.models import User
from hc.accounts.models import Profile, Project
from hc.api.models import Check

user, created = User.objects.get_or_create(
    username=os.environ["HC_ADMIN_USER"],
    defaults={"email": os.environ["HC_ADMIN_EMAIL"], "is_staff": True, "is_superuser": True},
)
if user.email != os.environ["HC_ADMIN_EMAIL"] or not user.is_staff or not user.is_superuser:
    user.email = os.environ["HC_ADMIN_EMAIL"]
    user.is_staff = True
    user.is_superuser = True
    user.save()
user.set_password(os.environ["HC_ADMIN_PASSWORD"])
user.save()
Profile.objects.get_or_create(user=user)
project, _ = Project.objects.get_or_create(owner=user, name="EdSys Timers")
checks = [
    ("edsys-backup", "EdSys local backup", 26, 2),
    ("edsys-backup-check", "EdSys weekly restic check", 8 * 24, 24),
    ("edsys-git-sync", "EdSys git sync", 2, 1),
    ("edsys-restore-test", "EdSys monthly restore test", 45 * 24, 24),
    ("edsys-offsite-sync", "EdSys offsite sync placeholder", 8 * 24, 24),
]
for slug, name, timeout_hours, grace_hours in checks:
    check, _ = Check.objects.get_or_create(
        project=project,
        slug=slug,
        defaults={
            "name": name,
            "methods": "",
            "timeout": timedelta(hours=timeout_hours),
            "grace": timedelta(hours=grace_hours),
        },
    )
    changed = False
    if check.name != name:
        check.name = name; changed = True
    if check.methods is None:
        check.methods = ""; changed = True
    if changed:
        check.save()
    print(f"{slug}\t{check.code}")
PY
} | docker exec -e HC_ADMIN_USER="$ADMIN_USER" -e HC_ADMIN_EMAIL="$ADMIN_EMAIL" -i "$CONTAINER" sh -lc 'read -r HC_ADMIN_PASSWORD; export HC_ADMIN_PASSWORD; cd /opt/healthchecks && python manage.py shell' >"$TMP_MAP"
chmod 0600 "$TMP_MAP"

sudo install -d -m 0750 -o root -g root "$OUT_DIR"
while IFS=$'\t' read -r slug code; do
  [[ -n "$slug" && -n "$code" ]] || continue
  env_file="$OUT_DIR/$slug.env"
  tmp_env="$(mktemp)"
  printf 'HC_PING_URL=%s/ping/%s\n' "$BASE_URL" "$code" >"$tmp_env"
  sudo install -m 0600 -o root -g root "$tmp_env" "$env_file"
  rm -f "$tmp_env"
done < "$TMP_MAP"

declare -A SYSTEMD_SERVICES=(
  [edsys-backup]=edsys-backup
  [edsys-backup-check]=edsys-backup-check
  [edsys-git-sync]=edsys-git-sync
  [edsys-restore-test]=edsys-restore-test
  [edsys-offsite-sync]=edsys-offsite-sync
)

for slug in "${!SYSTEMD_SERVICES[@]}"; do
  service="${SYSTEMD_SERVICES[$slug]}"
  if ! systemctl list-unit-files "${service}.service" --no-legend 2>/dev/null | grep -q "^${service}\\.service"; then
    continue
  fi
  dropin_dir="/etc/systemd/system/${service}.service.d"
  tmp_dropin="$(mktemp)"
  cat >"$tmp_dropin" <<UNIT
[Service]
EnvironmentFile=-${OUT_DIR}/${slug}.env
ExecStartPost=/usr/local/sbin/edsys-healthchecks-ping success
UNIT
  sudo install -d -m 0755 -o root -g root "$dropin_dir"
  sudo install -m 0644 -o root -g root "$tmp_dropin" "$dropin_dir/20-healthchecks.conf"
  rm -f "$tmp_dropin"
done

if [[ "$WRITE_ADMIN_ENV" == true ]]; then
  tmp_admin="$(mktemp)"
  {
    printf 'HEALTHCHECKS_LOCAL_ADMIN_USERNAME=%s\n' "$ADMIN_USER"
    printf 'HEALTHCHECKS_LOCAL_ADMIN_EMAIL=%s\n' "$ADMIN_EMAIL"
    printf 'HEALTHCHECKS_LOCAL_ADMIN_PASSWORD=%s\n' "$ADMIN_PASSWORD"
  } >"$tmp_admin"
  sudo install -m 0600 -o root -g root "$tmp_admin" "$ADMIN_ENV"
  rm -f "$tmp_admin"
fi

sudo systemctl daemon-reload

echo "Healthchecks records, local admin credentials, private systemd EnvironmentFiles, and systemd drop-ins are in place. Ping URLs and credentials were not printed."
