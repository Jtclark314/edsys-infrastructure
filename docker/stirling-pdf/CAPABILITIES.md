# Open-source capability contract

This inventory applies to Stirling PDF **3.2.2 core**, local build `edsys6`.
“Enabled” means present in the core application and supported by installed engines;
“qualified” identifies representative returned-output checks, not a claim that
every option, language, device or document layout has been tested. Detailed
synthetic PDFs, rendered previews, hashes, engine outputs and browser receipts
remain with the private release record.

## Core capabilities and qualification

| Family | Available tools/workflows | Representative qualification |
| --- | --- | --- |
| Viewer and canvas editing | PDF viewing, page thumbnails, selection, annotations, text/image insertion, drawing, highlights, stamps, signatures, comparison, ruler/measurement, undo/edit/export | Browser upload/preview/merge/download; core JSON text edit and PDF reconstruction; complete text preserved. Ruler and individual annotation modes remain enabled but are not individually tested |
| Page organization | Merge, remove/extract/reorder/rotate, page/range/chapter/section/size splits, crop/scale, multi-page layout, booklet imposition, overlay and single-page conversion | Ordered three-page merge; three one-page splits; rotation of 120 vector sheets at 36×48 inches; two-step pipeline |
| Text/metadata/navigation | Extract text, CSV tables, document information, fonts/images/bookmarks, metadata update, table of contents, auto rename, JavaScript inspection/removal | Tabula CSV cell values; text-editor JSON edit/export; attachment byte checks. Metadata/navigation options remain enabled and are not individually exercised |
| Attachments and portfolios | Add, list, extract single/all and batch attachment management; create/flatten portfolios | Embed/list/extract a text attachment; returned bytes identical. Portfolio options are enabled but not individually exercised |
| OCR and scans | OCR, searchable PDF, sidecar text, auto rotate, deskew/cleanup options, remove blanks, image scan extraction, scanner effect, contrast/color inversion | Raster-only English invoice; Latin-script and Chinese marker recognition; OpenCV two-card extraction; image-only input confirmed |
| Image/vector formats | PNG/JPEG/TIFF/GIF/BMP/WebP and supported image inputs, PDF raster export, SVG/vector conversion, image extraction, CBZ creation/reading and supported comic archive reading | PNG/JPEG/SVG to PDF; PDF to PNG/JPEG/WebP; SVG raster shape/color comparison (text is outlined); two-page PDF→CBZ→PDF roundtrip |
| Office/documents | LibreOffice/UNO office input and PDF-to-Word/RTF/presentation/text/HTML/XML paths; bundled Stirling Office Convert beta selectable per request | DOCX→PDF and PDF→DOCX; native and cross-engine roundtrips; see layout limitation below |
| HTML, web, email, Markdown | HTML/ZIP, Markdown and EML conversion; HTTP(S) URL conversion for public, LAN and Tailscale targets | Static markers from uploaded files and public/private fixture pages; invalid/file URL and embedded file-reference rejection |
| Ebooks | Supported ebook input and PDF-to-EPUB through Calibre | EPUB→PDF, PDF→EPUB and roundtrip markers |
| Compression/repair | PDF compression, optimization, QPDF normalization/linearization, Ghostscript repair, sanitization, annotation removal and grayscale options | Returned compressed PDF preserves pages/text; strict PDF/A conversion. Fine-grained repair/sanitization options remain enabled; proprietary line-art conversion is excluded |
| PDF standards | PDF/A B-level conversion, PDF/X where offered by core, metadata-declared compliance inspection and pipeline compliance gate | Strict PDF/A-2b output; veraPDF reports compliant with zero failures; compliance gate returns identical bytes. Other levels/options are not individually tested |
| Passwords/permissions/redaction | Add/remove passwords, permission changes, manual/search redaction and certificate-signature removal | AES-256 encryption requires password; authorized decryption restores text; search redaction removes selected text while preserving surroundings |
| Forms | List/add/edit/delete/fill fields, unlock forms, flatten, extract/import supported form data | Discover a field, fill its value and flatten to readable page text |
| Signing and timestamps | Draw/type/upload signatures, shared image assets, uploaded PEM/PKCS12/PFX/JKS certificate signing, signature verification, RFC3161 timestamping | Disposable PKCS12 signature validated by application and Poppler; timestamp token signature and document digest verified. No production identity/certificate is created |
| Automation | Core Automate UI, browser-saved automation recipes, REST pipeline execution, server JSON configurations and watched folders | Browser-created rotation+watermark recipe, execution and verified PDF download; matching API pipeline and disposable watch-folder output. Watch removed afterward; no production recurring job is installed |
| Mobile transfer | QR/session image upload, scanner transfer, mobile drawn/photo/typed signature transfer | Create session, upload synthetic image, list/download it, confirm consumed-file cleanup, close session. Camera hardware is device-dependent |
| API and jobs | Core REST/OpenAPI, synchronous/asynchronous jobs, progress/status and pipeline integration | Live OpenAPI and endpoint inventory; actual tool requests through job executor; final HTTPS route acceptance |
| Local browser state | File library, personal signatures, saved automations/preferences | Available per browser; server backup intentionally does not claim to synchronize it |
| Read aloud | Core viewer uses browser Web Speech API and available voices | Feature remains available. Voice availability and audio output depend on device/browser; synthetic UI inspection only |

