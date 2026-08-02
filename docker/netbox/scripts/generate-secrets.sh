#!/usr/bin/env bash
set -euo pipefail

secret_dir=${1:-/etc/edsys-secrets/netbox}
if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root." >&2
  exit 1
fi

install -d -o root -g root -m 0700 "$secret_dir"

create_secret() {
  local name=$1 bytes=$2
  if [[ ! -s "$secret_dir/$name" ]]; then
    umask 077
    openssl rand -base64 "$bytes" | tr -d '\n' >"$secret_dir/$name"
    printf '\n' >>"$secret_dir/$name"
  fi
  chown root:root "$secret_dir/$name"
  # The NetBox image runs as uid 999 with primary gid 0. Root ownership plus
  # group-read permits only root-group container processes to consume the
  # bind-mounted Compose secret; unprivileged host users remain denied.
  chmod 0640 "$secret_dir/$name"
}

create_bearer() {
  local name=$1
  if [[ ! -s "$secret_dir/$name" ]]; then
    umask 077
    python3 - "$secret_dir/$name" <<'PY'
import secrets
import string
import sys

alphabet = string.ascii_letters + string.digits
key = ''.join(secrets.choice(alphabet) for _ in range(12))
token = ''.join(secrets.choice(alphabet) for _ in range(64))
with open(sys.argv[1], 'w', encoding='utf-8') as handle:
    handle.write(f'Bearer nbt_{key}.{token}\n')
PY
  fi
  chown root:root "$secret_dir/$name"
  chmod 0640 "$secret_dir/$name"
}

create_secret db_password 36
create_secret valkey_tasks_password 36
create_secret valkey_cache_password 36
create_secret secret_key 64
create_secret api_token_pepper_1 64
create_secret superuser_password 36
create_bearer sync_api_bearer
create_bearer export_api_bearer

printf 'NetBox secret files are present under %s (values not displayed).\n' "$secret_dir"
