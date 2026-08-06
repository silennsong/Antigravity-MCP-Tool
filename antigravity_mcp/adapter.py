"""Compatibility adapter for the Antigravity CLI command surface."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


MINIMUM_AUTOMATION_VERSION = (1, 1, 1)


@dataclass(frozen=True)
class VersionCheck:
    raw: str
    parsed: tuple[int, int, int] | None

    @property
    def supported(self) -> bool:
        return self.parsed is not None and self.parsed >= MINIMUM_AUTOMATION_VERSION


def read_version(executable: str, timeout: int = 10) -> VersionCheck:
    completed = subprocess.run(
        [executable, "--version"],
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    raw = (completed.stdout or completed.stderr).strip()
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", raw)
    parsed = tuple(int(part) for part in match.groups()) if match else None
    return VersionCheck(raw=raw, parsed=parsed)


def build_print_command(
    *,
    executable: str,
    model: str,
    prompt: str,
    timeout_seconds: int,
    mode_args: list[str] | None = None,
) -> list[str]:
    command = [
        executable,
        "--model",
        model,
        "--print-timeout",
        f"{timeout_seconds}s",
    ]
    command.extend(mode_args or [])
    command.extend(["-p", prompt])
    return command
