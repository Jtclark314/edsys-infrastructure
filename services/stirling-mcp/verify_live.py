"""Explicit synthetic-only live acceptance using a fresh official SDK stdio session."""
import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def pdf(path, label):
    stream = f"BT /F1 18 Tf 72 720 Td ({label}) Tj ET".encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(data)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def returned_value(result):
    if result.is_error:
        raise AssertionError("MCP tool returned a sanitized error: " + " ".join(item.text for item in result.content if item.type == "text"))
    value = getattr(result, "structured_content", None)
    if value is not None:
        return value
    for item in result.content:
        if item.type == "text":
            return json.loads(item.text)
    raise AssertionError("No structured tool result")


def check_pdf(artifact, labels):
    path = Path(artifact["path"])
    assert digest(path) == artifact["sha256"], "Returned artifact checksum mismatch"
    info = subprocess.run(["pdfinfo", str(path)], check=True, capture_output=True, text=True, timeout=20).stdout
    count = int(re.search(r"^Pages:\s+(\d+)", info, re.M)[1])
    assert count == len(labels), "Merged page count mismatch"
    text = subprocess.run(["pdftotext", str(path), "-"], check=True, capture_output=True, text=True, timeout=20).stdout
    assert all(label in text for label in labels), "Returned PDF does not contain both inputs"
    return {"pages": count, "text_checks": len(labels), "bytes": artifact["bytes"], "sha256": artifact["sha256"], "path": str(path)}


async def verify(args):
    os.umask(0o077)
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, mode=0o700, exist_ok=True)
    labels = ["MCP-STIRLING-FIRST", "MCP-STIRLING-SECOND"]
    inputs = [work / "first.pdf", work / "second.pdf"]
    for path, label in zip(inputs, labels):
        if path.exists():
            raise SystemExit("Choose a new work directory; fixture paths already exist.")
        pdf(path, label)
    before = [digest(path) for path in inputs]
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "transport": "official SDK stdio", "calls": []}
    parameters = StdioServerParameters(command=args.python, args=[str(Path(args.server).resolve())], env={
        "STIRLING_BASE_URL": args.backend, "STIRLING_ARTIFACT_DIR": str(work / "artifacts"), "PYTHONDONTWRITEBYTECODE": "1"})
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = sorted(tool.name for tool in listed.tools)
            assert names == ["stirling_execute", "stirling_operation_schema", "stirling_operations"]
            for tool in listed.tools:
                assert tool.annotations.read_only_hint is (tool.name != "stirling_execute")
                assert tool.annotations.destructive_hint is (tool.name == "stirling_execute")
            report["tools"] = names

            async def call(name, arguments):
                result = returned_value(await session.call_tool(name, arguments, read_timeout_seconds=150))
                report["calls"].append({"tool": name, "operation": arguments.get("operation_id"), "ok": True})
                return result

            listing = await call("stirling_operations", {"query": "merge"})
            report["catalog_operations"] = listing["catalog_operations"]
            assert any(item["operation_id"] == "mergePdfs" for item in listing["operations"])
            schema = await call("stirling_operation_schema", {"operation_id": "mergePdfs"})
            assert schema["method"] == "POST" and "MergePdfsRequest" in str(schema["components"])
            request = {"operation_id": "mergePdfs", "parameters": {"removeCertSign": False,
                       "sortType": "orderProvided", "generateToc": False}, "files": {"fileInput": [str(path) for path in inputs]}}
            merged = await call("stirling_execute", request)
            report["sync_merge"] = check_pdf(merged["artifact"], labels)
            submitted = await call("stirling_execute", {**request, "asynchronous": True})
            job_id = submitted["json"]["jobId"]
            for _ in range(45):
                status = await call("stirling_execute", {"operation_id": "getJobStatus", "path_parameters": {"jobId": job_id}})
                state = status["json"].get("jobResult", status["json"])
                assert not state.get("error"), "Async PDF merge failed"
                if state.get("complete") is True or str(state.get("status", "")).lower() in {"completed", "complete", "done", "success", "succeeded"}:
                    break
                await asyncio.sleep(1)
            else:
                raise AssertionError("Async job did not finish within the bounded acceptance window")
            result = await call("stirling_execute", {"operation_id": "getJobResult", "path_parameters": {"jobId": job_id}})
            report["async_merge"] = check_pdf(result["artifact"], labels)
            job_files = await call("stirling_execute", {"operation_id": "getJobFiles", "path_parameters": {"jobId": job_id}})
            file_id = job_files["json"]["files"][0]["fileId"]
            downloaded = await call("stirling_execute", {"operation_id": "downloadFile", "path_parameters": {"fileId": file_id}})
            report["download"] = check_pdf(downloaded["artifact"], labels)
            assert downloaded["artifact"]["sha256"] == result["artifact"]["sha256"]
            secret_path = work / "synthetic-password.txt"
            with secret_path.open("x", encoding="utf-8") as handle:
                handle.write(secrets.token_urlsafe(36) + "\n")
            try:
                security = {"keyLength": 256, "preserveExisting": False,
                            **{name: False for name in ("preventAssembly", "preventExtractContent", "preventExtractForAccessibility",
                                                       "preventFillInForm", "preventModify", "preventModifyAnnotations",
                                                       "preventPrinting", "preventPrintingFaithful")}}
                encrypted = await call("stirling_execute", {"operation_id": "addPassword", "parameters": security,
                                       "files": {"fileInput": [str(inputs[0])]},
                                       "secret_files": {"password": str(secret_path), "ownerPassword": str(secret_path)}})
                locked = subprocess.run(["pdfinfo", encrypted["artifact"]["path"]], capture_output=True, timeout=20)
                assert locked.returncode != 0, "The encrypted PDF unexpectedly opens without a password"
                decrypted = await call("stirling_execute", {"operation_id": "removePassword", "parameters": {"removeAll": True},
                                       "files": {"fileInput": [encrypted["artifact"]["path"]]},
                                       "secret_files": {"password": str(secret_path), "ownerPassword": str(secret_path), "sourcePassword": str(secret_path)}})
                report["secret_file_roundtrip"] = {"locked_without_password": True, "decrypted": check_pdf(decrypted["artifact"], labels[:1]),
                                                   "secret_in_tool_arguments": False}
            finally:
                secret_path.unlink(missing_ok=True)
    assert before == [digest(path) for path in inputs], "An input was modified"
    report["inputs_preserved"] = True
    report["success"] = True
    evidence = work / "acceptance.json"
    evidence.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"success": True, "catalog_operations": report["catalog_operations"], "tool_count": len(report["tools"]),
                      "sync_pages": report["sync_merge"]["pages"], "async_pages": report["async_merge"]["pages"],
                      "download_verified": True, "secret_file_roundtrip": True, "inputs_preserved": True, "evidence": str(evidence)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://10.254.230.50:8080")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--server", default=str(Path(__file__).with_name("server.py")))
    asyncio.run(verify(parser.parse_args()))
