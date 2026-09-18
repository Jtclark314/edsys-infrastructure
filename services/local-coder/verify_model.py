#!/usr/bin/env python3
"""Verify the official model manifest and optionally every weight byte."""
import argparse
import hashlib
import json
from pathlib import Path


def verify(full: bool = False) -> dict:
    lock = json.loads(Path(__file__).with_name("artifacts.json").read_text())
    root = Path("/mnt/ai-store/models/ollama/models")
    manifest = root / "manifests/registry.ollama.ai/library/qwen3.6/35b-a3b-q8_0"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != lock["source_manifest_sha256"]:
        raise RuntimeError("Official source manifest changed; review before deployment")
    blob = root / ("blobs/sha256-" + lock["model_blob_sha256"])
    if blob.stat().st_size != lock["model_blob_bytes"]:
        raise RuntimeError("Model weight size mismatch")
    if full:
        digest = hashlib.sha256()
        with blob.open("rb") as handle:
            for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != lock["model_blob_sha256"]:
            raise RuntimeError("Model weight checksum mismatch")
    return {"manifest_verified": True, "weight_size_verified": True,
            "full_weight_hash_verified": full}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    print(json.dumps(verify(parser.parse_args().full)))
