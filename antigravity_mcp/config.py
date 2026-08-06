"""Persistent user configuration for the Antigravity MCP wrapper."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CONFIG_VERSION = 1


class ConfigError(ValueError):
    pass


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "antigravity-delegate"


def config_path() -> Path:
    override = os.environ.get("ANTIGRAVITY_DELEGATE_CONFIG", "").strip()
    return Path(override).expanduser() if override else config_dir() / "config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or config_path()
    if not target.exists():
        return {"version": CONFIG_VERSION, "models": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid configuration {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"configuration must be a JSON object: {target}")
    if data.get("version") != CONFIG_VERSION:
        raise ConfigError(f"unsupported configuration version in {target}")
    models = data.get("models", {})
    if not isinstance(models, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in models.items()
    ):
        raise ConfigError(f"models must be a string map in {target}")
    agy_command = data.get("agy_command")
    if agy_command is not None and not isinstance(agy_command, str):
        raise ConfigError(f"agy_command must be a string in {target}")
    return data


def save_config(data: dict[str, Any], path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["version"] = CONFIG_VERSION
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    return target


def configured_model(tier: str) -> str | None:
    variable = "ANTIGRAVITY_" + "".join(
        character if character.isalnum() else "_" for character in tier
    ).upper() + "_MODEL"
    environment_value = os.environ.get(variable, "").strip()
    if environment_value:
        return environment_value
    value = load_config().get("models", {}).get(tier)
    return value.strip() if isinstance(value, str) and value.strip() else None


def configured_agy_command() -> str | None:
    environment_value = os.environ.get("ANTIGRAVITY_CLI", "").strip()
    if environment_value:
        return environment_value
    value = load_config().get("agy_command")
    return value.strip() if isinstance(value, str) and value.strip() else None
