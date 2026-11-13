#!/usr/bin/python3
"""Independently validate exported-file integrity and the generated PDF fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_output(directory: Path) -> dict:
    manifest = json.loads((directory / "provenance.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["docling_version"] == "2.150.0"
    assert manifest["conversion"]["offline"] is True
    assert manifest["conversion"]["device"] == "cpu"
    assert manifest["resource_limits"] == {
        "memory_max_bytes": 6 * 1024**3,
        "swap_max_bytes": 0,
        "cpu_max_cores": 4.0,
    }
    assert sha256(Path(manifest["source"]["path"])) == manifest["source"]["sha256"]
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for name, expected in manifest["artifacts"].items():
        path = directory / name
        assert (
            path.resolve().is_relative_to(directory.resolve()) and not path.is_symlink()
        )
        assert sha256(path) == expected["sha256"]
        assert path.stat().st_size == expected["bytes"]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    document = json.loads((directory / "document.json").read_text())
    assert set(map(int, document["pages"])) == {p["page_no"] for p in manifest["pages"]}
    for page in manifest["pages"]:
        assert page["source_uri"].endswith(f"#page={page['page_no']}")
        text = (directory / page["markdown"]).read_text()
        assert page["source_uri"] in text and manifest["source"]["sha256"] in text
    page_numbers = {p["page_no"] for p in manifest["pages"]}
    for item in manifest["items"]:
        assert set(item["pages"]).issubset(page_numbers)
    return {
        "pages": len(page_numbers),
        "artifacts": len(manifest["artifacts"]),
        "source_unchanged": True,
    }


def verify_fixtures(directory: Path) -> dict:
    text_dir = directory / "text-table-output"
    scan_dir = directory / "scanned-output"
    text_result = verify_output(text_dir)
    scan_result = verify_output(scan_dir)
    first = (text_dir / "pages/page-0001.md").read_text()
    second = (text_dir / "pages/page-0002.md").read_text()
    assert "NORTH source marker 37" in first and "SOUTH source marker 68" not in first
    assert "SOUTH source marker 68" in second and "NORTH source marker 37" not in second
    data = json.loads((text_dir / "document.json").read_text())["tables"][0]["data"]
    expected = [
        ["Zone", "Required airflow", "Status"],
        ["North", "450 CFM", "Verified"],
        ["South", "625 CFM", "Pending"],
        ["Roof", "900 CFM", "Verified"],
    ]
    cells = {
        (c["start_row_offset_idx"], c["start_col_offset_idx"]): c["text"]
        for c in data["table_cells"]
    }
    assert data["num_rows"] == 4 and data["num_cols"] == 3
    for row, values in enumerate(expected):
        for col, value in enumerate(values):
            assert cells[(row, col)] == value
    scan_text = (scan_dir / "document.md").read_text()
    assert "ALPHA 4286" in scan_text and "pressure 25 Pa" in scan_text
    original_scan = directory / "fixtures/scanned.pdf"
    assert not subprocess.check_output(["pdftotext", str(original_scan), "-"]).strip()
    return {
        "text_table": text_result,
        "scanned_ocr": scan_result,
        "table_cells_exact": 12,
        "scan_had_no_native_text": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--fixtures", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            verify_fixtures(args.directory)
            if args.fixtures
            else verify_output(args.directory),
            indent=2,
        )
    )
