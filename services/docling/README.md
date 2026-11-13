# Docling CPU document extraction

Commissioned on 2026-11-12 for on-demand, offline extraction of selected local
PDFs. **No web interface, listener, public route, timer, resident model or
automatic document ingestion.** The command is `edsys-docling` on 9950x.

Docling **2.150.0**, docling-slim **2.150.0**, CPU-only PyTorch **2.15.0+cpu**
and torchvision **0.29.2+cpu** are isolated from existing services. The full
Python dependency closure is version-pinned and distribution-hashed in
`requirements.lock`. Models are pinned to exact Hugging Face commits in
`model-pins.json`; only five Heron-layout/accurate-TableFormer files are cached.
The existing system Tesseract CLI supplies English OCR. GPU inference, VLMs,
external plugins, picture description and remote document services are disabled.

Sources: [Docling 2.150.0](https://github.com/docling-project/docling/releases/tag/v2.150.0),
[Docling documentation](https://docling-project.github.io/docling/),
[PyTorch](https://pypi.org/project/torch/2.15.0/),
[torchvision](https://pypi.org/project/torchvision/0.29.2/).

## Use

```sh
edsys-docling doctor
edsys-docling convert /absolute/path/selected.pdf \
  --output /home/jeremy/.local/share/edsys-docling/outputs/new-document
```

Output must be a new directory; its parent must already exist. `--ocr auto`
uses native text and OCR where needed, `--ocr force` OCRs full pages, and
`--ocr off` skips OCR. Defaults allow a 100 MiB PDF with at most 200 pages;
`--max-pages` can explicitly raise the page ceiling to 1,000. Exceeding the
ceiling fails rather than publishing a successful truncated extraction. Each
conversion has a 30-minute pipeline timeout and rejects incomplete results.

Exports are `document.json` (Docling structure and item geometry/provenance),
`document.md`, `pages/page-NNNN.md`, and `provenance.json` (source SHA-256,
absolute source URI, page links, item/page mapping, output hashes, model-manifest
hash, elapsed time and main-process peak RSS). Source content is read only and
hashed before/after conversion. The completion marker is `provenance.json` with
`status: complete`; verify it before adopting a derived output.

These are derived working files, not executed or authoritative source records.
Preserve originals and review extraction before relying on amounts, table cells,
scope or contractual language. Conversion quality on real project documents
has not been qualified by the generated-fixture acceptance below.

Every invocation runs as a transient user systemd job with a 4-core CPU quota,
6 GiB RAM ceiling, zero swap allowance, low CPU priority and a single-job lock.
The worker verifies the actual kernel cgroup limits before loading models.
CPU-only wheels and explicit CPU options prevent use of the shared GPU. Linux
socket-family restrictions prohibit IPv4/IPv6 even in child processes; offline
Hub/Transformers settings and a Python socket audit guard add separate checks.
Only an explicit `prepare-models` command enables model-download networking.
No API/account credentials are passed to conversion or model preparation.

Files and owned directories use 0600 and 0700. Keep real outputs outside Git,
general RAG and public/shared locations unless separately authorized.

## Install and update

Prerequisites: existing managed `uv`, system Python 3.12, user systemd/cgroup-v2,
system Tesseract with English data, and the mounted AI Store. Run as Jeremy:

```sh
python3 services/docling/install.py
edsys-docling prepare-models
```

The installer refuses unrelated launchers/unmarked runtimes, validates the
complete installed package map against the lock, and retains prior venvs and
source releases. Preparation fetches only commit-pinned public weights and
records content hashes; subsequent calls verify and reuse the cache.

Runtime layout:

- Launcher: `/home/jeremy/.local/bin/edsys-docling`.
- Owned toolkit: `/home/jeremy/.local/share/edsys-docling/` (versioned `venvs/`,
  source snapshots in `releases/`, `install.json`, `state/`, reconstructible
  package cache and private managed `outputs/`).
- Model cache: `/mnt/ai-store/models/docling/` (384,428,156 bytes of selected
  model artifacts, plus metadata; no source documents).
- No web interface or network port.

Updates require editing explicit pins, regenerating hashes with
`uv pip compile requirements.in --python /usr/bin/python3 --torch-backend cpu
--generate-hashes --output-file requirements.lock`, reinstalling and repeating
qualification. Model changes require a separate cache/reviewed migration; an
existing differing model manifest is refused. No automatic update or model pull
is installed. Roll back by restoring the previous private `install.json` and
matching launcher/source release; keep matching model weights and lock.

## Verification

```sh
python3 -m unittest discover -s services/docling -p 'test_*.py' -v
ruff check services/docling
```

`make_fixtures.py` uses system `python3` with ReportLab/Pillow to generate two
credential-free PDFs. The two-page text sample has distinct page markers and a
four-row, three-column table; the image-only sample has no native text. Convert
to new `text-table-output` and `scanned-output` directories, then run:

```sh
python3 services/docling/verify_outputs.py --fixtures /private/qualification
```

On commissioning, visual review of all three rendered source pages passed.
Offline text/table and forced scanned OCR conversions passed; all 12 table
cells, both page markers, OCR codes, source preservation, JSON/Markdown exports,
source/page provenance, file hashes and permissions were verified. A fresh
process using restored source and restored model weights also passed offline.
Network-family refusal and actual cgroup limits were checked directly. There is
no long-lived service to restart; repeated independent launches reload models
from the preserved cache and release their memory at exit.

Private receipts remain under
`/mnt/ai-store/maintenance/local-ai-upgrade-20261112/docling/`; runtime data and
logs are excluded from this repository.

## Backup and recovery

`/etc/systemd/system/edsys-backup.service.d/62-docling.conf` runs `backup.py`
before the existing encrypted backup. Its existing staging include covers
`/srv/edsys-backup/staging/docling/`. No new timer was created. Install the
drop-in from `edsys-backup-docling.conf` and run `systemctl daemon-reload` when
commissioning on a replacement host.

The stage contains exact model weights, model hashes/pins, installed-package
metadata, active source release/lock/launcher, and files inside managed
`outputs/`. It excludes reconstructible venvs/package caches and private inputs
or outputs stored elsewhere; those remain the owning source's backup duty.
Staging keeps the current and previous verified directory; encrypted Restic
retention remains governed by the existing backup system.

```sh
sudo python3 services/docling/backup.py
python3 services/docling/backup.py --verify /private/isolated-restore
```

Initial staging and an isolated file/hash restore passed. The restored source
and exact model cache completed a disposable offline conversion. This proves
local reconstruction/data integrity; a subsequent scheduled encrypted/offsite
backup and remote restore were not exercised during commissioning.

For recovery, restore to an isolated directory, verify `manifest.json`, rebuild
the environment using the source lock, then restore managed outputs and model
cache to owned private paths. Reconcile `install.json` paths with the newly
created environment before selecting it; verify a disposable offline conversion
before switching. Preserve the prior runtime/cache for rollback. To retire the
tool, remove only its verified launcher and backup drop-in after preserving
desired outputs; do not delete original source records or shared model stores.
