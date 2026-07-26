from __future__ import annotations

import ipaddress
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from pypdf import PdfWriter

from kindle_drop import (
    Dispatcher,
    Settings,
    allowed_host,
    create_backup,
    sha256_file,
    validate_pdf,
)
from tailnet_deny_ranges import TAILNET, complement


class FakeGmail:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_pdf(self, path: Path, name: str, message_id: str) -> str:
        self.sent.append((sha256_file(path), message_id))
        return f"gmail-{len(self.sent)}"

    def health(self) -> None:
        return

    def unread(self):
        return []


def make_pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)


class KindleDropTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        value = {
            "share_root": str(root / "share"),
            "state_root": str(root / "state"),
            "private_blob_path": str(root / "missing.dpapi"),
            "listen_ip": "127.0.0.1",
            "listen_port": 0,
            "automatic_limit_bytes": 4096,
            "web_limit_bytes": 8192,
            "stable_seconds": 0,
            "poll_seconds": 1,
            "gmail_poll_seconds": 120,
            "gmail_health_seconds": 300,
            "max_send_attempts": 3,
            "max_pages": 10,
            "return_download_limit_bytes": 8192,
        }
        config = root / "config.json"
        config.write_text(json.dumps(value), encoding="utf-8")
        self.settings = Settings.load(config)
        self.dispatcher = Dispatcher(self.settings)
        self.gmail = FakeGmail()
        self.dispatcher.gmail = self.gmail  # type: ignore[assignment]
        self.dispatcher.gmail_state = "ready"

    def tearDown(self) -> None:
        self.dispatcher.ledger.connection.close()
        self.temporary.cleanup()

    def settle_and_process(self) -> None:
        self.dispatcher.claim_ready()
        self.dispatcher.claim_ready()
        self.dispatcher.process_claimed()

    def test_valid_pdf_is_submitted_exactly_and_source_deleted(self) -> None:
        incoming = self.settings.inbox / "PLAN.PDF"
        make_pdf(incoming)
        expected = sha256_file(incoming)
        self.settle_and_process()
        self.assertFalse(incoming.exists())
        self.assertEqual(self.gmail.sent[0][0], expected)
        row = self.dispatcher.ledger.execute(
            "SELECT state, sha256 FROM submissions"
        ).fetchone()
        self.assertEqual(row["state"], "submitted")
        self.assertEqual(row["sha256"], expected)

    def test_duplicate_is_suppressed_but_resend_is_not(self) -> None:
        make_pdf(self.settings.inbox / "first.pdf")
        self.settle_and_process()
        make_pdf(self.settings.inbox / "duplicate.pdf")
        self.settle_and_process()
        make_pdf(self.settings.resend / "again.pdf")
        self.settle_and_process()
        states = [
            row["state"]
            for row in self.dispatcher.ledger.execute(
                "SELECT state FROM submissions ORDER BY created_at, rowid"
            ).fetchall()
        ]
        self.assertEqual(states, ["submitted", "duplicate-suppressed", "submitted"])
        self.assertEqual(len(self.gmail.sent), 2)

    def test_deceptive_extension_and_zero_byte_are_failed(self) -> None:
        (self.settings.inbox / "not-a-pdf.pdf").write_text("hello", encoding="utf-8")
        (self.settings.inbox / "empty.pdf").touch()
        self.settle_and_process()
        states = self.dispatcher.ledger.execute(
            "SELECT state, reason FROM submissions ORDER BY rowid"
        ).fetchall()
        self.assertEqual([row["state"] for row in states], ["failed", "failed"])
        self.assertEqual(len(list(self.settings.failed.iterdir())), 2)

    def test_automatic_boundary_and_web_routing(self) -> None:
        path = self.settings.inbox / "large.pdf"
        make_pdf(path)
        self.settings = Settings(
            **{
                **self.settings.__dict__,
                "automatic_limit_bytes": path.stat().st_size - 1,
                "web_limit_bytes": path.stat().st_size + 1,
            }
        )
        self.dispatcher.settings = self.settings
        self.settle_and_process()
        self.assertTrue((self.settings.oversize / "large.pdf").exists())
        row = self.dispatcher.ledger.execute(
            "SELECT state FROM submissions"
        ).fetchone()
        self.assertEqual(row["state"], "needs-web-upload")

    def test_pdf_validation_and_host_allowlist(self) -> None:
        path = self.settings.inbox / "two.pdf"
        make_pdf(path, pages=2)
        digest, size, pages = validate_pdf(path, 10)
        self.assertEqual(digest, sha256_file(path))
        self.assertGreater(size, 0)
        self.assertEqual(pages, 2)
        self.assertTrue(allowed_host("download.amazon.com", ["*.amazon.com"]))
        self.assertFalse(allowed_host("amazon.com.evil.invalid", ["*.amazon.com"]))
        self.assertFalse(allowed_host("amazon.com", ["*.amazon.com"]))

    def test_backup_contains_only_snapshot_receipts_and_returns(self) -> None:
        make_pdf(self.settings.returned / "annotated.pdf")
        (self.settings.receipts / "receipt.json").write_text(
            '{"state":"submitted"}', encoding="utf-8"
        )
        archive = create_backup(self.settings, self.settings.state_root / "backups")
        with zipfile.ZipFile(archive) as handle:
            names = set(handle.namelist())
        self.assertEqual(
            names,
            {
                "state/kindle-drop.sqlite3",
                "receipts/receipt.json",
                "returned-annotated/annotated.pdf",
            },
        )

    def test_tailnet_deny_complement_excludes_only_approved_hosts(self) -> None:
        approved = ["100.87.137.47", "100.108.67.36"]
        denied = complement(approved)
        for value in approved:
            self.assertFalse(any(ipaddress.ip_address(value) in network for network in denied))
        self.assertTrue(
            any(ipaddress.ip_address("100.109.193.95") in network for network in denied)
        )
        self.assertEqual(
            sum(network.num_addresses for network in denied),
            TAILNET.num_addresses - len(approved),
        )


if __name__ == "__main__":
    unittest.main()
