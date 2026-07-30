#!/usr/bin/env bash
set -euo pipefail

STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly STACK_DIR
REPO_ROOT="$(cd "${STACK_DIR}/../.." && pwd)"
readonly REPO_ROOT
readonly APP_ROOT="${CODE_INTELLIGENCE_APP_ROOT:-/home/jeremy/code/edsys-code-intelligence}"
readonly STATE_ROOT="${CODE_INTELLIGENCE_STATE_ROOT:-/mnt/ai-store/codex-intelligence}"
readonly MODEL_ROOT="${CODE_INTELLIGENCE_RERANK_MODEL_ROOT:-${STATE_ROOT}/models/ms-marco-MiniLM-L6-v2-c5ee24cb}"
readonly INDEXER_SOURCE="${REPO_ROOT}/scripts/ops/edsys-code-intelligence-index.py"
readonly INDEXER_TARGET="/usr/local/sbin/edsys-code-intelligence-index"
readonly SYSTEMD_SOURCE="${REPO_ROOT}/scripts/ops/systemd"
readonly MCP_PORT="${CODE_INTELLIGENCE_MCP_PORT:-6071}"
readonly ZOEKT_IMAGE="edsys/zoekt-minimal:2cb19912-go1.26.5"
readonly RERANK_IMAGE="edsys/code-intelligence-reranker:0.1.0"
readonly MCP_IMAGE="edsys/code-intelligence-mcp:local"

