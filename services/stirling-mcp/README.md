# Private Stirling PDF MCP bridge

This MIT-licensed bridge gives the canonical 9950x Codex hub access to the **available open-source Stirling core REST API** through the official MCP Python SDK. It starts on demand over local stdin/stdout. It has **no HTTP listener, public URL, account, API key, or separate daemon**.

The qualified core 3.2.2 catalog has 148 paths / 149 operations. Three MCP tools keep the initial tool context small:

- `stirling_operations`: searchable, paginated operation catalog.
- `stirling_operation_schema`: exact OpenAPI operation and all referenced schemas, with large schemas saved as private JSON artifacts.
- `stirling_execute`: validated multipart, JSON, URL-encoded, query and path requests; local file upload; secret fields from private local files; async job/status/result/file operations; streamed private output artifacts.

The backend catalog loads lazily and is cached for five minutes. MCP initialize and tool listing work while Stirling is stopped. No stale catalog is used after a failed refresh. HTTP proxy environment variables are ignored, redirects are not followed, and the target origin comes only from the operator's configuration. Returned job or download URLs are data, never automatically fetched; use documented `getJobStatus`, `getJobResult`, `getJobFiles`, and `downloadFile` operation IDs instead.

## Scope and limits

This is our independent REST adapter. It does **not** use or reproduce Stirling's proprietary native MCP implementation, bypass licensing, add paid features, or make browser-only UI workflows into APIs. Catalog presence alone does not guarantee a feature is implemented or that hardware exists. See [the qualified core capability matrix](../../docker/stirling-pdf/CAPABILITIES.md) for actual available features, native/Office/OCR tools, and exclusions.

Excluded upstream capabilities remain excluded: proprietary accounts/SSO/library/sharing/native MCP/AI engines and other separately licensed functions. Hardware certificate stores/PKCS11 require the relevant hardware and runtime support. Browser editing, mobile scanning UI, and client-side workflows continue through the private web interface. PDF-to-CBR lacks a non-OSS RAR encoder; the advertised video conversion route is absent in this core build. URL rendering has the qualified static HTML/CSS behavior and existing SSRF guards; it does not acquire browser cookies or JavaScript execution. MCP does not open additional network access beyond the configured Stirling application's capabilities.

The generic execution tool is conservatively marked destructive and open-world because some API operations cancel/clean up jobs, alter pipelines, sign/redact documents, or fetch a user-selected public/private URL. The user's task must authorize the selected operation and local inputs. The bridge is a trusted local-user integration, **not a filesystem sandbox or authorization boundary against the same OS user**. Document contents and returned JSON are untrusted data, not agent instructions.

## Files and confidential parameters

`files` maps an API binary field to a list of absolute regular-file paths. A scalar binary field takes exactly one path. Final-component symlinks, devices, FIFOs and relative paths are rejected. Files are opened before upload and bounded by their checked length. Limits are 1 GiB per file, 2 GiB combined, and 256 files per operation. Source files are never modified.

`json_body_file` reads a complete JSON request from a selected local file, up to 64 MiB. This supports large PDF text-editor JSON round trips without copying document/base64 data into the model context. It cannot be mixed with other body inputs.

`secret_files` maps a documented string body field, such as `password`, to an explicitly selected UTF-8 local file owned by the current user with mode 0600/0400 (no group/other permissions), maximum 8 KiB. One final newline is stripped. Passwords/private values need not enter MCP tool argument logs. Private key and certificate **files** use the normal binary file fields. Do not place passwords in `parameters` or documentation. The bridge does not log request bodies, upstream errors, secrets, or document text; backend application logging remains governed by Stirling's own deployment.

Every result is saved in a new random directory under the private artifact root (directories 0700, files 0600). Names are generated locally; a recognized Content-Disposition extension may supply a type hint, never a path or filename. No existing user file is overwritten. Responses stream with a 2 GiB maximum; partial outputs are removed on failure. Results return absolute paths, sizes, MIME types and SHA256 checksums. JSON up to 48 KiB is also returned inline; larger JSON, including embedded base64 document data, stays in its artifact. Binary responses are never inlined. There is no automatic artifact expiration or deletion. Needed outputs belong in the user's document backup workflow.

Validation uses the current OpenAPI schema, including required fields and enum/type constraints. It does not silently apply defaults. Supply required and operationally important values explicitly. Two upstream metadata date annotations are display masks; the bridge validates those as `yyyy/MM/dd HH:mm:ss` while returning the original schema unchanged. Spring form objects/arrays are serialized with indexed property binding. Request methods/paths are catalog-selected; arbitrary URLs/headers cannot replace the backend.

