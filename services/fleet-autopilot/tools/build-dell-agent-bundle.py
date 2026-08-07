#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


class BundleError(RuntimeError):
    pass


def run(
    argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-1000:]
        raise BundleError(f"Command failed ({result.returncode}): {' '.join(argv)}: {detail}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_identity(source_root: Path) -> str:
    top = Path(
        run(["git", "rev-parse", "--show-toplevel"], cwd=source_root).stdout.decode().strip()
    )
    branch = run(["git", "branch", "--show-current"], cwd=top).stdout.decode().strip()
    status = run(["git", "status", "--porcelain"], cwd=top).stdout.decode().strip()
    if branch != "main" or status:
        raise BundleError("Dell agent bundle requires a clean authoritative main checkout")
    local = run(["git", "rev-parse", "HEAD"], cwd=top).stdout.decode().strip()
    remote = run(["git", "rev-parse", "origin/main"], cwd=top).stdout.decode().strip()
    if local != remote:
        raise BundleError("Dell agent bundle source must match origin/main")
    return local


def copy_payload(source_root: Path, destination: Path) -> list[Path]:
    agent = source_root / "windows" / "dell-agent"
    mappings = {
        agent / "config.example.json": destination / "config.example.json",
        agent / "install-agent.ps1": destination / "install-agent.ps1",
        source_root / "windows" / "node-toolchain-adapter.ps1": (
            destination / "bundle" / "adapters" / "node-toolchain-adapter.ps1"
        ),
    }
    copied: list[Path] = []
    for source, target in mappings.items():
        if not source.is_file():
            raise BundleError(f"Required bundle source is missing: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def build_agent(source_root: Path, destination: Path) -> Path:
    output = destination / "edsys-fleet-agent.exe"
    env = dict(os.environ)
    env.update({"GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0"})
    run(
        ["go", "build", "-trimpath", "-ldflags=-s -w", "-o", str(output), "."],
        cwd=source_root / "windows" / "dell-agent",
        env=env,
    )
    if not output.is_file() or output.stat().st_size < 1024:
        raise BundleError("Windows agent build did not create a valid executable")
    return output


def deterministic_zip(bundle_root: Path, output: Path) -> None:
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in bundle_root.rglob("*") if item.is_file()):
            relative = path.relative_to(bundle_root).as_posix()
            info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = (0o755 if path.suffix.lower() in {".exe", ".ps1"} else 0o644) << 16
            archive.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)


def build_bundle(source_root: Path, output_root: Path, signing_key: Path) -> dict[str, object]:
    os.umask(0o077)
    commit = source_identity(source_root)
    if not signing_key.is_file() or not Path(f"{signing_key}.pub").is_file():
        raise BundleError("Existing Fleet deployment signing identity is unavailable")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{commit[:12]}"
    final = output_root / run_id
    if final.exists():
        raise BundleError(f"Bundle run already exists: {final}")

    with tempfile.TemporaryDirectory(prefix=f".{run_id}-", dir=output_root) as temporary:
        stage = Path(temporary)
        payload = copy_payload(source_root, stage)
        payload.append(build_agent(source_root, stage))

        public_key = Path(f"{signing_key}.pub").read_text(encoding="utf-8").strip()
        allowed = stage / "allowed_signers"
        allowed.write_text(f"edsys-fleet-release {public_key}\n", encoding="utf-8")
        signer_sha256 = sha256(allowed)
        manifest = {
            "schema_version": 1,
            "component": "fleet-windows-pull-agent",
            "platform": "windows-amd64",
            "source_commit": commit,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "signer_sha256": signer_sha256,
            "files": [
                {
                    "path": path.relative_to(stage).as_posix(),
                    "sha256": sha256(path),
                    "size": path.stat().st_size,
                }
                for path in sorted(payload)
            ],
        }
        manifest_path = stage / "bundle-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        run(["ssh-keygen", "-Y", "sign", "-f", str(signing_key), "-n", "file", str(manifest_path)])
        signature = Path(f"{manifest_path}.sig")
        run(
            [
                "ssh-keygen", "-Y", "verify", "-f", str(allowed), "-I",
                "edsys-fleet-release", "-n", "file", "-s", str(signature),
            ],
            input_bytes=manifest_path.read_bytes(),
        )
        shutil.move(stage, final)

    archive = output_root / f"{run_id}.zip"
    deterministic_zip(final, archive)
    return {
        "status": "prepared",
        "run_id": run_id,
        "source_commit": commit,
        "bundle_root": str(final),
        "archive": str(archive),
        "archive_sha256": sha256(archive),
        "manifest_sha256": sha256(final / "bundle-manifest.json"),
        "signature_sha256": sha256(final / "bundle-manifest.json.sig"),
        "trusted_signer_sha256": sha256(final / "allowed_signers"),
        "mutations_enabled": False,
    }


def main() -> int:
    default_source = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build the signed outbound Dell Fleet agent bundle")
    parser.add_argument("--source-root", type=Path, default=default_source)
    parser.add_argument(
        "--output-root", type=Path,
        default=Path("/mnt/ai-store/private/fleet-autopilot/dell-agent-bundles"),
    )
    parser.add_argument(
        "--signing-key", type=Path,
        default=Path.home() / ".local/share/edsys-fleet-autopilot/deployment-signing-key",
    )
    args = parser.parse_args()
    try:
        value = build_bundle(args.source_root.resolve(), args.output_root.resolve(), args.signing_key.resolve())
    except BundleError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
