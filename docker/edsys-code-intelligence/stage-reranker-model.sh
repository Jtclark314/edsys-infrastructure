#!/usr/bin/env bash
set -euo pipefail

readonly MODEL_REPO="cross-encoder/ms-marco-MiniLM-L6-v2"
readonly MODEL_REVISION="c5ee24cb16019beea0893ab7796b1df96625c6b8"
readonly MODEL_ROOT="${CODE_INTELLIGENCE_RERANK_MODEL_ROOT:-/mnt/ai-store/codex-intelligence/models/ms-marco-MiniLM-L6-v2-c5ee24cb}"
readonly ONNX_RELATIVE="onnx/model_qint8_avx512_vnni.onnx"
readonly ONNX_SHA256="3573b6b9593cb2f75987a31815d409ca3dd8808629118fd20451bb1a5d90cec7"
readonly HF_BIN="${HF_BIN:-/home/jeremy/.local/bin/hf}"
readonly -a REQUIRED_FILES=(
  "README.md"
  "config.json"
  "special_tokens_map.json"
  "tokenizer.json"
  "tokenizer_config.json"
  "vocab.txt"
  "${ONNX_RELATIVE}"
)

if [[ ${EUID} -ne 0 ]]; then
  printf 'Run this script with sudo so it can provision AI Store state.\n' >&2
  exit 1
fi

if [[ ! -x "${HF_BIN}" ]]; then
  printf 'The Hugging Face hf CLI is required. Install the pinned CLI before continuing.\n' >&2
  exit 1
fi

install -d -m 0755 "${MODEL_ROOT}"

need_stage=false
for relative in "${REQUIRED_FILES[@]}"; do
  [[ -f "${MODEL_ROOT}/${relative}" ]] || need_stage=true
done
if [[ "${need_stage}" == true ]] \
  || ! printf '%s  %s\n' "${ONNX_SHA256}" "${MODEL_ROOT}/${ONNX_RELATIVE}" | sha256sum --check --status; then
  "${HF_BIN}" download "${MODEL_REPO}" \
    "${REQUIRED_FILES[@]}" \
    --revision "${MODEL_REVISION}" \
    --local-dir "${MODEL_ROOT}"
fi

for relative in "${REQUIRED_FILES[@]}"; do
  [[ -f "${MODEL_ROOT}/${relative}" ]] || {
    printf 'Required model file is missing after staging: %s\n' "${relative}" >&2
    exit 1
  }
done
printf '%s  %s\n' "${ONNX_SHA256}" "${MODEL_ROOT}/${ONNX_RELATIVE}" \
  | sha256sum --check
chmod -R a+rX "${MODEL_ROOT}"

python3 - "${MODEL_ROOT}" "${MODEL_REPO}" "${MODEL_REVISION}" "${ONNX_RELATIVE}" "${ONNX_SHA256}" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

root = Path(sys.argv[1])
manifest = {
    "schema_version": 1,
    "source_repository": sys.argv[2],
    "revision": sys.argv[3],
    "artifact": sys.argv[4],
    "sha256": sys.argv[5],
    "staged_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "backup_required": False,
}
path = root / "MODEL_MANIFEST.json"
path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
path.chmod(0o644)
PY

printf 'Pinned CPU reranker model is ready at %s\n' "${MODEL_ROOT}"
