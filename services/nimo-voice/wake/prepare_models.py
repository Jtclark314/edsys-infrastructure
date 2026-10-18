"""Explicit preparation only: fetch and verify model assets; never imports audio."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request


def prepare(directory, manifest_path=None):
    manifest = json.loads(Path(manifest_path or Path(__file__).with_name("models.lock.json")).read_text(encoding="utf-8"))
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for item in manifest["models"]:
        name = item["filename"]
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("invalid_model_name")
        destination = directory / name
        if destination.exists() and not destination.is_symlink():
            data = destination.read_bytes()
            if len(data) == item["size"] and hashlib.sha256(data).hexdigest() == item["sha256"]:
                continue
        if not item["url"].startswith("https://"):
            raise ValueError("model_requires_https")
        # Bound downloads even if upstream responds with unexpected content.
        with urllib.request.urlopen(item["url"], timeout=25) as response:
            if not response.url.startswith("https://"):
                raise ValueError("model_redirect_requires_https")
            data = response.read(item["size"] + 1)
        if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("model_verification")
        fd, temporary = tempfile.mkstemp(prefix=".verified-model-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return len(manifest["models"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        count = prepare(args.models_dir)
        print(json.dumps({"prepared_models": count, "microphone_opened": False}))
        return 0
    except Exception:
        print(json.dumps({"error": "model_preparation_failed", "microphone_opened": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
