"""Create a transactionally consistent copy of the SQLite command ledger."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    source_path = Path(args.source)
    destination_path = Path(args.destination)
    if not source_path.is_file():
        raise SystemExit("source ledger does not exist")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
        with sqlite3.connect(destination_path) as destination:
            source.backup(destination)
            result = destination.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise SystemExit("ledger backup integrity check failed")


if __name__ == "__main__":
    main()
