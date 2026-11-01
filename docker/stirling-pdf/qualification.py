#!/usr/bin/env python3
"""Expanded synthetic acceptance for core security, forms and workflow engines.

Outputs and synthetic signing keys must stay in a private directory outside Git.
The --state-root argument is for an isolated candidate only; it creates a named
one-shot watch-folder fixture and removes that watch after checking its output.
"""
import argparse
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

sys.dont_write_bytecode = True
import requests
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from acceptance import make_pdf, pages, text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--only", help="Comma-separated case names to rerun")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    base = args.base_url.rstrip("/")
    session = requests.Session()
    session.trust_env = False
    selected = set(args.only.split(",")) if args.only else None
    previous = root / "qualification.json"
    results = json.loads(previous.read_text()) if selected and previous.exists() else {}
    make_pdf(root / "source.pdf", ["EDSYS SECURITY FIXTURE ONE", "EDSYS SECURITY FIXTURE TWO"])
    source = root / "source.pdf"

    def post(endpoint, fields=None, data=None, output=None):
        handles = []
        files = []
        try:
            for name, path in fields or [("fileInput", source)]:
                handle = path.open("rb")
                handles.append(handle)
                files.append((name, (path.name, handle,
                                      mimetypes.guess_type(path.name)[0] or "application/octet-stream")))
            response = session.post(base + endpoint, files=files, data=data or {}, timeout=240)
            if response.status_code != 200:
                raise AssertionError(f"HTTP {response.status_code}: {response.text[:300]}")
            if output:
                output.write_bytes(response.content)
                assert output.read_bytes().startswith(b"%PDF-"), "Expected returned PDF"
            return response
        finally:
            for handle in handles:
                handle.close()

    def check(name, work):
        if selected and name not in selected:
            return
        started = time.monotonic()
        try:
            detail = work()
            results[name] = {"status": "passed", "detail": detail}
        except Exception as error:
            results[name] = {"status": "failed", "error": str(error)[:600]}
        results[name]["seconds"] = round(time.monotonic() - started, 2)
        print(name + ": " + results[name]["status"], flush=True)
        (root / "qualification.json").write_text(json.dumps(results, indent=2) + "\n")

    def encryption():
        password = secrets.token_urlsafe(24)
        encrypted = root / "encrypted.pdf"
        post("/api/v1/security/add-password", data={"password": password, "ownerPassword": password,
             "keyLength": "256"}, output=encrypted)
        assert subprocess.run(["pdfinfo", str(encrypted)], capture_output=True).returncode != 0
        decrypted = root / "decrypted.pdf"
        post("/api/v1/security/remove-password", [("fileInput", encrypted)],
             {"password": password, "removeAll": "true"}, decrypted)
        assert pages(decrypted) == 2 and "EDSYS SECURITY FIXTURE TWO" in text(decrypted)
        return "AES-256 encryption requires a password; authorized decryption preserves both pages/text."

    def compression():
        output = root / "compressed.pdf"
        post("/api/v1/misc/compress-pdf", data={"optimizeLevel": "2", "linearize": "true",
             "normalize": "true"}, output=output)
        assert pages(output) == 2 and "EDSYS SECURITY FIXTURE ONE" in text(output)
        assert output.stat().st_size <= source.stat().st_size
        return "Compression/QPDF processing preserves two pages and text; upstream keeps the smaller input if optimization increases size."

    def pdfa():
        output = root / "pdfa.pdf"
        post("/api/v1/convert/pdf/pdfa", data={"outputFormat": "pdfa-2b", "strict": "true"}, output=output)
        assert pages(output) == 2 and "EDSYS SECURITY FIXTURE ONE" in text(output)
        gate = post("/api/v1/security/validate-compliance", [("fileInput", output)])
        assert gate.content == output.read_bytes(), "Compliance gate must return the conforming document unchanged"
        validation = post("/api/v1/security/verify-pdf", [("fileInput", output)]).json()
        (root / "pdfa-validation.json").write_text(json.dumps(validation, indent=2) + "\n")
        return {"strict_pdfa2b_conversion": True, "vera_pdf_report": validation}

    def forms():
        form = root / "form.pdf"
        c = canvas.Canvas(str(form))
        c.drawString(50, 760, "EDSYS FORM FIXTURE")
        c.acroForm.textfield(name="reference", x=50, y=690, width=220, height=28, value="EMPTY")
        c.save()
        fields = post("/api/v1/form/fields", [("file", form)]).json()
        assert "reference" in json.dumps(fields)
        output = root / "filled-form.pdf"
        post("/api/v1/form/fill?flatten=true", [("file", form)],
             {"data": json.dumps({"reference": "EDSYS FILLED 482761"})}, output)
        assert "EDSYS FILLED 482761" in text(output)
        return "AcroForm field discovery, fill and flatten preserve the supplied field value."

    def certificate_signing():
        password = secrets.token_urlsafe(24)
        key, cert, bundle = root / "synthetic.key", root / "synthetic.crt", root / "synthetic.p12"
        env = dict(os.environ, STIRLING_TEST_KEY_PASSWORD=password)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=EdSys Synthetic Acceptance",
                        "-addext", "keyUsage=digitalSignature"], check=True, capture_output=True)
        key.chmod(0o600)
        subprocess.run(["openssl", "pkcs12", "-export", "-inkey", str(key), "-in", str(cert),
                        "-out", str(bundle), "-passout", "env:STIRLING_TEST_KEY_PASSWORD"],
                       env=env, check=True, capture_output=True)
        bundle.chmod(0o600)
        output = root / "signed.pdf"
        post("/api/v1/security/cert-sign", [("fileInput", source), ("p12File", bundle)],
             {"certType": "PKCS12", "password": password, "showSignature": "true", "showLogo": "false",
              "name": "EdSys Synthetic Acceptance", "reason": "Disposable test fixture", "pageNumber": "1"}, output)
        signature = subprocess.check_output(["pdfsig", str(output)], text=True)
        assert "Signature is Valid" in signature
        validation = post("/api/v1/security/validate-signature",
                          [("fileInput", output), ("certFile", cert)]).json()
        (root / "signature-validation.json").write_text(json.dumps(validation, indent=2) + "\n")
        return {"cryptographic_integrity": True, "certificate": "synthetic self-signed; not a production identity",
                "app_validation": validation}

    def text_editor():
        extracted = post("/api/v1/convert/pdf/text-editor").json()
        encoded = json.dumps(extracted)
        assert "EDSYS SECURITY FIXTURE ONE" in encoded
        edited = root / "edited.json"
        edited.write_text(encoded.replace("EDSYS SECURITY FIXTURE ONE", "EDSYS EDITED FIXTURE ONE"))
        output = root / "edited.pdf"
        post("/api/v1/convert/text-editor/pdf", [("fileInput", edited)], output=output)
        assert pages(output) == 2 and "EDSYS EDITED FIXTURE ONE" in text(output)
        return "Core JSON text editor extracts content, edits a text run and rebuilds a readable PDF."

    def timestamp():
        output = root / "timestamped.pdf"
        post("/api/v1/security/timestamp-pdf", data={"tsaUrl": "http://timestamp.digicert.com"}, output=output)
        content = output.read_bytes()
        assert b"/ETSI.RFC3161" in content and pages(output) == 2
        matched = re.search(rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]", content)
        assert matched, "Timestamp byte range missing"
        start, first_length, second_start, second_length = map(int, matched.groups())
        hashed = hashlib.sha256(content[start:start + first_length] +
                                content[second_start:second_start + second_length]).digest()
        token = bytes.fromhex(content[first_length:second_start].strip()[1:-1].decode("ascii"))
        assert hashed in token, "Timestamp message imprint differs from returned PDF"
        token_path = root / "timestamp-token.der"
        token_path.write_bytes(token)
        subprocess.run(["openssl", "cms", "-verify", "-noverify", "-inform", "DER", "-in",
                        str(token_path), "-out", os.devnull], check=True, capture_output=True)
        return "RFC3161 token signature and SHA-256 PDF imprint verified; only synthetic digest sent to public DigiCert TSA."

    def tabular_csv():
        table_pdf = root / "table.pdf"
        c = canvas.Canvas(str(table_pdf))
        table = Table([["ITEM", "QUANTITY"], ["EDSYS-BOLT", "482761"], ["EDSYS-NUT", "728416"]],
                      colWidths=[200, 150], rowHeights=32)
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
        table.wrapOn(c, 500, 500)
        table.drawOn(c, 50, 600)
        c.save()
        response = post("/api/v1/convert/pdf/csv", [("fileInput", table_pdf)], {"pageNumbers": "all"})
        if response.content.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                extracted = "\n".join(archive.read(name).decode("utf-8-sig") for name in archive.namelist())
        else:
            extracted = response.content.decode("utf-8-sig")
        assert "EDSYS-BOLT" in extracted and "482761" in extracted
        (root / "extracted-table.csv").write_text(extracted)
        return "Tabula extracts both table labels and values into CSV."

    def redaction():
        output = root / "redacted.pdf"
        post("/api/v1/security/auto-redact", data={"listOfText": "SECURITY", "useRegex": "false",
             "wholeWordSearch": "true", "redactColor": "#000000", "customPadding": "0",
             "convertPDFToImage": "false"}, output=output)
        assert pages(output) == 2 and "SECURITY" not in text(output)
        assert "EDSYS" in text(output)
        return "Search redaction removes target text from extraction while preserving surrounding text."

    def attachments():
        attached = root / "attachment.txt"
        attached.write_text("EDSYS ATTACHMENT 482761\n")
        output = root / "attached.pdf"
        post("/api/v1/misc/add-attachments", [("fileInput", source), ("attachments", attached)], output=output)
        listing = post("/api/v1/misc/list-attachments", [("fileInput", output)]).json()
        assert attached.name in json.dumps(listing)
        response = post("/api/v1/misc/extract-attachments", [("fileInput", output)])
        if response.content.startswith(b"PK"):
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert attached.read_bytes() in [archive.read(name) for name in archive.namelist()]
        else:
            assert response.content == attached.read_bytes()
        return "Embed, list and extract an attachment with byte-identical returned content."

    def engineering_size():
        engineering = root / "engineering-120-pages.pdf"
        c = canvas.Canvas(str(engineering), pagesize=(2592, 3456))
        for page in range(1, 121):
            c.setFont("Helvetica", 32)
            c.drawString(80, 3370, f"EDSYS ENGINEERING SHEET {page:03d}")
            for y in range(160, 3200, 40):
                c.line(80, y, 2500, y)
            for x in range(80, 2500, 40):
                c.line(x, 160, x, 3200)
            c.showPage()
        c.save()
        output = root / "engineering-rotated.pdf"
        post("/api/v1/general/rotate-pdf", [("fileInput", engineering)], {"angle": "90"}, output)
        content = text(output)
        assert pages(output) == 120 and "EDSYS ENGINEERING SHEET 120" in content
        info = subprocess.check_output(["pdfinfo", str(output)], text=True)
        assert "2592 x 3456" in info and re.search(r"^Page rot:\s+90", info, re.M)
        return {"pages": 120, "sheet_inches": [36, 48], "input_bytes": engineering.stat().st_size,
                "output_bytes": output.stat().st_size, "limit": "Vector sheet geometry test; not a 1 GiB load benchmark."}

    def multilingual_ocr():
        cases = [
            ("latin-script", "script_Latin", ["Édition numéro 482761", "Proyecto revisión 728416"],
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", ["482761", "728416", "Proyecto"]),
            ("chinese", "chi_sim", ["工程图纸编号 482761", "施工质量检查"],
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", ["施工质量检查", "482761"]),
        ]
        for name, language, lines, font_path, markers in cases:
            raster = Image.new("RGB", (1836, 2376), "white")
            draw = ImageDraw.Draw(raster)
            font = ImageFont.truetype(font_path, 76)
            for number, line in enumerate(lines):
                draw.text((130, 200 + number * 160), line, font=font, fill="black")
            input_pdf = root / (name + "-input.pdf")
            c = canvas.Canvas(str(input_pdf), pagesize=(612, 792))
            c.drawImage(ImageReader(raster), 0, 0, width=612, height=792)
            c.showPage()
            c.save()
            assert not text(input_pdf).strip()
            output = root / (name + "-ocr.pdf")
            post("/api/v1/misc/ocr-pdf", [("fileInput", input_pdf)],
                 {"languages": language, "ocrType": "force-ocr", "ocrRenderType": "hocr",
                  "sidecar": "false", "deskew": "false", "clean": "false"}, output)
            normalized = re.sub(r"\s", "", text(output))
            assert pages(output) == 1 and all(marker in normalized for marker in markers), \
                f"Expected OCR markers missing for {language}; inspect returned PDF"
        return "Latin script alias and Simplified Chinese model produce searchable marker text from raster-only PDFs."

    def line_art_excluded():
        with source.open("rb") as handle:
            response = session.post(base + "/api/v1/misc/compress-pdf",
                                    files={"fileInput": (source.name, handle, "application/pdf")},
                                    data={"optimizeLevel": "4", "lineArt": "true"}, timeout=60)
        assert response.status_code == 403
        assert "Line art conversion is unavailable" in response.json()["detail"]
        return "Proprietary line-art implementation is absent and explicitly rejected; ordinary compression remains qualified."

    def comic_archive():
        response = post("/api/v1/convert/pdf/cbz")
        archive_path = root / "comic.cbz"
        archive_path.write_bytes(response.content)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            images = [n for n in archive.namelist() if n.lower().endswith((".png", ".jpg", ".jpeg"))]
            assert len(images) == 2
            assert all(Image.open(io.BytesIO(archive.read(n))).width > 0 for n in images)
        output = root / "comic-roundtrip.pdf"
        post("/api/v1/convert/cbz/pdf", [("fileInput", archive_path)], output=output)
        assert pages(output) == 2
        return "PDF-to-CBZ produces two valid raster pages; CBZ input returns a two-page PDF."

    pipeline = {"name": "EdSys synthetic pipeline", "pipeline": [
        {"operation": "/api/v1/general/rotate-pdf", "parameters": {"angle": "90"}},
        {"operation": "/api/v1/security/add-watermark", "parameters": {
            "watermarkType": "text", "watermarkText": "EDSYS PIPELINE", "fontSize": "24",
            "alphabet": "roman",
            "customColor": "#222222",
            "rotation": "0", "opacity": "0.7", "widthSpacer": "100", "heightSpacer": "100",
            "convertPDFToImage": "false"}}
    ], "outputFileName": "{filename}-processed"}

    def verify_pipeline(output):
        assert pages(output) == 2 and "EDSYS PIPELINE" in text(output)
        info = subprocess.check_output(["pdfinfo", str(output)], text=True)
        assert re.search(r"^Page rot:\s+90", info, re.M)

    def pipeline_api():
        output = root / "pipeline.pdf"
        post("/api/v1/pipeline/handleData", data={"json": json.dumps(pipeline)}, output=output)
        verify_pipeline(output)
        return "REST pipeline applies rotation and watermark; both effects verified in output."

    def watch_folder():
        if not args.state_root:
            raise AssertionError("--state-root candidate directory required")
        name = "edsys-qualification-" + uuid.uuid4().hex
        watched = args.state_root / "pipeline/watchedFolders" / name
        finished = args.state_root / "pipeline/finishedFolders" / name
        watched.mkdir(parents=True)
        try:
            watch_config = dict(pipeline, outputDir="{outputFolder}/" + name)
            (watched / "pipeline.json").write_text(json.dumps(watch_config, indent=2) + "\n")
            shutil.copyfile(source, watched / "watch-input.pdf")
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                outputs = list(finished.rglob("*.pdf")) if finished.exists() else []
                if outputs:
                    output = root / "watch-folder.pdf"
                    shutil.copyfile(outputs[0], output)
                    verify_pipeline(output)
                    return "One-shot watched-folder output verified; fixture watch removed afterward."
                time.sleep(1)
            raise AssertionError("No watched-folder PDF within 100 seconds")
        finally:
            shutil.rmtree(watched, ignore_errors=True)
            shutil.rmtree(finished, ignore_errors=True)

    def mobile_transfer():
        session_id = str(uuid.uuid4())
        prefix = base + "/api/v1/mobile-scanner"
        created = session.post(prefix + "/create-session/" + session_id, timeout=15)
        created.raise_for_status()
        raster = root / "mobile.png"
        im = Image.new("RGB", (800, 500), "white")
        ImageDraw.Draw(im).text((40, 60), "EDSYS MOBILE SCAN 482761", fill="black")
        im.save(raster)
        try:
            with raster.open("rb") as f:
                response = session.post(prefix + "/upload/" + session_id,
                    files={"files": ("mobile.png", f, "image/png")}, timeout=30)
            response.raise_for_status()
            listing = session.get(prefix + "/files/" + session_id, timeout=15)
            listing.raise_for_status()
            payload = listing.json()
            assert payload["count"] == 1
            metadata = payload["files"][0]
            downloaded = session.get(prefix + "/download/" + session_id + "/" + metadata["filename"], timeout=30)
            downloaded.raise_for_status()
            if downloaded.content.startswith(b"%PDF-"):
                received = root / "mobile-received.pdf"
                received.write_bytes(downloaded.content)
                assert pages(received) == 1
            else:
                assert Image.open(io.BytesIO(downloaded.content)).size == im.size
            after = session.get(prefix + "/files/" + session_id, timeout=15).json()
            assert after["count"] == 0, "Mobile file should be consumed after download"
            (root / "mobile-transfer.json").write_text(json.dumps(payload, indent=2) + "\n")
            return {"session_creation": True, "image_upload": True,
                    "download_verified": True, "removed_after_download": True}
        finally:
            session.delete(prefix + "/session/" + session_id, timeout=15)

    for name, work in [("encryption", encryption), ("compression", compression), ("pdfa", pdfa),
                       ("forms", forms), ("certificate_signing", certificate_signing),
                       ("timestamp", timestamp),
                       ("text_editor", text_editor), ("tabular_csv", tabular_csv),
                       ("redaction", redaction), ("attachments", attachments),
                       ("engineering_size", engineering_size),
                       ("multilingual_ocr", multilingual_ocr),
                       ("line_art_excluded", line_art_excluded), ("comic_archive", comic_archive),
                       ("pipeline_api", pipeline_api), ("watch_folder", watch_folder),
                       ("mobile_transfer", mobile_transfer)]:
        check(name, work)
    if any(result["status"] != "passed" for result in results.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
