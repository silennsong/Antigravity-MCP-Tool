#!/usr/bin/env python3
"""Build the dependency-free Antigravity MCP runtime bundled by the Codex plugin."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPOSITORY_ROOT / "antigravity_mcp"
DEFAULT_OUTPUT = (
    REPOSITORY_ROOT
    / "plugins"
    / "antigravity-mcp"
    / "mcp"
    / "antigravity_mcp.pyz"
)
ARCHIVE_MAIN = "from antigravity_mcp.cli import main\n\nraise SystemExit(main())\n"
FIXED_TIMESTAMP = (2020, 1, 1, 0, 0, 0)


def _write_entry(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def build(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w") as archive:
        _write_entry(archive, "__main__.py", ARCHIVE_MAIN.encode("utf-8"))
        for source in sorted(PACKAGE_ROOT.rglob("*")):
            if not source.is_file() or source.suffix in {".pyc", ".pyo"}:
                continue
            relative = source.relative_to(REPOSITORY_ROOT).as_posix()
            _write_entry(archive, relative, source.read_bytes())
    temporary.replace(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(build(args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
