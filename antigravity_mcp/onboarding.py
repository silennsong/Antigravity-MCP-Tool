"""Read-only readiness checks and explicit Antigravity permission management."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


_FILE_RULE = re.compile(r"^(read_file|write_file)\((.*)\)$")


class PermissionSettingsError(ValueError):
    pass


def settings_path() -> Path:
    override = os.environ.get("ANTIGRAVITY_CLI_SETTINGS", "").strip()
    return (
        Path(override).expanduser()
        if override
        else Path.home() / ".gemini/antigravity-cli/settings.json"
    )


def load_settings(path: Path | None = None) -> tuple[Path, dict[str, Any]]:
    target = path or settings_path()
    if not target.exists():
        return target, {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PermissionSettingsError(f"invalid Antigravity CLI settings {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise PermissionSettingsError(f"Antigravity CLI settings must be an object: {target}")
    return target, data


def _permission_lists(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    permissions = data.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        raise PermissionSettingsError("permissions must be an object")
    allow = permissions.setdefault("allow", [])
    deny = permissions.setdefault("deny", [])
    if not isinstance(allow, list) or any(not isinstance(item, str) for item in allow):
        raise PermissionSettingsError("permissions.allow must be a string list")
    if not isinstance(deny, list) or any(not isinstance(item, str) for item in deny):
        raise PermissionSettingsError("permissions.deny must be a string list")
    return allow, deny


def _rule_paths(rules: list[str], operation: str) -> list[Path]:
    paths: list[Path] = []
    for rule in rules:
        match = _FILE_RULE.fullmatch(rule)
        if not match or match.group(1) != operation:
            continue
        candidate = Path(match.group(2)).expanduser()
        if candidate.is_absolute():
            paths.append(candidate)
    return paths


def _covers(root: Path, workspace: Path) -> bool:
    try:
        workspace.relative_to(root)
    except ValueError:
        return False
    return True


def audit_permissions(workspace: Path | None = None) -> dict[str, Any]:
    target, data = load_settings()
    allow, deny = _permission_lists(data)
    read_roots = _rule_paths(allow, "read_file")
    deny_write_roots = _rule_paths(deny, "write_file")
    all_roots = {str(path): path for path in [*read_roots, *deny_write_roots]}
    stale = sorted(path for path, parsed in all_roots.items() if not parsed.exists())
    result: dict[str, Any] = {
        "settings_path": str(target),
        "settings_exists": target.exists(),
        "stale_workspace_paths": stale,
        "file_permission_paths": sorted(all_roots),
    }
    if workspace is not None:
        resolved = workspace.expanduser().resolve(strict=True)
        result.update(
            {
                "workspace": str(resolved),
                "read_allowed": any(_covers(root, resolved) for root in read_roots),
                "write_denied": any(_covers(root, resolved) for root in deny_write_roots),
                "exact_read_rule": f"read_file({resolved})" in allow,
                "exact_write_deny_rule": f"write_file({resolved})" in deny,
            }
        )
    return result


def _save_settings(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def sync_read_only_permissions(workspace: Path) -> dict[str, Any]:
    resolved = workspace.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise PermissionSettingsError(f"workspace is not a directory: {resolved}")
    target, data = load_settings()
    allow, deny = _permission_lists(data)
    read_rule = f"read_file({resolved})"
    write_rule = f"write_file({resolved})"
    added: list[str] = []
    if read_rule not in allow:
        allow.append(read_rule)
        added.append(read_rule)
    if write_rule not in deny:
        deny.append(write_rule)
        added.append(write_rule)
    _save_settings(target, data)
    result = audit_permissions(resolved)
    result["added_rules"] = added
    return result


def prune_stale_permissions(*, apply: bool) -> dict[str, Any]:
    target, data = load_settings()
    allow, deny = _permission_lists(data)
    stale_rules: list[str] = []
    for rules in (allow, deny):
        for rule in rules:
            match = _FILE_RULE.fullmatch(rule)
            if not match:
                continue
            candidate = Path(match.group(2)).expanduser()
            if candidate.is_absolute() and not candidate.exists():
                stale_rules.append(rule)
    if apply and stale_rules:
        stale_set = set(stale_rules)
        allow[:] = [rule for rule in allow if rule not in stale_set]
        deny[:] = [rule for rule in deny if rule not in stale_set]
        _save_settings(target, data)
    return {
        "settings_path": str(target),
        "applied": apply,
        "stale_rules": sorted(set(stale_rules)),
    }
