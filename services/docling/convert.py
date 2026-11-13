"""Convert a selected local PDF offline into private, source/page-linked sidecars."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import resource
import sys
import time


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def refuse_network(event: str, args: tuple) -> None:
    if event in ("socket.connect", "socket.connect_ex", "socket.sendto", "socket.bind"):
        raise RuntimeError("Docling conversion is offline; network access is refused")


def resource_limits() -> dict:
    """Read kernel controls; refuse an accidentally unbounded direct invocation."""
    group = Path("/proc/self/cgroup").read_text().strip().split(":", 2)[2]
    directory = Path("/sys/fs/cgroup") / group.lstrip("/")
    memory = (directory / "memory.max").read_text().strip()
    swap = (directory / "memory.swap.max").read_text().strip()
    quota, period = (directory / "cpu.max").read_text().split()
    if (
        memory == "max"
        or int(memory) > 6 * 1024**3
        or swap != "0"
        or quota == "max"
        or int(quota) / int(period) > 4
    ):
        raise RuntimeError(
            "Launch through edsys-docling with its CPU, RAM and swap limits"
        )
    return {
        "memory_max_bytes": int(memory),
        "swap_max_bytes": 0,
        "cpu_max_cores": int(quota) / int(period),
    }


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    convert = commands.add_parser(
        "convert", help="Convert one local PDF; requires a new output directory"
    )
    convert.add_argument("source", type=Path)
    convert.add_argument("--output", type=Path, required=True)
    convert.add_argument("--ocr", choices=("auto", "force", "off"), default="auto")
    convert.add_argument("--max-pages", type=int, default=200)
    args = parser.parse_args()
    limits = resource_limits()
    if not 1 <= args.max_pages <= 1000:
        parser.error("--max-pages must be between 1 and 1000")
    if args.source.is_symlink() or not args.source.is_file():
        parser.error("source must be an existing regular local PDF")
    source = args.source.resolve()
    if source.suffix.lower() != ".pdf" or source.stat().st_size > 100 * 1024 * 1024:
        parser.error("source must be a PDF no larger than 100 MiB")
    if args.output.exists() or args.output.is_symlink():
        parser.error("output already exists; select a new directory")
    output = args.output.absolute()
    if not output.parent.is_dir() or output.parent.is_symlink():
        parser.error("output parent must be an existing directory")
    model_root = Path(os.environ["DOCLING_MODEL_ROOT"])
    cache_manifest = model_root / "models.json"
    if not cache_manifest.is_file():
        parser.error("run edsys-docling prepare-models once before offline conversion")
    models = json.loads(cache_manifest.read_text())
    for relative, expected in models["files"].items():
        path = model_root / relative
        if path.is_symlink() or sha256(path) != expected["sha256"]:
            raise RuntimeError("Model cache integrity failure")
    sys.addaudithook(refuse_network)
    logging.basicConfig(level=logging.ERROR)
    import torch
    from docling.datamodel.accelerator_options import (
        AcceleratorDevice,
        AcceleratorOptions,
    )
    from docling.datamodel.base_models import ConversionStatus, InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import ImageRefMode

    if torch.version.cuda is not None or torch.cuda.is_available():
        raise RuntimeError("This toolkit requires the pinned CPU-only PyTorch build")
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    options = PdfPipelineOptions(
        artifacts_path=model_root / "artifacts",
        accelerator_options=AcceleratorOptions(
            device=AcceleratorDevice.CPU, num_threads=4
        ),
        enable_remote_services=False,
        allow_external_plugins=False,
        do_ocr=args.ocr != "off",
        ocr_options=TesseractCliOcrOptions(
            lang=["eng"],
            force_full_page_ocr=args.ocr == "force",
            tesseract_cmd="/usr/bin/tesseract",
        ),
        do_table_structure=True,
        document_timeout=1800,
        generate_page_images=False,
        generate_picture_images=False,
        do_picture_classification=False,
        do_picture_description=False,
        do_code_enrichment=False,
        do_formula_enrichment=False,
    )
    options.layout_options.model_spec.revision = models["pins"]["models"][0]["revision"]
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )
    original_digest = sha256(source)
    started = time.monotonic()
    result = converter.convert(
        source, max_num_pages=args.max_pages, max_file_size=100 * 1024 * 1024
    )
    if result.status != ConversionStatus.SUCCESS:
        raise RuntimeError(
            f"Conversion incomplete ({result.status}); no successful output published"
        )
    if sha256(source) != original_digest:
        raise RuntimeError("Source changed during conversion; no output published")
    document = result.document
    output.mkdir(mode=0o700)
    (output / "pages").mkdir(mode=0o700)
    document.save_as_json(output / "document.json", image_mode=ImageRefMode.PLACEHOLDER)
    pages = []
    for page_no in sorted(document.pages):
        filename = f"pages/page-{page_no:04d}.md"
        content = (
            f"# Source page {page_no}\n\nSource: [{source.name}]({source.as_uri()}#page={page_no})\n\nSHA-256: `{original_digest}`\n\n"
            + document.export_to_markdown(page_no=page_no)
        )
        (output / filename).write_text(content + "\n", encoding="utf-8")
        pages.append(
            {
                "page_no": page_no,
                "markdown": filename,
                "source_uri": f"{source.as_uri()}#page={page_no}",
            }
        )
    joined = f"# {source.name}\n\nSource SHA-256: `{original_digest}`\n\n"
    joined += "\n\n".join((output / p["markdown"]).read_text() for p in pages)
    (output / "document.md").write_text(joined, encoding="utf-8")
    items = []
    for item, _level in document.iterate_items():
        if hasattr(item, "prov"):
            items.append(
                {
                    "ref": item.self_ref,
                    "label": str(item.label),
                    "pages": sorted({p.page_no for p in item.prov}),
                }
            )
    artifacts = {}
    for path in output.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
            artifacts[str(path.relative_to(output))] = {
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
    manifest = {
        "schema": 1,
        "status": "complete",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "docling_version": importlib.metadata.version("docling"),
        "source": {
            "path": str(source),
            "uri": source.as_uri(),
            "sha256": original_digest,
            "bytes": source.stat().st_size,
        },
        "conversion": {
            "offline": True,
            "device": "cpu",
            "ocr": args.ocr,
            "threads": 4,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "process_peak_rss_mib": round(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2
            ),
        },
        "models_manifest_sha256": sha256(cache_manifest),
        "resource_limits": limits,
        "pages": pages,
        "items": items,
        "artifacts": artifacts,
    }
    (output / "provenance.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "output": str(output),
                "pages": len(pages),
                "tables": len(document.tables),
                "seconds": manifest["conversion"]["elapsed_seconds"],
                "source_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()
