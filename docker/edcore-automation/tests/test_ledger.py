"""Threading and shutdown contracts for the durable command-ID ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
import unittest


STACK_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SOURCE = STACK_ROOT / "runtime" / "src"
sys.path.insert(0, str(RUNTIME_SOURCE))

from automation_runtime.ledger import DuplicateCommandError, Ledger  # noqa: E402


class LedgerThreadContractTestCase(unittest.TestCase):
    def test_main_thread_ledger_can_be_claimed_once_by_worker(self) -> None:
        now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / "seen.sqlite3")
            try:
                worker_errors: list[BaseException] = []

                def claim() -> None:
                    try:
                        ledger.claim(
                            "00000000-0000-4000-8000-000000000001",
                            now + timedelta(minutes=5),
                            now,
                        )
                    except BaseException as exc:  # preserve worker failure for the main assertion
                        worker_errors.append(exc)

                worker = threading.Thread(target=claim, name="ledger-test-worker")
                worker.start()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(worker_errors, [])

                with self.assertRaises(DuplicateCommandError):
                    ledger.claim(
                        "00000000-0000-4000-8000-000000000001",
                        now + timedelta(minutes=5),
                        now,
                    )
            finally:
                ledger.close()

    def test_source_joins_serial_workers_before_main_thread_close(self) -> None:
        ledger = (RUNTIME_SOURCE / "automation_runtime" / "ledger.py").read_text(encoding="utf-8")
        service = (RUNTIME_SOURCE / "automation_runtime" / "service.py").read_text(encoding="utf-8")
        self.assertIn("check_same_thread=False", ledger)
        self.assertNotIn("def release", ledger)
        self.assertLess(service.index("worker.join(timeout=30)"), service.index("self.ledger.close()"))
        self.assertLess(service.index("heartbeat.join(timeout=30)"), service.index("self.ledger.close()"))
        self.assertLess(service.index("worker.is_alive() or heartbeat.is_alive()"), service.index("self.ledger.close()"))


if __name__ == "__main__":
    unittest.main()
