#!/usr/bin/env python3
"""Generate credential-free text/table and image-only PDF qualification fixtures."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(args.directory / "text-table.pdf"), pagesize=(612, 792))
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(60, 725, "Docling qualification - page 1")
    pdf.setFont("Helvetica", 14)
    pdf.drawString(60, 690, "NORTH source marker 37")
    rows = [
        ("Zone", "Required airflow", "Status"),
        ("North", "450 CFM", "Verified"),
        ("South", "625 CFM", "Pending"),
        ("Roof", "900 CFM", "Verified"),
    ]
    widths = [118, 190, 160]
    top = 620
    pdf.setFont("Helvetica", 15)
    for row, values in enumerate(rows):
        x = 60
        for col, value in enumerate(values):
            pdf.rect(x, top - (row + 1) * 48, widths[col], 48)
            pdf.drawString(x + 10, top - row * 48 - 30, value)
            x += widths[col]
    pdf.showPage()
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(60, 725, "Docling qualification - page 2")
    pdf.setFont("Helvetica", 15)
    pdf.drawString(60, 675, "SOUTH source marker 68")
    pdf.drawString(60, 640, "Acceptance note: source pages remain separate.")
    pdf.showPage()
    pdf.save()
    image = Image.new("RGB", (1800, 2200), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 48)
    heading = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60
    )
    draw.text((130, 180), "SCANNED QUALIFICATION", font=heading, fill="black")
    for n, line in enumerate(
        (
            "Inspection code ALPHA 4286",
            "Door seal reviewed on source page 1.",
            "Record test: pressure 25 Pa.",
        )
    ):
        draw.text((130, 350 + n * 115), line, font=font, fill="black")
    image.save(args.directory / "scanned-source.png")
    scan = canvas.Canvas(str(args.directory / "scanned.pdf"), pagesize=(612, 748))
    scan.drawImage(ImageReader(image), 0, 0, width=612, height=748)
    scan.showPage()
    scan.save()


if __name__ == "__main__":
    main()
