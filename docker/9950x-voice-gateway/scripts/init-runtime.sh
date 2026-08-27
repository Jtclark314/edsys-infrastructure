#!/usr/bin/env bash
set -euo pipefail
umask 077

readonly secret_dir="/etc/edsys-secrets/voice-gateway"
readonly ca_dir="/etc/edsys-secrets/voice-gateway-ca"
readonly image_ref="${1:-}"

[[ ${EUID} -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ ${image_ref} =~ ^sha256:[0-9a-f]{64}$ || ${image_ref} =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "usage: init-runtime.sh <immutable-image-id-or-registry-digest>" >&2
  exit 2
}

install -d -m 0700 "${secret_dir}" "${secret_dir}/tls" "${ca_dir}"

if [[ ! -e ${ca_dir}/ca.key ]]; then
  openssl ecparam -name prime256v1 -genkey -noout -out "${ca_dir}/ca.key"
  openssl req -x509 -new -sha256 -days 3650 \
    -key "${ca_dir}/ca.key" \
    -subj "/CN=EdSys Voice Gateway Internal CA" \
    -out "${ca_dir}/ca.crt"
fi

if [[ ! -e ${secret_dir}/tls/tls.key ]]; then
  openssl ecparam -name prime256v1 -genkey -noout -out "${secret_dir}/tls/tls.key"
  openssl req -new -sha256 \
    -key "${secret_dir}/tls/tls.key" \
    -subj "/CN=edsys-voice-gateway.edsys.home" \
    -out "${secret_dir}/tls/tls.csr"
  openssl x509 -req -sha256 -days 825 \
    -in "${secret_dir}/tls/tls.csr" \
    -CA "${ca_dir}/ca.crt" \
    -CAkey "${ca_dir}/ca.key" \
    -CAcreateserial \
    -extfile <(printf '%s\n' \
      'subjectAltName=DNS:edsys-voice-gateway.edsys.home,IP:192.168.50.50,IP:127.0.0.1' \
      'extendedKeyUsage=serverAuth' \
      'keyUsage=digitalSignature,keyAgreement') \
    -out "${secret_dir}/tls/tls.crt"
  install -m 0644 "${ca_dir}/ca.crt" "${secret_dir}/tls/ca.crt"
  shred -u "${secret_dir}/tls/tls.csr"
fi

if [[ ! -e ${secret_dir}/deploy.env ]]; then
  cat >"${secret_dir}/deploy.env" <<EOF
EDSYS_VOICE_GATEWAY_IMAGE=${image_ref}
EDSYS_VOICE_GATEWAY_PULL_POLICY=never
EDSYS_VOICE_GATEWAY_LAN_IP=192.168.50.50
EDSYS_VOICE_GATEWAY_PORT=8055
EDSYS_VOICE_GATEWAY_METRICS_PORT=8056
EOF
fi

if [[ ! -e ${secret_dir}/gateway.env ]]; then
  gateway_token=$(openssl rand -hex 32)
  operator_token=$(openssl rand -hex 32)
  cat >"${secret_dir}/gateway.env" <<EOF
EDSYS_VOICE_ENVIRONMENT=production
EDSYS_VOICE_GATEWAY_BEARER_TOKEN=${gateway_token}
EDSYS_VOICE_OPERATOR_BEARER_TOKEN=${operator_token}
EDSYS_VOICE_HOME_ASSISTANT_URL=http://192.168.50.75:8123
EDSYS_VOICE_HOME_ASSISTANT_TOKEN=TO_BE_CONFIRMED
EDSYS_VOICE_HOME_ASSISTANT_AGENT_ID=conversation.home_assistant
EDSYS_VOICE_LITELLM_URL=http://192.168.50.50:4000/v1
EDSYS_VOICE_LITELLM_API_KEY=TO_BE_CONFIRMED
EDSYS_VOICE_LITELLM_CLOUD_MODEL=edsys-voice-cloud
EDSYS_VOICE_LITELLM_LOCAL_MODEL=edsys-voice-quality
EDSYS_VOICE_RAG_QUERY_URL=TO_BE_CONFIRMED
EDSYS_VOICE_RAG_API_KEY=TO_BE_CONFIRMED
EDSYS_VOICE_CONTROL_API_URL=http://192.168.50.50:8099
EDSYS_VOICE_WORKFLOW_BASE_URL=
EDSYS_VOICE_WORKFLOW_BEARER_TOKEN=
EDSYS_VOICE_APPROVED_WORKFLOWS_PATH=/app/config/approved-workflows.json
EDSYS_VOICE_VERIFY_UPSTREAM_TLS=true
EDSYS_VOICE_SESSION_TTL_SECONDS=300
EDSYS_VOICE_UPSTREAM_TIMEOUT_SECONDS=5
EDSYS_VOICE_MODEL_TIMEOUT_SECONDS=15
EDSYS_VOICE_LISTEN_HOST=0.0.0.0
EDSYS_VOICE_LISTEN_PORT=8443
EDSYS_VOICE_METRICS_HOST=0.0.0.0
EDSYS_VOICE_METRICS_PORT=9101
EDSYS_VOICE_TLS_CERT_FILE=/run/edsys-voice-tls/tls.crt
EDSYS_VOICE_TLS_KEY_FILE=/run/edsys-voice-tls/tls.key
EOF
fi

chown root:root "${secret_dir}/deploy.env" "${secret_dir}/gateway.env"
chmod 0600 "${secret_dir}/deploy.env" "${secret_dir}/gateway.env"
chown root:root "${secret_dir}/tls/ca.crt" "${secret_dir}/tls/tls.crt"
chmod 0644 "${secret_dir}/tls/ca.crt" "${secret_dir}/tls/tls.crt"
chown root:10001 "${secret_dir}/tls"
chmod 0750 "${secret_dir}/tls"
chown 10001:10001 "${secret_dir}/tls/tls.key"
chmod 0400 "${secret_dir}/tls/tls.key"
chmod 0600 "${ca_dir}/ca.key"
chmod 0644 "${ca_dir}/ca.crt"

echo "private runtime initialized; upstream service credentials remain an explicit operator gate"
