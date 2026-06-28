#!/usr/bin/env bash
set -euo pipefail

STACK_DIR="/srv/edsys/edsys-infrastructure/docker/9950x-workhorse"
STATE_DIR="${EDSYS_WORKHORSE_STATE_DIR:-/opt/edsys-workhorse}"
ENV_DIR="$STATE_DIR/env"
ENV_FILE="$ENV_DIR/9950x-workhorse.env"
SEARXNG_DIR="$STATE_DIR/searxng"
SEARXNG_SETTINGS="$SEARXNG_DIR/settings.yml"
LOKI_CONFIG_DIR="$STATE_DIR/loki-config"
LOKI_CONFIG_FILE="$LOKI_CONFIG_DIR/loki-config.yaml"

rand_b64() { openssl rand -base64 "${1:-32}" | tr -d '\n'; }
rand_hex() { openssl rand -hex "${1:-32}" | tr -d '\n'; }
rand_key() { printf 'sk-%s' "$(rand_hex 24)"; }

owner="${SUDO_USER:-$USER}"
group="$(id -gn "$owner" 2>/dev/null || id -gn)"
install_cmd=(install -d -m 0750 -o "$owner" -g "$group")
if [[ ! -w "$(dirname "$STATE_DIR")" ]]; then
  install_cmd=(sudo install -d -m 0750 -o "$owner" -g "$group")
fi
"${install_cmd[@]}" "$STATE_DIR" "$ENV_DIR" \
  "$STATE_DIR/backrest/data" "$STATE_DIR/backrest/config" "$STATE_DIR/backrest/cache" \
  "$STATE_DIR/ntfy/cache" "$STATE_DIR/ntfy/etc" \
  "$STATE_DIR/karakeep/data" "$STATE_DIR/loki" "$STATE_DIR/alloy" \
  "$LOKI_CONFIG_DIR" \
  "$STATE_DIR/scrutiny/config" "$STATE_DIR/scrutiny/influxdb" \
  "$STATE_DIR/renovate/cache" "$STATE_DIR/crowdsec/config" "$STATE_DIR/crowdsec/data" \
  "$SEARXNG_DIR"

# The upstream Loki image runs as UID/GID 10001 and needs write access to /loki.
if [[ "$(id -u)" -eq 0 ]]; then
  chown 10001:10001 "$STATE_DIR/loki"
  chmod 0750 "$STATE_DIR/loki"
elif command -v sudo >/dev/null 2>&1; then
  sudo chown 10001:10001 "$STATE_DIR/loki"
  sudo chmod 0750 "$STATE_DIR/loki"
else
  chmod 0777 "$STATE_DIR/loki"
  echo "WARNING: sudo unavailable; made $STATE_DIR/loki world-writable so the Loki container can start." >&2
fi

if [[ ! -f "$ENV_FILE" ]]; then
  umask 077
  cat > "$ENV_FILE" <<ENV
EDSYS_WORKHORSE_STATE_DIR=$STATE_DIR
DOZZLE_HOST_PORT=3013
NTFY_HOST_PORT=3015
NTFY_BASE_URL=http://127.0.0.1:3015
SEARXNG_HOST_PORT=3017
SEARXNG_BASE_URL=http://127.0.0.1:3017/
KARAKEEP_HOST_PORT=3018
KARAKEEP_VERSION=release
KARAKEEP_NEXTAUTH_URL=http://127.0.0.1:3018
KARAKEEP_DISABLE_SIGNUPS=false
KARAKEEP_NEXTAUTH_SECRET=$(rand_b64 32)
KARAKEEP_MEILI_MASTER_KEY=$(rand_b64 32)
HEALTHCHECKS_HOST_PORT=3014
HEALTHCHECKS_SITE_ROOT=http://127.0.0.1:3014
HEALTHCHECKS_ALLOWED_HOSTS=127.0.0.1,localhost
HEALTHCHECKS_DB_NAME=healthchecks
HEALTHCHECKS_DB_USER=healthchecks
HEALTHCHECKS_DB_PASSWORD=$(rand_b64 32)
HEALTHCHECKS_SECRET_KEY=$(rand_b64 48)
BACKREST_HOST_PORT=9898
SCRUTINY_HOST_PORT=3016
SCRUTINY_COLLECTOR_PORT=8086
LOKI_HOST_PORT=3100
LITELLM_HOST_PORT=4000
LITELLM_DB_NAME=litellm
LITELLM_DB_USER=litellm
LITELLM_DB_PASSWORD=$(rand_b64 32)
LITELLM_MASTER_KEY=$(rand_key)
LITELLM_SALT_KEY=$(rand_b64 32)
LITELLM_UI_USERNAME=admin
LITELLM_UI_PASSWORD=$(rand_b64 24)
LANGFUSE_HOST_PORT=3012
LANGFUSE_NEXTAUTH_URL=http://127.0.0.1:3012
LANGFUSE_POSTGRES_PASSWORD=$(rand_b64 32)
LANGFUSE_CLICKHOUSE_PASSWORD=$(rand_hex 24)
LANGFUSE_MINIO_ROOT_PASSWORD=$(rand_b64 32)
LANGFUSE_REDIS_AUTH=$(rand_b64 32)
LANGFUSE_SALT=$(rand_b64 32)
LANGFUSE_ENCRYPTION_KEY=$(rand_hex 32)
LANGFUSE_NEXTAUTH_SECRET=$(rand_b64 32)
LANGFUSE_TELEMETRY_ENABLED=false
RENOVATE_TOKEN=
DOCKER_GROUP_GID=$(getent group docker | cut -d: -f3 || echo 999)
ENV
  chmod 0600 "$ENV_FILE"
  echo "Created private environment file at $ENV_FILE"
else
  echo "Private environment file already exists at $ENV_FILE"
fi

if [[ ! -f "$SEARXNG_SETTINGS" ]]; then
  umask 077
  cat > "$SEARXNG_SETTINGS" <<YAML
use_default_settings: true
server:
  bind_address: "0.0.0.0"
  port: 8080
  secret_key: "$(rand_b64 32)"
  base_url: "http://127.0.0.1:3017/"
redis:
  url: "redis://searxng-redis:6379/0"
search:
  safe_search: 1
  autocomplete: "duckduckgo"
YAML
  chmod 0600 "$SEARXNG_SETTINGS"
  echo "Created private SearXNG settings at $SEARXNG_SETTINGS"
else
  echo "Private SearXNG settings already exists at $SEARXNG_SETTINGS"
fi

if [[ ! -f "$LOKI_CONFIG_FILE" ]]; then
  install -m 0644 "$STACK_DIR/config/loki/loki-config.yaml" "$LOKI_CONFIG_FILE"
  echo "Installed Loki runtime config at $LOKI_CONFIG_FILE"
else
  echo "Loki runtime config already exists at $LOKI_CONFIG_FILE"
fi

ln -sfn "$ENV_FILE" "$STACK_DIR/.env"
echo "Linked $STACK_DIR/.env -> $ENV_FILE"
echo "No secret values were printed."
