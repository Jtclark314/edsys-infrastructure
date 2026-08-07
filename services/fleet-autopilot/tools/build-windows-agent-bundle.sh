#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
signing_key="$(realpath "${1:?usage: build-windows-agent-bundle.sh PRIVATE_ED25519_KEY OUTPUT_DIR}")"
output="$(realpath -m "${2:?usage: build-windows-agent-bundle.sh PRIVATE_ED25519_KEY OUTPUT_DIR}")"

[[ -f "${signing_key}" ]] || { printf 'Signing key not found.\n' >&2; exit 1; }
umask 077
[[ ! -e "${output}" ]] || { printf 'Output already exists; choose a new immutable bundle directory.\n' >&2; exit 1; }
mkdir -p "${output}/bundle/adapters"

(
  cd "${root}/windows/dell-agent"
  GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build \
    -trimpath -ldflags='-s -w -buildid=' \
    -o "${output}/edsys-fleet-agent.exe" .
)
install -m 0600 "${root}/windows/dell-agent/config.example.json" "${output}/config.example.json"
install -m 0600 "${root}/windows/dell-agent/install-agent.ps1" "${output}/install-agent.ps1"
install -m 0600 "${root}/windows/node-toolchain-adapter.ps1" "${output}/bundle/adapters/node-toolchain-adapter.ps1"

public_key="$(ssh-keygen -y -f "${signing_key}")"
printf 'edsys-fleet-release %s\n' "${public_key}" >"${output}/allowed_signers"

python3 - "${output}" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
files=[]
for path in sorted(p for p in root.rglob('*') if p.is_file() and p.name not in {'bundle-manifest.json','bundle-manifest.json.sig'}):
    files.append({'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size})
manifest={'schema_version': 1, 'release': 'fleet-windows-agent-0.2.0', 'files': files}
(root/'bundle-manifest.json').write_text(json.dumps(manifest, sort_keys=True, separators=(',',':'))+'\n')
PY

ssh-keygen -Y sign -f "${signing_key}" -n file "${output}/bundle-manifest.json" >/dev/null

printf 'bundle=%s\n' "${output}"
printf 'trusted_signer_sha256=%s\n' "$(sha256sum "${output}/allowed_signers" | awk '{print $1}')"
printf 'agent_sha256=%s\n' "$(sha256sum "${output}/edsys-fleet-agent.exe" | awk '{print $1}')"
