#!/usr/bin/env bash
set -euo pipefail

# Smoke-test EdSys LiteLLM broker using a service-scoped key file.
# This script prints no secrets and intentionally avoids recurring cloud-token spend.

KEY_FILE="${LITELLM_SERVICE_KEY_FILE:-/opt/edsys-workhorse/litellm/service-keys/edsys-ai-gateway.env}"
TIMEOUT_SECONDS="${LITELLM_SMOKE_TIMEOUT_SECONDS:-45}"

ping_hc() {
  local status="${1:-success}"
  if [[ -n "${HC_PING_URL:-}" ]] && command -v edsys-healthchecks-ping >/dev/null 2>&1; then
    HC_TIMEOUT_SECONDS="${HC_TIMEOUT_SECONDS:-10}" edsys-healthchecks-ping "$status" || true
  fi
}

on_exit() {
  local rc=$?
  if [[ $rc -eq 0 ]]; then
    ping_hc success
  else
    ping_hc fail
  fi
  exit "$rc"
}
trap on_exit EXIT

if [[ ! -r "$KEY_FILE" ]]; then
  echo "LiteLLM service key file is not readable: $KEY_FILE" >&2
  exit 1
fi

python3 - <<'PY' "$KEY_FILE" "$TIMEOUT_SECONDS"
import json
import sys
import time
import urllib.request

key_file = sys.argv[1]
timeout = int(float(sys.argv[2]))
env = {}
with open(key_file, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v

base = env["LITELLM_BASE_URL"].rstrip("/")
api_key = env["LITELLM_API_KEY"]
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def request(method, endpoint, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + endpoint, data=data, method=method, headers=headers)
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.load(r)
    return round(time.time() - start, 2), body

elapsed_models, models = request("GET", "/models")
model_ids = {item.get("id") for item in models.get("data", [])}
required = {"edsys-chat-cloud", "edsys-coder-cloud", "edsys-voice-fast", "edsys-embeddings-fast", "gpt-5-chat-latest", "gpt-5-codex"}
missing = sorted(required - model_ids)
if missing:
    raise SystemExit(f"missing required LiteLLM models: {', '.join(missing)}")

elapsed_chat, chat = request(
    "POST",
    "/chat/completions",
    {"model": "edsys-voice-fast", "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 5, "temperature": 0},
)
if not chat.get("choices"):
    raise SystemExit("edsys-voice-fast returned no chat choices")

elapsed_embed, embed = request(
    "POST",
    "/embeddings",
    {"model": "edsys-embeddings-fast", "input": "EdSys LiteLLM broker smoke test"},
)
embedding = ((embed.get("data") or [{}])[0] or {}).get("embedding")
if not isinstance(embedding, list) or not embedding:
    raise SystemExit("edsys-embeddings-fast returned no embedding vector")

print(json.dumps({
    "ok": True,
    "models_visible": len(model_ids),
    "model_registry_elapsed_s": elapsed_models,
    "voice_fast_elapsed_s": elapsed_chat,
    "embedding_fast_elapsed_s": elapsed_embed,
    "embedding_dim": len(embedding),
}, sort_keys=True))
PY
