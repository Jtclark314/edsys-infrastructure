#!/usr/bin/env python3
"""Repair v3.2.2 URL conversion's request-thread-only redirect construction.

Compile only the exact pinned core controller. Keep every other release JAR
entry byte-identical, including nested dependencies and their ZIP storage mode.
The private runtime never includes the compiler or annotation processor.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path("/patch")
RELATIVE_CLASS = "stirling/software/SPDF/controller/api/converters/ConvertWebsiteToPDF.class"
SOURCE = ROOT / "ConvertWebsiteToPDF.java"
EXPECTED_SOURCE = "86ff7308dea8f3f1b6963003bad566155a8b91b94e798ae65f247fbf5d9f337f"
EXPECTED_JAR = "370a4218624fa4b3cf5460c886f03de01bfcfd2d4893009202a81e53b5f3344d"


def sha(data):
    return hashlib.sha256(data).hexdigest()


original_source = SOURCE.read_bytes()
assert sha(original_source) == EXPECTED_SOURCE, "Unexpected upstream controller; review patch again"
original = ROOT / "original.jar"
assert sha(original.read_bytes()) == EXPECTED_JAR, "Unexpected core release artifact"
old = 'ServletUriComponentsBuilder.fromCurrentContextPath().path("/url-to-pdf")'
new = 'UriComponentsBuilder.fromPath("/url-to-pdf")'
source = original_source.decode()
assert source.count(old) == 1
source = source.replace(old, new).replace(
    "import org.springframework.web.servlet.support.ServletUriComponentsBuilder;\n", "")
source = source.replace("import stirling.software.common.service.CustomPDFDocumentFactory;",
                        "import stirling.software.common.service.CustomPDFDocumentFactory;\n"
                        "import stirling.software.common.service.SsrfProtectionService;")
source = source.replace("    private final TempFileManager tempFileManager;",
                        "    private final TempFileManager tempFileManager;\n"
                        "    private final SsrfProtectionService ssrfProtectionService;")
# This deployment explicitly permits both public and private HTTP(S) pages.
# The upstream HEAD preflight unconditionally rejects private addresses even
# when other SSRF policy is configured otherwise. Keep validation local to this
# controller, reuse the existing configurable URL policy, and use its bounded
# GET to determine reachability. No global network helper is changed.
start = source.index("            // Validate the URL format (relaxed: only invalid if BOTH checks fail)")
end = source.index("\n        }\n\n        if (location != null)", start)
assert "GeneralUtils.isURLReachable(URL)" in source[start:end]
source = source[:start] + '''            boolean validHttpUrl;
            try {
                URI target = URI.create(URL);
                validHttpUrl =
                        ("http".equalsIgnoreCase(target.getScheme())
                                || "https".equalsIgnoreCase(target.getScheme()))
                                && target.getHost() != null
                                && !target.getHost().isBlank();
            } catch (IllegalArgumentException | NullPointerException e) {
                validHttpUrl = false;
            }
            if (!validHttpUrl) {
                location =
                        uriComponentsBuilder
                                .queryParam("error", "error.invalidUrlFormat")
                                .build()
                                .toUri();
            } else if (!ssrfProtectionService.isUrlAllowed(URL)) {
                location =
                        uriComponentsBuilder
                                .queryParam("error", "error.urlNotReachable")
                                .build()
                                .toUri();
            }''' + source[end:]
SOURCE.write_text(source)

classes, deps, compiled = ROOT / "classes", ROOT / "deps", ROOT / "compiled"
for directory in (classes, deps, compiled):
    directory.mkdir()
with zipfile.ZipFile(original) as archive:
    assert not any("proprietary" in name.lower() for name in archive.namelist())
    for member in archive.infolist():
        name = member.filename
        destination = None
        if name.startswith("BOOT-INF/classes/") and name.endswith(".class"):
            destination = classes / name.removeprefix("BOOT-INF/classes/")
        elif name.startswith("BOOT-INF/lib/") and name.endswith(".jar"):
            destination = deps / Path(name).name
        if destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(member))

subprocess.run([
    "/opt/java/openjdk/bin/javac", "--release", "25", "-parameters", "-encoding", "UTF-8",
    "-classpath", f"{classes}:{deps}/*:{ROOT / 'lombok.jar'}",
    "-processorpath", str(ROOT / "lombok.jar"), "-d", str(compiled), str(SOURCE)
], check=True)
assert [str(p.relative_to(compiled)) for p in compiled.rglob("*.class")] == [RELATIVE_CLASS]
entry_name = "BOOT-INF/classes/" + RELATIVE_CLASS
replacement = (compiled / RELATIVE_CLASS).read_bytes()
patched = ROOT / "patched.jar"
with zipfile.ZipFile(original) as before, zipfile.ZipFile(patched, "w") as after:
    for member in before.infolist():
        after.writestr(member, replacement if member.filename == entry_name else before.read(member))
with zipfile.ZipFile(original) as before, zipfile.ZipFile(patched) as after:
    assert before.namelist() == after.namelist()
    changed = [name for name in before.namelist() if before.read(name) != after.read(name)]
    assert changed == [entry_name]
    assert all(before.getinfo(n).compress_type == after.getinfo(n).compress_type for n in before.namelist())

receipt = {
    "upstream": "v3.2.2",
    "reason": "AutoJob runs even synchronous requests on worker threads without servlet request attributes.",
    "repair": "Use same-origin relative error redirects, HTTP(S)-with-host validation and the existing configurable SsrfProtectionService instead of the unconditional private-address HEAD block. Bounded GET, redirect refusal and file-reference protection remain unchanged.",
    "upstream_jar_sha256": EXPECTED_JAR,
    "patched_jar_sha256": sha(patched.read_bytes()),
    "upstream_source_sha256": EXPECTED_SOURCE,
    "patched_source_sha256": sha(SOURCE.read_bytes()),
    "changed_jar_entries": changed,
    "compiled_class_sha256": sha(replacement),
}
(ROOT / "url-context-patch.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
