#!/usr/bin/env python3
"""Synthetic acceptance against a specifically supplied Stirling instance.

Requires Python requests, reportlab and Pillow, plus pdfinfo/pdftotext/pdftoppm.
Writes only synthetic documents and a sanitized receipt to the supplied private directory.
"""
import argparse
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def text(path):
    return subprocess.check_output(["pdftotext", str(path), "-"], text=True)


def pages(path):
    info = subprocess.check_output(["pdfinfo", str(path)], text=True)
    return int(re.search(r"^Pages:\s+(\d+)", info, re.M).group(1))


def make_pdf(path, markers):
    pdf = canvas.Canvas(str(path), pagesize=letter)
    for marker in markers:
        pdf.setFont("Helvetica-Bold", 22)
        pdf.drawString(54, 730, marker)
        pdf.setFont("Helvetica", 14)
        pdf.drawString(54, 690, "Synthetic EdSys acceptance fixture. No project records.")
        pdf.showPage()
    pdf.save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    session = requests.Session()
    session.trust_env = False
    base = args.base_url.rstrip("/")
    health = session.get(base + "/api/v1/info/status", timeout=15)
    health.raise_for_status()
    assert health.status_code == 200

    def post(endpoint, inputs, data, output):
        opened = []
        try:
            for path in inputs:
                opened.append(("fileInput", (path.name, path.open("rb"), "application/pdf")))
            result = session.post(base + endpoint, files=opened, data=data, timeout=180)
            result.raise_for_status()
            assert result.status_code == 200, result.status_code
            output.write_bytes(result.content)
            return result.headers.get("Content-Type", "")
        finally:
            for _, (_, handle, _) in opened:
                handle.close()

    make_pdf(root / "alpha.pdf", ["EDSYS ALPHA PAGE ONE", "EDSYS ALPHA PAGE TWO"])
    make_pdf(root / "beta.pdf", ["EDSYS BETA PAGE THREE"])
    merged = root / "merged.pdf"
    post("/api/v1/general/merge-pdfs", [root / "alpha.pdf", root / "beta.pdf"],
         {"sortType": "orderProvided", "removeCertSign": "false"}, merged)
    assert merged.read_bytes().startswith(b"%PDF-")
    assert pages(merged) == 3
    merged_text = text(merged)
    markers = ["EDSYS ALPHA PAGE ONE", "EDSYS ALPHA PAGE TWO", "EDSYS BETA PAGE THREE"]
    positions = [merged_text.index(marker) for marker in markers]
    assert positions == sorted(positions)

    split = root / "split.zip"
    post("/api/v1/general/split-pages", [merged], {"pageNumbers": "all"}, split)
    split_receipts = []
    with zipfile.ZipFile(split) as archive:
        members = [item for item in archive.namelist() if item.lower().endswith(".pdf")]
        assert len(members) == 3
        for number, member in enumerate(members, 1):
            output = root / f"split-{number}.pdf"
            output.write_bytes(archive.read(member))
            assert pages(output) == 1
            found = [marker for marker in markers if marker in text(output)]
            assert len(found) == 1
            split_receipts.append(found[0])
    assert sorted(split_receipts) == sorted(markers)

    # Raster-only input proves an OCR pass added searchable text.
    scan = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(scan)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 52)
    lines = ["EDSYS OCR ACCEPTANCE", "Invoice number 482761", "Total amount 125.50", "Local document processing"]
    for line_no, line in enumerate(lines):
        draw.text((150, 210 + line_no * 120), line, font=font, fill="black")
    scan.save(root / "scan.png")
    scan_pdf = root / "scan.pdf"
    pdf = canvas.Canvas(str(scan_pdf), pagesize=letter)
    pdf.drawImage(ImageReader(scan), 0, 0, width=612, height=792)
    pdf.showPage()
    pdf.save()
    assert not text(scan_pdf).strip()
    ocr = root / "ocr.pdf"
    post("/api/v1/misc/ocr-pdf", [scan_pdf],
         {"languages": "eng", "ocrType": "force-ocr", "ocrRenderType": "hocr",
          "sidecar": "false", "deskew": "false", "clean": "false"}, ocr)
    assert ocr.read_bytes().startswith(b"%PDF-")
    assert pages(ocr) == 1
    ocr_text = text(ocr)
    for marker in ("EDSYS OCR ACCEPTANCE", "482761", "125.50", "Local document processing"):
        assert marker in ocr_text, f"OCR did not return expected synthetic text: {marker}"

    for name in ("merged", "ocr"):
        subprocess.run(["pdftoppm", "-r", "80", "-f", "1", "-singlefile", "-png",
                        str(root / f"{name}.pdf"), str(root / f"{name}-preview")], check=True)
    receipt = {
        "health_http": health.status_code,
        "merge": {"pages": pages(merged), "page_order_verified": True},
        "split": {"files": len(split_receipts), "pages_each": 1, "all_markers_verified": True},
        "ocr": {"input_searchable": False, "output_pages": pages(ocr), "expected_text_verified": True},
        "sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   for name in ("merged.pdf", "split.zip", "ocr.pdf")},
        "visual_review": "Inspect merged-preview.png and ocr-preview.png separately.",
    }
    (root / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