## Example flow

1. Search for `merge`, then read `mergePdfs`' exact schema.
2. Call `stirling_execute` with `operation_id="mergePdfs"`, `parameters={"removeCertSign":false,"sortType":"orderProvided"}`, and `files={"fileInput":["/absolute/first.pdf","/absolute/second.pdf"]}`.
3. Read the returned artifact, verify the intended result, and move/copy it to the destination requested by the user if needed.

For long operations, add `asynchronous=true` (Stirling's `async=true` POST flag). Read the returned job ID, poll `getJobStatus` with `path_parameters={"jobId":"..."}`, and when complete use `getJobResult` or `getJobFiles` followed by `downloadFile` with its documented file ID. A non-AutoJob endpoint may ignore the async flag and return a synchronous result. The default HTTP timeout is 120 seconds, selectable from 1–7200. A timeout can occur after backend acceptance; do not blindly retry a mutation. The bridge itself never retries. Stirling can have its own controller retry behavior.

## Installation and recovery

Source owner: `edsys-infrastructure/services/stirling-mcp/`. Official MCP SDK 2.3.0 and all 27 transitive dependencies are pinned with SHA256 hashes in `requirements.lock`; they match the SDK line already qualified for the local operations MCP. This is independent of Codex CLI/Desktop updates. Dependency changes require a new lock and requalification, not a floating install.

Permanent layout:

```text
/home/jeremy/.local/share/edsys-stirling-mcp/
  releases/<immutable-release>/  # source, license, lock, run.sh, isolated venv
  current -> releases/<immutable-release>
/home/jeremy/.local/state/edsys-stirling-mcp/artifacts/  # private generated documents
```

`bash install.sh /absolute/new-release-directory` creates only a new release; it refuses an existing destination. The installer requires existing `uv` and `/usr/bin/python3` (qualified Python 3.12) and performs a hash-checked isolated install. It does not edit Codex configuration, replace global packages, restart an app-server, or change Stirling. `run.sh` starts only the release's official SDK stdio server. Keep the release immutable after qualification and retain old releases.

Register exactly one MCP server using `current/run.sh`, with `STIRLING_BASE_URL=http://10.254.230.50:8080` and `STIRLING_ARTIFACT_DIR=/home/jeremy/.local/state/edsys-stirling-mcp/artifacts`. The backend can be overridden only by trusted operator configuration. Parent commissioning retains the exact pre-change Codex configuration, qualifies a fresh returned-data MCP session, and reloads MCP configuration using the supported app-server command. No backend restart is required.

The private-services backup archives the qualified immutable runtime **including the venv** as an opaque `.tar.zst` under its `images/` directory, alongside source and exact registration/pre-change configuration under private `deploy/`. Venv/current symlinks stay outside the strict application-state backup tree. The source inclusion and final archive are managed by the parent private-services commissioning procedure. Generated artifact documents are outside that application backup and are not automatically uploaded or deleted.

Rollback: first save the current Codex configuration and compare its hash with the post-install hash in the private installation receipt. Restore the exact pre-change configuration only while that hash still matches. If unrelated settings have since changed, remove or restore only the `edsys-stirling` entry and preserve those later changes. Point `current` at a retained qualified release if applicable, then reload MCP configuration. Keep generated outputs; no Stirling data rollback is needed to disable the bridge. Recover a runtime archive to its original absolute release path because Python venv launchers contain absolute paths, then verify initialization plus a returned-data PDF workflow before restoring the current pointer. Installing from the pinned lock is the fallback when the original-path runtime is unavailable.

## Verification

Run offline checks from this source directory with the isolated Python:

```sh
/path/to/release/venv/bin/python -m unittest -v test_bridge.py
```

`verify_live.py` performs an explicitly requested synthetic-only acceptance against the operator-selected backend: fresh stdio initialize and exact tool listing, real catalog/schema discovery, synchronous two-PDF merge, asynchronous merge with polling and file download, secret-file encryption/decryption, PDF page/text checks, SHA256 checks and byte-identical input preservation. The generated test password never enters tool arguments or logs and is removed after its check. It saves evidence/fixtures under the supplied private work directory, never production documents. It requires the existing `pdfinfo`/`pdftotext` utilities. It is a manual verification tool, not a scheduler or monitor.

Primary integration references: [official Python SDK release 2.3.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0), [SDK server documentation](https://py.sdk.modelcontextprotocol.io/servers/), and the deployed core's `/v1/api-docs`. Dependency licenses remain with their distributions. Only the new bridge source is covered by the adjacent MIT license.
