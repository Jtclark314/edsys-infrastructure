from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int
    elapsed_ms: int

    def json(self, default: Any = None) -> Any:
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError:
            return default


class CommandRunner:
    def __init__(self, timeout: int = 24):
        self.timeout = timeout

    def run(self, argv: list[str], timeout: int | None = None) -> CommandResult:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                text=True,
                capture_output=True,
                timeout=timeout or self.timeout,
                check=False,
            )
            return CommandResult(
                ok=proc.returncode == 0,
                stdout=proc.stdout.strip(),
                stderr=proc.stderr.strip(),
                returncode=proc.returncode,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                ok=False,
                stdout=(exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
                stderr="command_timeout",
                returncode=124,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )

    def ssh(self, alias: str, command: str, timeout: int | None = None) -> CommandResult:
        return self.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                alias,
                command,
            ],
            timeout=timeout,
        )