All available core endpoint groups remain enabled. The only explicit endpoint
removals are **DocParse** (proprietary) and **PDF-to-video** (the upstream controller
is disabled). Native core dependency checks also exclude CBR writing and the
proprietary form-detection/PDF-UA/accessibility features. The live inventory has
99 generic endpoint flags and 148 OpenAPI paths; flags include UI aliases and
documentation pages and must not be mistaken for 149 distinct PDF tools.

## Optional credentials, client state and excluded components

| Capability | Status | Reason |
| --- | --- | --- |
| Telegram pipeline bot | Credential required; unconfigured | Core implementation exists; no bot token supplied and no external notifications or polling task activated |
| Email/SMTP | Unconfigured | No mail account supplied; account/password-reset/server-sharing workflows also require excluded proprietary services |
| Browser read aloud | Client dependent | Upstream chooses an available language voice and does not filter on `SpeechSynthesisVoice.localService`; a nonlocal voice may send text to the browser/OS provider. No server TTS service or account is added |
| Browser-local saved library/automations/signatures | Enabled, per browser | Accessible on each authorized device; does not imply cross-device server synchronization |
| Native users, login, API keys, SSO/LDAP/admin account management | License excluded | Proprietary backend is absent; the deployment's Tailscale identity proxy supplies access control |
| Server library/storage/sharing and associated workflows | License excluded | Implemented under proprietary namespace; browser-local files and core server pipelines remain available |
| MCP server, AI Engine, DocParse, cloud indexing, automatic form detection | License excluded | Proprietary backend components; configuration disabled and code absent |
| PDF/UA tagging/accessibility reports and PDF/A A-level tagger | License excluded | Requires proprietary implementation; core B-level conversion/verification remains available |
| Server-managed certificate store | License excluded | Uploaded certificate signing remains available in core |
| Compress → line-art sub-option | License excluded | The optional implementation is `app/proprietary/.../ImageMagickLineArtConversionService`; core returns explicit HTTP 403. Installing the OSS ImageMagick binary does not provide that proprietary service; ordinary compression/grayscale remain available |
| Drive/cloud document integrations and paid portal/Processor | License excluded/unconfigured | Proprietary integration paths and/or external OAuth accounts absent; public build keys blank |
| Vendor tool-recommendation API | License excluded | Official core client suppresses its first 404, disables further calls and falls back to static recommendations without a user-facing error |
| PDF→CBR writing | Non-OSS binary excluded | Requires `rar`; no shareware encoder installed. Supported CBZ and archive-reading paths remain available |
| PDF→video | Upstream omitted | Controller mapping is commented out and FFmpeg is absent from the vendor base; advertised metadata alone is not an implementation |
| JavaScript-rendered web pages | Upstream limitation | Core HTML/URL engine is WeasyPrint, not Chromium. Static HTML/CSS renders; script-only content does not. No unrelated browser service is invented |
| Document translation | Not an independent core tool | UI localization and voice-language selection do not translate document content; excluded AI integrations are not silently substituted |