log() {
  printf '%s %s\n' "$(date --utc +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

if [[ ${EUID} -ne 0 ]]; then
  fail "Run this installer with sudo."
fi

[[ "$(hostname -s)" == "9950x" ]] || fail "This installer is restricted to host 9950x."
[[ -d /mnt/ai-store ]] || fail "/mnt/ai-store is unavailable."
mountpoint -q /mnt/ai-store || fail "/mnt/ai-store is not a mounted filesystem."
grep -qw avx512_vnni /proc/cpuinfo || fail "The required AVX-512 VNNI CPU feature is unavailable."
command -v docker >/dev/null 2>&1 || fail "Docker is unavailable."
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable."
docker network inspect ai-net >/dev/null 2>&1 || fail "External Docker network ai-net is missing."
[[ -d "${APP_ROOT}/.git" ]] || fail "Application checkout is missing at ${APP_ROOT}."
[[ -f "${APP_ROOT}/requirements.lock" ]] || fail "Application runtime lock is missing."
[[ -f "${APP_ROOT}/requirements.reranker.lock" ]] || fail "Reranker runtime lock is missing."
[[ -f "${STACK_DIR}/repositories.json" ]] || fail "Repository allowlist is missing."
[[ -f "${STACK_DIR}/images/zoekt/Dockerfile" ]] || fail "Minimal Zoekt build is missing."
[[ -f "${INDEXER_SOURCE}" ]] || fail "Indexer source is missing."
chmod 0644 "${STACK_DIR}/repositories.json"

available_kib="$(df --output=avail "${STATE_ROOT%/*}" | tail -1 | tr -d ' ')"
(( available_kib > 10 * 1024 * 1024 )) || fail "AI Store has less than 10 GiB available."

if ss -H -ltn "sport = :${MCP_PORT}" | grep -q .; then
  if ! docker ps --format '{{.Names}} {{.Ports}}' \
    | grep -Eq "^edsys-code-intelligence-mcp .*127\\.0\\.0\\.1:${MCP_PORT}->"; then
    fail "TCP port ${MCP_PORT} is already in use by another service."
  fi
fi

log "Validating repository allowlist."
python3 - "${STACK_DIR}/repositories.json" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

catalog = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
names = set()
for item in catalog["repositories"]:
    if not item.get("enabled", True):
        continue
    name = item["name"]
    if name in names:
        raise SystemExit(f"duplicate repository name: {name}")
    source = Path(item["source"])
    if not source.exists():
        raise SystemExit(f"missing repository: {name}")
    subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--verify", "HEAD^{commit}"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    names.add(name)
print(f"validated {len(names)} repositories")
PY

install -d -m 0755 "${STATE_ROOT}" "${STATE_ROOT}/zoekt" "${STATE_ROOT}/state"
chown jeremy:jeremy "${STATE_ROOT}/zoekt" "${STATE_ROOT}/state"
install -m 0755 "${INDEXER_SOURCE}" "${INDEXER_TARGET}"
install -m 0644 \
  "${SYSTEMD_SOURCE}/edsys-code-intelligence-index.service" \
  /etc/systemd/system/edsys-code-intelligence-index.service
install -m 0644 \
  "${SYSTEMD_SOURCE}/edsys-code-intelligence-index.timer" \
  /etc/systemd/system/edsys-code-intelligence-index.timer
install -m 0644 \
  "${SYSTEMD_SOURCE}/edsys-code-intelligence-full-index.service" \
  /etc/systemd/system/edsys-code-intelligence-full-index.service
install -m 0644 \
  "${SYSTEMD_SOURCE}/edsys-code-intelligence-full-index.timer" \
  /etc/systemd/system/edsys-code-intelligence-full-index.timer
systemctl daemon-reload

log "Staging and verifying the pinned CPU reranker model."
CODE_INTELLIGENCE_RERANK_MODEL_ROOT="${MODEL_ROOT}" \
  "${STACK_DIR}/stage-reranker-model.sh"

log "Validating and building all minimal local runtime images."
CODE_INTELLIGENCE_APP_ROOT="${APP_ROOT}" \
CODE_INTELLIGENCE_STATE_ROOT="${STATE_ROOT}" \
CODE_INTELLIGENCE_RERANK_MODEL_ROOT="${MODEL_ROOT}" \
CODE_INTELLIGENCE_MCP_PORT="${MCP_PORT}" \
  docker compose --project-directory "${STACK_DIR}" -f "${STACK_DIR}/compose.yaml" config -q
CODE_INTELLIGENCE_APP_ROOT="${APP_ROOT}" \
CODE_INTELLIGENCE_STATE_ROOT="${STATE_ROOT}" \
CODE_INTELLIGENCE_RERANK_MODEL_ROOT="${MODEL_ROOT}" \
  docker compose --project-directory "${STACK_DIR}" -f "${STACK_DIR}/compose.yaml" \
  build --pull=false zoekt-search onnx-rerank-cpu code-intelligence-mcp

log "Verifying immutable source labels on local runtime images."
python3 - "${ZOEKT_IMAGE}" "${RERANK_IMAGE}" "${MCP_IMAGE}" <<'PY'
import json
import subprocess
import sys

zoekt_image, rerank_image, mcp_image = sys.argv[1:]

def inspect(image: str) -> dict:
    return json.loads(
        subprocess.check_output(["docker", "image", "inspect", image], text=True)
    )[0]

zoekt = inspect(zoekt_image)
zoekt_labels = zoekt["Config"]["Labels"]
assert zoekt_labels["org.opencontainers.image.revision"] == (
    "2cb19912a4073e5a9895658b7cb135ee4b35733b"
)
assert zoekt_labels["com.edsys.go.version"] == "1.26.5"
assert zoekt_labels["com.edsys.ctags.version"] == "6.1.0"
assert zoekt_labels["com.edsys.runtime"] == "zoekt-minimal"

reranker = inspect(rerank_image)
rerank_labels = reranker["Config"]["Labels"]
assert rerank_labels["com.edsys.runtime"] == "code-intelligence-reranker"
assert rerank_labels["com.edsys.onnxruntime.version"] == "1.28.0"
assert reranker["Config"]["User"] == "10001:10001"

mcp = inspect(mcp_image)
assert mcp["Config"]["Labels"]["com.edsys.runtime"] == "code-intelligence-mcp"
print("local image provenance verified")
PY
ctags_version="$(
  docker run --rm --pull never --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --entrypoint universal-ctags "${ZOEKT_IMAGE}" --version
)"
grep -Fq "Universal Ctags 6.1.0" <<<"${ctags_version}" \
  || fail "Universal Ctags version verification failed."
for feature in +json +interactive +sandbox; do
  grep -Fq "${feature}" <<<"${ctags_version}" \
    || fail "Universal Ctags required feature is missing: ${feature}"
done

if command -v trivy >/dev/null 2>&1; then
  log "Rejecting local runtime images with fixed HIGH or CRITICAL vulnerabilities."
  for image in "${ZOEKT_IMAGE}" "${RERANK_IMAGE}" "${MCP_IMAGE}"; do
    trivy image --quiet --scanners vuln --severity HIGH,CRITICAL \
      --ignore-unfixed --exit-code 1 "${image}"
  done
else
  log "Trivy is unavailable; image vulnerability gate skipped."
fi

log "Proving the indexer excludes dirty, untracked, and ignored working-tree content."
python3 "${STACK_DIR}/scripts/verify-committed-only-index.py"

log "Building and validating a fresh committed-content index."
"${INDEXER_TARGET}" \
  --mode full \
  --catalog "${STACK_DIR}/repositories.json" \
  --state-root "${STATE_ROOT}" \
  --stack-dir "${STACK_DIR}"

mapfile -t legacy_rerankers < <(
  docker ps -aq \
    --filter "label=com.docker.compose.project=edsys-code-intelligence" \
    --filter "label=com.docker.compose.service=infinity-rerank-cpu"
)
if (( ${#legacy_rerankers[@]} > 1 )); then
  fail "Multiple legacy Infinity containers require manual audit."
fi
if (( ${#legacy_rerankers[@]} == 1 )); then
  legacy_id="${legacy_rerankers[0]}"
  legacy_name="$(docker inspect --format '{{.Name}}' "${legacy_id}")"
  legacy_managed="$(docker inspect --format '{{index .Config.Labels "com.edsys.managed"}}' "${legacy_id}")"
  [[ "${legacy_name}" == "/edsys-code-reranker" ]] \
    || fail "Refusing to remove an unexpected legacy container."
  [[ "${legacy_managed}" == "true" ]] \
    || fail "Refusing to remove an unmanaged legacy container."
  log "Replacing the audited legacy Infinity reranker container; its image remains available for rollback."
  docker stop --time 45 "${legacy_id}" >/dev/null
  docker rm "${legacy_id}" >/dev/null
fi

log "Starting the complete hardened stack."
CODE_INTELLIGENCE_APP_ROOT="${APP_ROOT}" \
CODE_INTELLIGENCE_STATE_ROOT="${STATE_ROOT}" \
CODE_INTELLIGENCE_RERANK_MODEL_ROOT="${MODEL_ROOT}" \
CODE_INTELLIGENCE_MCP_PORT="${MCP_PORT}" \
  docker compose --project-directory "${STACK_DIR}" -f "${STACK_DIR}/compose.yaml" \
  up -d --remove-orphans --force-recreate

deadline=$((SECONDS + 180))
while (( SECONDS < deadline )); do
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' edsys-code-intelligence-mcp 2>/dev/null || true)"
  [[ "${health}" == "healthy" ]] && break
  sleep 3
done
[[ "${health:-}" == "healthy" ]] || {
  docker compose --project-directory "${STACK_DIR}" -f "${STACK_DIR}/compose.yaml" ps
  docker logs --tail 100 edsys-code-intelligence-mcp || true
  fail "MCP service did not become healthy."
}

log "Verifying MCP protocol surface and read-only annotations."
docker exec -i edsys-code-intelligence-mcp \
  python - "http://127.0.0.1:6071/mcp" < "${STACK_DIR}/scripts/mcp-smoke.py"

listeners="$(ss -H -ltn "sport = :${MCP_PORT}")"
[[ -n "${listeners}" ]] || fail "MCP loopback listener is missing."
if grep -Evq "127\\.0\\.0\\.1:${MCP_PORT}[[:space:]]" <<<"${listeners}"; then
  printf '%s\n' "${listeners}" >&2
  fail "MCP port is bound beyond IPv4 loopback."
fi

log "Verifying container hardening and CPU-only reranker placement."
python3 - <<'PY'
import json
import subprocess

expected = {
    "edsys-code-search": {"user": "100:101", "readonly": True},
    "edsys-code-reranker": {"user": "10001:10001", "readonly": True},
    "edsys-code-intelligence-mcp": {"user": "10001:10001", "readonly": True},
}
for name, policy in expected.items():
    data = json.loads(
        subprocess.check_output(["docker", "inspect", name], text=True)
    )[0]
    assert data["Config"]["User"] == policy["user"], name
    assert data["HostConfig"]["ReadonlyRootfs"] is policy["readonly"], name
    assert "ALL" in data["HostConfig"]["CapDrop"], name
    assert "no-new-privileges:true" in data["HostConfig"]["SecurityOpt"], name
reranker = json.loads(
    subprocess.check_output(["docker", "inspect", "edsys-code-reranker"], text=True)
)[0]
assert not reranker["HostConfig"].get("DeviceRequests"), "reranker must remain CPU-only"
print("container hardening verified")
PY

systemctl enable --now \
  edsys-code-intelligence-index.timer \
  edsys-code-intelligence-full-index.timer

log "Installation completed successfully."
docker compose --project-directory "${STACK_DIR}" -f "${STACK_DIR}/compose.yaml" ps
systemctl list-timers --all 'edsys-code-intelligence*' --no-pager
curl -fsS "http://127.0.0.1:${MCP_PORT}/ready" | python3 -m json.tool
