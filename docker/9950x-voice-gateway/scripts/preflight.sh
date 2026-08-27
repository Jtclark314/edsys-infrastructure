#!/usr/bin/env bash
set -euo pipefail

readonly stack_dir="/srv/edsys/edsys-infrastructure/docker/9950x-voice-gateway"
readonly secret_dir="/etc/edsys-secrets/voice-gateway"
readonly deploy_env="${secret_dir}/deploy.env"
readonly gateway_env="${secret_dir}/gateway.env"
readonly cert_file="${secret_dir}/tls/tls.crt"
readonly key_file="${secret_dir}/tls/tls.key"

[[ ${EUID} -eq 0 ]] || { echo "run as root" >&2; exit 1; }
for path in "${deploy_env}" "${gateway_env}" "${cert_file}" "${key_file}"; do
  [[ -r ${path} ]] || { echo "required private runtime file is unavailable" >&2; exit 1; }
done

[[ $(stat -c '%u:%g:%a' "${secret_dir}/tls") == '0:10001:750' ]] || {
  echo "TLS directory ownership or mode is unsafe for the non-root container" >&2
  exit 1
}
[[ $(stat -c '%u:%g:%a' "${key_file}") == '10001:10001:400' ]] || {
  echo "TLS key ownership or mode is unsafe for the non-root container" >&2
  exit 1
}

python3 - "${gateway_env}" <<'PY'
import sys
from pathlib import Path

values = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key] = value
required = (
    "EDSYS_VOICE_GATEWAY_BEARER_TOKEN",
    "EDSYS_VOICE_OPERATOR_BEARER_TOKEN",
    "EDSYS_VOICE_HOME_ASSISTANT_TOKEN",
    "EDSYS_VOICE_LITELLM_API_KEY",
    "EDSYS_VOICE_RAG_QUERY_URL",
    "EDSYS_VOICE_RAG_API_KEY",
)
for key in required:
    value = values.get(key, "")
    if not value or "TO_BE_CONFIRMED" in value or "provided-locally" in value:
        raise SystemExit(f"required private setting is not ready: {key}")
for key in (
    "EDSYS_VOICE_GATEWAY_BEARER_TOKEN",
    "EDSYS_VOICE_OPERATOR_BEARER_TOKEN",
    "EDSYS_VOICE_HOME_ASSISTANT_TOKEN",
):
    if len(values[key]) < 32:
        raise SystemExit(f"private credential is unexpectedly short: {key}")
for key in ("EDSYS_VOICE_LITELLM_API_KEY", "EDSYS_VOICE_RAG_API_KEY"):
    if len(values[key]) < 24:
        raise SystemExit(f"private credential is unexpectedly short: {key}")
PY

image_ref=$(sed -n 's/^EDSYS_VOICE_GATEWAY_IMAGE=//p' "${deploy_env}")
[[ ${image_ref} =~ ^sha256:[0-9a-f]{64}$ || ${image_ref} =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "voice image must use an immutable sha256 digest" >&2
  exit 1
}
docker image inspect "${image_ref}" >/dev/null

openssl x509 -checkend 86400 -noout -in "${cert_file}" >/dev/null
openssl x509 -in "${cert_file}" -noout -ext subjectAltName | grep -Fq 'IP Address:192.168.50.50'

cert_pub=$(openssl x509 -in "${cert_file}" -pubkey -noout | sha256sum | cut -d' ' -f1)
key_pub=$(openssl pkey -in "${key_file}" -pubout 2>/dev/null | sha256sum | cut -d' ' -f1)
[[ ${cert_pub} == "${key_pub}" ]] || { echo "TLS certificate/key mismatch" >&2; exit 1; }

python3 - "${stack_dir}/config/approved-workflows.json" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert set(data) == {"schema", "workflows"}
assert data["schema"] == "edsys.voice-workflows.v1"
assert isinstance(data["workflows"], list)
PY

set -a
# shellcheck disable=SC1090
source "${deploy_env}"
set +a
docker compose --project-directory "${stack_dir}" -f "${stack_dir}/compose.yaml" config --quiet