`SYSTEM_ENABLEALPHAFUNCTIONALITY=true` is exposed in app configuration. Inspection
found no additional active core tool gated by this flag in this release; the
upstream template explicitly says enabling it may make no changes. The separate
**Stirling Office Convert 0.2.2 beta** is MIT, bundled and selectable per request.
LibreOffice remains the default. A forced LibreOffice PDF→DOCX→PDF roundtrip
clipped a synthetic numeric field; native and mixed-engine roundtrips preserved
it. Converted layouts should be reviewed, especially complex engineering tables.

## Engine and OCR inventory

Observed from the qualified engine image:

| Engine | Version |
| --- | --- |
| Java runtime | Temurin 25.0.4+7 |
| LibreOffice / unoserver |26.2.6.3 /3.7 |
| Tesseract / OCRmyPDF |5.3.4 /17.13.0 |
| Ghostscript / QPDF |10.08.0 /12.4.2 |
| WeasyPrint / Calibre |70.0 /9.15.0 |
| ImageMagick / OpenCV |7.1.2-32 /5.0.0.93 |
| PDFBox / native PDFium bridge |3.0.8 / jpdfium 1.1.6 |
| Tabula / Batik / BouncyCastle |1.0.5 /1.19 /1.85 |
| Stirling Office Convert beta |0.2.2 |

ImageMagick is present in the official engine base, but the line-art integration
above is proprietary and absent. FontForge 20230101 is an installed optional font
conversion helper; its individual font-format paths are not separately exercised.

There are **162 unique trained models**: 123 language/variant models, 37 script
models and two support models (`osd`, `equ`). Because upstream selectors enumerate
only the root directory, 37 immutable `script_` aliases expose the script models
in the picker; they do not duplicate model contents. The orientation model is
excluded from the language picker as upstream intends. The model archive and
commit are pinned in Dockerfile; no runtime language downloads occur. These are
recognition models, not 162 separately tested languages.

## Narrow URL-controller repair

`patch-url-context.py` verifies exact v3.2.2 source and JAR hashes, recompiles only
`ConvertWebsiteToPDF`, and asserts that every other release JAR entry stays
byte-identical with unchanged ZIP storage mode. The compiler and Lombok processor
are checksum/digest pinned and excluded from the runtime image.

The repair removes a servlet-thread-only error-redirect builder that fails under
AutoJob's worker executor. It also replaces an unconditional private-address HEAD
preflight with explicit HTTP(S)-with-host validation and the existing configurable
SsrfProtectionService. Private networks are allowed and internal TLD restrictions
are cleared; initial loopback, link-local and cloud-metadata targets stay blocked. The original bounded GET, refusal to follow redirects,
file-reference detection and renderer protocol restrictions remain in place.
The resulting JAR hash and exact changed entry are embedded in
`/opt/stirling/url-context-patch.json`. Public/private success and invalid/file
input rejection are mandatory release checks.

## Source evidence

The pinned source paths establishing this boundary include
`app/common/.../EndpointConfiguration.java`, `app/core/.../UIDataController.java`,
`app/core/.../OCRController.java`, `app/core/.../PipelineController.java`,
`app/core/.../ConvertWebsiteToPDF.java`, `app/core/.../ConvertPdfToVideoController.java`,
`app/core/.../OfficeConversionService.java`, `app/core/.../TelegramPipelineBot.java`,
`frontend/src/editor/core/components/viewer/useViewerReadAloud.ts`, and the
`app/proprietary` implementations listed above. The official core build is the
license boundary; marketing plan descriptions are not used as a substitute for
package/source licenses.
