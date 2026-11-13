"""Fetch only commit-pinned layout/table weights; no input documents are accepted."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    os.umask(0o077)
    from huggingface_hub import hf_hub_download

    root = Path(os.environ["DOCLING_MODEL_ROOT"])
    pins = json.loads(Path(__file__).with_name("model-pins.json").read_text())
    stamp = root / "models.json"
    if stamp.is_file():
        old = json.loads(stamp.read_text())
        if old["pins"] != pins:
            raise RuntimeError(
                "Model revision change requires a new cache and reviewed migration"
            )
        for relative, expected in old["files"].items():
            path = root / relative
            if path.is_symlink() or sha256(path) != expected["sha256"]:
                raise RuntimeError(
                    "Existing model cache integrity failure; restore before retrying"
                )
        print(
            json.dumps(
                {"prepared": True, "reused": True, "model_files": len(old["files"])}
            )
        )
        return
    files = {}
    for model in pins["models"]:
        destination = root / "artifacts" / model["repo_id"].replace("/", "--")
        for filename in model["files"]:
            path = Path(
                hf_hub_download(
                    repo_id=model["repo_id"],
                    filename=filename,
                    revision=model["revision"],
                    local_dir=destination,
                    token=False,
                )
            )
            if path.is_symlink():
                raise RuntimeError("Model artifacts must be regular files")
            path.chmod(0o600)
            files[str(path.relative_to(root))] = {
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
    for directory in root.rglob("*"):
        if directory.is_dir():
            directory.chmod(0o700)
    result = {"schema": 1, "pins": pins, "files": files}
    temporary = root / "models.json.new"
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    temporary.replace(stamp)
    print(
        json.dumps(
            {
                "prepared": True,
                "model_files": len(files),
                "bytes": sum(x["bytes"] for x in files.values()),
            }
        )
    )


if __name__ == "__main__":
    main()
