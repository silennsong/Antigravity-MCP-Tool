#!/usr/bin/env python3
"""Install this source/package into an isolated user venv and register Codex MCP."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path


AGY_INSTALLER_URL = "https://antigravity.google/cli/install.sh"


def run(command: list[str], **kwargs: object) -> None:
    completed = subprocess.run(command, check=False, **kwargs)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-agy", action="store_true")
    parser.add_argument("--auth", action="store_true")
    parser.add_argument("--configure-models", action="store_true")
    parser.add_argument("--init-project")
    parser.add_argument("--profile", choices=["read-only", "open"], default="read-only")
    parser.add_argument(
        "--force",
        action="store_true",
        help="append the marked Antigravity block when the target already has AGENTS.md",
    )
    parser.add_argument("--deep", action="store_true", help="run the real delegation probe")
    args = parser.parse_args()

    source = Path(__file__).resolve().parent
    venv = Path.home() / ".local/share/antigravity-delegate-mcp/venv"
    if not venv.exists():
        run([sys.executable, "-m", "venv", str(venv)])
    python = venv / "bin/python"
    command = venv / "bin/antigravity-delegate-mcp"
    run([str(python), "-m", "pip", "install", "--upgrade", str(source)])

    if args.install_agy and not (Path.home() / ".local/bin/agy").exists():
        with tempfile.TemporaryDirectory() as temporary:
            installer = Path(temporary) / "install.sh"
            urllib.request.urlretrieve(AGY_INSTALLER_URL, installer)
            run(["bash", str(installer)])

    run([str(command), "install", "--replace"])
    if args.auth:
        workspace = args.init_project or str(Path.cwd())
        run([str(command), "auth", "--workspace", workspace])
    if args.configure_models:
        run([str(command), "configure-models"])
    if args.init_project:
        init_command = [
            str(command),
            "init-project",
            "--workspace",
            args.init_project,
            "--profile",
            args.profile,
        ]
        if args.force:
            init_command.append("--force")
        run(init_command)
    doctor_command = [
        str(command),
        "doctor",
        "--workspace",
        args.init_project or str(Path.cwd()),
    ]
    if args.deep:
        doctor_command.append("--deep")
    run(doctor_command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
