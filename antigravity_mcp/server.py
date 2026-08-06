#!/usr/bin/env python3
"""Policy-neutral STDIO MCP entrypoint for Antigravity CLI.

The global server owns transport and process execution. Optional, enforceable task
rules are loaded from <project>/.codex/antigravity-policy.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

try:
    from . import adapter, config, onboarding
except ImportError:  # Support the absolute server.py path used by existing MCP installs.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from antigravity_mcp import adapter, config, onboarding


SERVER_NAME = "antigravity-delegate"
SERVER_VERSION = "0.4.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
TOOL_NAME = "delegate_to_antigravity"
READINESS_TOOL_NAME = "check_antigravity_readiness"
ENABLED_TOOL_NAMES = (TOOL_NAME, READINESS_TOOL_NAME)
POLICY_RELATIVE_PATH = Path(".codex/antigravity-policy.json")

# These are transport/resource ceilings, not task-routing policy. Projects may set
# lower limits in their own policy file.
GLOBAL_MAX_TASK_CHARS = 200_000
GLOBAL_MAX_OUTPUT_CHARS = 1_000_000
GLOBAL_MAX_TIMEOUT_SECONDS = 86_400
DEFAULT_MAX_OUTPUT_CHARS = 12_000
DEFAULT_TIMEOUT_SECONDS = 600

SERVER_INSTRUCTIONS = (
    "This is a general Antigravity CLI entrypoint. Do not assume a global task policy. "
    "For a new or renamed workspace, call check_antigravity_readiness before delegation. "
    "That check is read-only and returns exact setup actions; never silently modify global "
    "permissions during a delegation request. Pass the current project as workspace and "
    "follow its applicable AGENTS.md. If "
    "<workspace>/.codex/antigravity-policy.json (or one in a parent project directory) "
    "exists, the server loads and enforces it before invoking Antigravity. Without a "
    "project policy, the server applies no semantic task restrictions."
)


class RequestError(ValueError):
    """Raised for invalid input, project-policy rejection, or worker failure."""


class OnboardingError(RequestError):
    """Raised when a missing prerequisite has a concrete user-controlled remedy."""

    def __init__(self, message: str, *, code: str, actions: list[str]) -> None:
        super().__init__(message)
        self.code = code
        self.actions = actions


@dataclass(frozen=True)
class ProjectPolicy:
    path: Path | None
    project_root: Path | None
    data: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return self.path is not None


@dataclass(frozen=True)
class DelegationRequest:
    task: str
    workspace: Path
    task_kind: str
    model_tier: str
    explicit_model: str | None
    mode: str
    max_output_chars: int
    timeout_seconds: int
    policy: ProjectPolicy


def _tool_definition() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "title": "Delegate a task to Antigravity",
        "description": (
            "General entrypoint to Antigravity CLI. The global tool does not hard-code which "
            "tasks, models, or modes are appropriate. Always pass the current project path as "
            "workspace and obey its AGENTS.md. If the project contains "
            ".codex/antigravity-policy.json, its allow-lists, model routing, limits, forbidden "
            "patterns, worker instructions, output contract, and environment forwarding rules "
            "are enforced before the CLI starts. Without that file, semantic routing remains "
            "open and is the caller's responsibility."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task", "task_kind", "workspace"],
            "properties": {
                "task": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": GLOBAL_MAX_TASK_CHARS,
                    "description": "Self-contained assignment for the Antigravity worker.",
                },
                "task_kind": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Caller-supplied routing label. Any value is globally accepted; a "
                        "project policy may allow-list labels."
                    ),
                },
                "workspace": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Current project or subdirectory. The server searches this path and "
                        "its parents for .codex/antigravity-policy.json."
                    ),
                },
                "model_tier": {
                    "type": "string",
                    "default": "flash",
                    "description": (
                        "Logical model tier resolved from ANTIGRAVITY_<TIER>_MODEL. Any tier "
                        "is globally accepted; a project policy may restrict it."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Optional exact model name, bypassing tier lookup. A project policy may "
                        "disallow explicit models."
                    ),
                },
                "mode": {
                    "type": "string",
                    "default": "read_only",
                    "description": (
                        "Requested worker mode. The global server does not interpret it; project "
                        "policy and Antigravity permissions define its effect."
                    ),
                },
                "max_output_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": GLOBAL_MAX_OUTPUT_CHARS,
                    "default": DEFAULT_MAX_OUTPUT_CHARS,
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": GLOBAL_MAX_TIMEOUT_SECONDS,
                    "default": DEFAULT_TIMEOUT_SECONDS,
                },
            },
        },
        "annotations": {
            # The tool may be read-only or write-capable depending on project policy/mode.
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    }


def _readiness_tool_definition() -> dict[str, Any]:
    return {
        "name": READINESS_TOOL_NAME,
        "title": "Check Antigravity readiness",
        "description": (
            "Read-only first-run and migration check for an Antigravity workspace. Use this "
            "before the first delegation in every new or renamed project. It reports CLI, "
            "authentication/model access, configured model tier, project policy, AGENTS routing, "
            "read-only file permissions, stale historical paths, and exact commands for anything "
            "missing. It never edits project or global configuration."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["workspace"],
            "properties": {
                "workspace": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Absolute path of the new or existing project.",
                },
                "model_tier": {
                    "type": "string",
                    "default": "flash",
                    "description": "Configured model tier whose availability should be checked.",
                },
            },
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    }


def _resolve_workspace(raw_workspace: Any) -> Path:
    if not isinstance(raw_workspace, str) or not raw_workspace.strip():
        raise RequestError("workspace is required for a global MCP call")
    try:
        workspace = Path(raw_workspace).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RequestError(f"workspace cannot be resolved: {exc}") from exc
    if not workspace.is_dir():
        raise RequestError(f"workspace is not a directory: {workspace}")
    return workspace


def _policy_search_ceiling() -> Path:
    configured = os.environ.get("ANTIGRAVITY_POLICY_SEARCH_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=True)
    return Path.home().resolve(strict=True)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def load_project_policy(workspace: Path) -> ProjectPolicy:
    """Find the closest project policy, stopping at the configured search ceiling."""
    ceiling = _policy_search_ceiling()
    candidates: list[Path] = []

    current = workspace
    while True:
        candidates.append(current)
        if current == ceiling or current.parent == current:
            break
        if _is_relative_to(current, ceiling):
            current = current.parent
        else:
            # External workspaces search only their own ancestor chain to filesystem root.
            current = current.parent

    for directory in candidates:
        policy_path = directory / POLICY_RELATIVE_PATH
        if not policy_path.is_file():
            continue
        try:
            parsed = json.loads(policy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RequestError(f"invalid project policy {policy_path}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise RequestError(f"project policy must be a JSON object: {policy_path}")
        if parsed.get("version") != 1:
            raise RequestError(f"unsupported project policy version in {policy_path}")
        return ProjectPolicy(path=policy_path, project_root=directory, data=parsed)

    return ProjectPolicy(path=None, project_root=None, data={})


def _string_argument(arguments: dict[str, Any], name: str, default: str | None = None) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str) or not value.strip():
        raise RequestError(f"{name} must be a non-empty string")
    return value.strip()


def _integer_argument(
    arguments: dict[str, Any], name: str, default: int, minimum: int, maximum: int
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise RequestError(f"{name} must be between {minimum} and {maximum}")
    return value


def _string_list(policy: ProjectPolicy, key: str) -> list[str] | None:
    value = policy.data.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RequestError(f"{key} must be a list of strings in {policy.path}")
    return value


def _policy_integer(policy: ProjectPolicy, key: str) -> int | None:
    value = policy.data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RequestError(f"{key} must be a non-negative integer in {policy.path}")
    return value


def _match_is_negated(task: str, match: re.Match[str]) -> bool:
    """Recognize explicit negation in the same short clause as a forbidden term."""
    prefix = task[max(0, match.start() - 120) : match.start()]
    clause = re.split(r"[.!?。！？\n;；]", prefix)[-1]
    return bool(
        re.search(
            r"(?:\bdo\s+not\b|\bdon't\b|\bnever\b|\bmust\s+not\b|"
            r"\bshall\s+not\b|\bavoid\b|不要|不得|禁止|切勿|不能)",
            clause,
            flags=re.IGNORECASE,
        )
    )


def _enforce_project_policy(
    *,
    policy: ProjectPolicy,
    task: str,
    task_kind: str,
    model_tier: str,
    explicit_model: str | None,
    mode: str,
    max_output_chars: int,
    timeout_seconds: int,
) -> None:
    if not policy.enabled:
        return

    allowed_task_kinds = _string_list(policy, "allowed_task_kinds")
    if allowed_task_kinds is not None and task_kind not in allowed_task_kinds:
        raise RequestError(f"project policy rejects task_kind: {task_kind}")

    allowed_model_tiers = _string_list(policy, "allowed_model_tiers")
    if allowed_model_tiers is not None and model_tier not in allowed_model_tiers:
        raise RequestError(f"project policy rejects model_tier: {model_tier}")

    allow_explicit_model = policy.data.get("allow_explicit_model", True)
    if not isinstance(allow_explicit_model, bool):
        raise RequestError(f"allow_explicit_model must be boolean in {policy.path}")
    if explicit_model is not None and not allow_explicit_model:
        raise RequestError("project policy rejects an explicit model name; use model_tier")

    allowed_modes = _string_list(policy, "allowed_modes")
    if allowed_modes is not None and mode not in allowed_modes:
        raise RequestError(f"project policy rejects mode: {mode}")

    tier_task_kinds = policy.data.get("model_tier_task_kinds")
    if tier_task_kinds is not None:
        if not isinstance(tier_task_kinds, dict):
            raise RequestError(f"model_tier_task_kinds must be an object in {policy.path}")
        restricted_kinds = tier_task_kinds.get(model_tier)
        if restricted_kinds is not None:
            if not isinstance(restricted_kinds, list) or any(
                not isinstance(item, str) for item in restricted_kinds
            ):
                raise RequestError(
                    f"model_tier_task_kinds.{model_tier} must be a list of strings"
                )
            if task_kind not in restricted_kinds:
                raise RequestError(
                    f"project policy does not allow {model_tier} for task_kind {task_kind}"
                )

    min_task_chars = _policy_integer(policy, "min_task_chars")
    max_task_chars = _policy_integer(policy, "max_task_chars")
    if min_task_chars is not None and len(task) < min_task_chars:
        raise RequestError(f"project policy requires at least {min_task_chars} task characters")
    if max_task_chars is not None and len(task) > max_task_chars:
        raise RequestError(f"project policy allows at most {max_task_chars} task characters")

    project_output_limit = _policy_integer(policy, "max_output_chars")
    if project_output_limit is not None and max_output_chars > project_output_limit:
        raise RequestError(
            f"project policy limits max_output_chars to {project_output_limit}"
        )
    project_timeout_limit = _policy_integer(policy, "max_timeout_seconds")
    if project_timeout_limit is not None and timeout_seconds > project_timeout_limit:
        raise RequestError(
            f"project policy limits timeout_seconds to {project_timeout_limit}"
        )

    forbidden_patterns = policy.data.get("forbidden_task_patterns", [])
    if not isinstance(forbidden_patterns, list):
        raise RequestError(f"forbidden_task_patterns must be a list in {policy.path}")
    for entry in forbidden_patterns:
        if not isinstance(entry, dict) or not isinstance(entry.get("pattern"), str):
            raise RequestError(f"each forbidden task pattern needs a string pattern in {policy.path}")
        label = entry.get("label", entry["pattern"])
        if not isinstance(label, str):
            raise RequestError(f"forbidden task pattern label must be a string in {policy.path}")
        ignore_negated = entry.get("ignore_negated", False)
        if not isinstance(ignore_negated, bool):
            raise RequestError(
                f"forbidden task pattern ignore_negated must be boolean in {policy.path}"
            )
        try:
            matched = re.search(entry["pattern"], task, flags=re.IGNORECASE)
        except re.error as exc:
            raise RequestError(f"invalid forbidden regex in {policy.path}: {exc}") from exc
        if matched and not (ignore_negated and _match_is_negated(task, matched)):
            raise RequestError(f"project policy rejects task: detected {label}")


def validate_project_policy_structure(policy: ProjectPolicy) -> None:
    """Validate every supported project-policy field without executing a task."""
    if not policy.enabled:
        raise RequestError("no project policy found")
    known = {
        "$schema",
        "version",
        "allowed_task_kinds",
        "allowed_model_tiers",
        "allow_explicit_model",
        "allowed_modes",
        "model_tier_task_kinds",
        "mode_cli_args",
        "min_task_chars",
        "max_task_chars",
        "max_output_chars",
        "max_timeout_seconds",
        "forbidden_task_patterns",
        "worker_instructions",
        "required_output_sections",
        "forward_env",
    }
    unknown = set(policy.data) - known
    if unknown:
        raise RequestError(
            f"unknown project policy field(s): {', '.join(sorted(unknown))} in {policy.path}"
        )
    for key in (
        "allowed_task_kinds",
        "allowed_model_tiers",
        "allowed_modes",
        "worker_instructions",
        "required_output_sections",
        "forward_env",
    ):
        _string_list(policy, key)
    for key in (
        "min_task_chars",
        "max_task_chars",
        "max_output_chars",
        "max_timeout_seconds",
    ):
        _policy_integer(policy, key)
    allow_explicit_model = policy.data.get("allow_explicit_model", True)
    if not isinstance(allow_explicit_model, bool):
        raise RequestError(f"allow_explicit_model must be boolean in {policy.path}")
    tier_task_kinds = policy.data.get("model_tier_task_kinds", {})
    if not isinstance(tier_task_kinds, dict):
        raise RequestError(f"model_tier_task_kinds must be an object in {policy.path}")
    for tier, task_kinds in tier_task_kinds.items():
        if not isinstance(tier, str) or not isinstance(task_kinds, list) or any(
            not isinstance(item, str) for item in task_kinds
        ):
            raise RequestError(f"invalid model_tier_task_kinds entry in {policy.path}")
    mode_cli_args = policy.data.get("mode_cli_args", {})
    if not isinstance(mode_cli_args, dict):
        raise RequestError(f"mode_cli_args must be an object in {policy.path}")
    for mode, cli_args in mode_cli_args.items():
        if not isinstance(mode, str) or not isinstance(cli_args, list) or any(
            not isinstance(item, str) for item in cli_args
        ):
            raise RequestError(f"invalid mode_cli_args entry in {policy.path}")
    forbidden_patterns = policy.data.get("forbidden_task_patterns", [])
    if not isinstance(forbidden_patterns, list):
        raise RequestError(f"forbidden_task_patterns must be a list in {policy.path}")
    for entry in forbidden_patterns:
        if not isinstance(entry, dict) or not isinstance(entry.get("pattern"), str):
            raise RequestError(f"invalid forbidden_task_patterns entry in {policy.path}")
        if not isinstance(entry.get("ignore_negated", False), bool):
            raise RequestError(
                f"forbidden task pattern ignore_negated must be boolean in {policy.path}"
            )
        try:
            re.compile(entry["pattern"], flags=re.IGNORECASE)
        except re.error as exc:
            raise RequestError(f"invalid forbidden regex in {policy.path}: {exc}") from exc


def validate_arguments(arguments: Any) -> DelegationRequest:
    if not isinstance(arguments, dict):
        raise RequestError("tool arguments must be an object")
    known = {
        "task",
        "workspace",
        "task_kind",
        "model_tier",
        "model",
        "mode",
        "max_output_chars",
        "timeout_seconds",
    }
    unknown = set(arguments) - known
    if unknown:
        raise RequestError(f"unknown argument(s): {', '.join(sorted(unknown))}")

    task = _string_argument(arguments, "task")
    if len(task) > GLOBAL_MAX_TASK_CHARS:
        raise RequestError(f"task exceeds transport limit of {GLOBAL_MAX_TASK_CHARS} characters")
    workspace = _resolve_workspace(arguments.get("workspace"))
    task_kind = _string_argument(arguments, "task_kind")
    model_tier = _string_argument(arguments, "model_tier", "flash")
    explicit_model_raw = arguments.get("model")
    explicit_model = (
        _string_argument(arguments, "model") if explicit_model_raw is not None else None
    )
    mode = _string_argument(arguments, "mode", "read_only")
    max_output_chars = _integer_argument(
        arguments,
        "max_output_chars",
        DEFAULT_MAX_OUTPUT_CHARS,
        1,
        GLOBAL_MAX_OUTPUT_CHARS,
    )
    timeout_seconds = _integer_argument(
        arguments,
        "timeout_seconds",
        DEFAULT_TIMEOUT_SECONDS,
        1,
        GLOBAL_MAX_TIMEOUT_SECONDS,
    )
    policy = load_project_policy(workspace)
    if policy.enabled:
        validate_project_policy_structure(policy)
    _enforce_project_policy(
        policy=policy,
        task=task,
        task_kind=task_kind,
        model_tier=model_tier,
        explicit_model=explicit_model,
        mode=mode,
        max_output_chars=max_output_chars,
        timeout_seconds=timeout_seconds,
    )
    return DelegationRequest(
        task=task,
        workspace=workspace,
        task_kind=task_kind,
        model_tier=model_tier,
        explicit_model=explicit_model,
        mode=mode,
        max_output_chars=max_output_chars,
        timeout_seconds=timeout_seconds,
        policy=policy,
    )


def _model_name(request: DelegationRequest) -> str:
    if request.explicit_model is not None:
        return request.explicit_model
    normalized_tier = re.sub(r"[^A-Za-z0-9]", "_", request.model_tier).upper()
    variable = f"ANTIGRAVITY_{normalized_tier}_MODEL"
    try:
        model = config.configured_model(request.model_tier)
    except config.ConfigError as exc:
        raise RequestError(str(exc)) from exc
    if not model:
        raise OnboardingError(
            f"{variable} is not configured for model tier `{request.model_tier}`.",
            code="model_mapping_missing",
            actions=[
                _runtime_cli_command("configure-models"),
                _runtime_cli_command(
                    "doctor", "--workspace", str(request.workspace)
                ),
            ],
        )
    return model


def _agy_executable() -> str:
    try:
        configured = config.configured_agy_command() or "agy"
    except config.ConfigError as exc:
        raise RequestError(str(exc)) from exc
    if not configured:
        raise RequestError("ANTIGRAVITY_CLI is empty")
    if os.sep in configured:
        try:
            path = Path(configured).expanduser().resolve(strict=True)
        except OSError as exc:
            raise RequestError(f"ANTIGRAVITY_CLI cannot be resolved: {exc}") from exc
        if not path.is_file() or not os.access(path, os.X_OK):
            raise RequestError(f"ANTIGRAVITY_CLI is not executable: {path}")
        return str(path)
    found = shutil.which(configured)
    if found is None:
        fallback = Path.home() / ".local/bin/agy"
        if fallback.is_file() and os.access(fallback, os.X_OK):
            found = str(fallback)
    if found is None:
        raise OnboardingError(
            f"Antigravity CLI `{configured}` was not found on PATH.",
            code="antigravity_cli_missing",
            actions=[
                _runtime_cli_command("install-agy"),
            ],
        )
    return found


def _runtime_cli_command(*arguments: str) -> str:
    """Return a copy-paste command that works from package or plugin installs."""
    runtime_file = Path(__file__).resolve()
    archive = next(
        (parent for parent in runtime_file.parents if parent.suffix == ".pyz"),
        None,
    )
    if archive is not None:
        return shlex.join([sys.executable, str(archive), *arguments])
    runtime_root = runtime_file.parent.parent
    command = shlex.join([sys.executable, "-m", "antigravity_mcp", *arguments])
    return "cd " + shlex.quote(str(runtime_root)) + " && " + command


def _readiness_action(command: str, reason: str) -> dict[str, str]:
    return {"command": command, "reason": reason}


def check_readiness(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise RequestError("tool arguments must be an object")
    unknown = set(arguments) - {"workspace", "model_tier"}
    if unknown:
        raise RequestError(f"unknown argument(s): {', '.join(sorted(unknown))}")
    workspace = _resolve_workspace(arguments.get("workspace"))
    model_tier = _string_argument(arguments, "model_tier", "flash")
    checks: list[dict[str, Any]] = []
    actions: list[dict[str, str]] = []

    try:
        executable = _agy_executable()
        version = adapter.read_version(executable)
        checks.append(
            {
                "name": "antigravity_cli",
                "status": "ok" if version.supported else "warning",
                "detail": f"{version.raw or 'unknown version'} at {executable}",
            }
        )
    except (RequestError, OSError, subprocess.SubprocessError) as exc:
        executable = None
        checks.append({"name": "antigravity_cli", "status": "missing", "detail": str(exc)})
        actions.append(
            _readiness_action(
                _runtime_cli_command("install-agy"),
                "Install the official Antigravity CLI before authentication or delegation.",
            )
        )

    available_models: list[str] = []
    if executable:
        try:
            listed = subprocess.run(
                [executable, "models"],
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            available_models = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
            if listed.returncode == 0 and available_models:
                checks.append(
                    {
                        "name": "authentication_and_model_access",
                        "status": "ok",
                        "detail": f"{len(available_models)} model(s) available",
                    }
                )
            else:
                detail = (listed.stderr or listed.stdout or "no model list returned").strip()
                checks.append(
                    {
                        "name": "authentication_and_model_access",
                        "status": "missing",
                        "detail": detail[-1000:],
                    }
                )
                actions.append(
                    _readiness_action(
                        _runtime_cli_command("auth", "--workspace", str(workspace)),
                        "Complete Google OAuth, terms, and workspace trust interactively.",
                    )
                )
        except (OSError, subprocess.SubprocessError) as exc:
            checks.append(
                {
                    "name": "authentication_and_model_access",
                    "status": "missing",
                    "detail": str(exc),
                }
            )
            actions.append(
                _readiness_action(
                    _runtime_cli_command("auth", "--workspace", str(workspace)),
                    "Complete interactive Antigravity onboarding.",
                )
            )

    try:
        configured = config.configured_model(model_tier)
    except config.ConfigError as exc:
        configured = None
        checks.append({"name": "model_mapping", "status": "missing", "detail": str(exc)})
    else:
        mapping_ready = bool(configured) and (not available_models or configured in available_models)
        checks.append(
            {
                "name": "model_mapping",
                "status": "ok" if mapping_ready else "missing",
                "detail": (
                    f"{model_tier} -> {configured}"
                    if configured
                    else f"no model configured for tier `{model_tier}`"
                ),
            }
        )
    if not configured or (available_models and configured not in available_models):
        actions.append(
            _readiness_action(
                _runtime_cli_command("configure-models"),
                f"Choose an exact available model for tier `{model_tier}`.",
            )
        )

    try:
        policy = load_project_policy(workspace)
        if policy.enabled:
            validate_project_policy_structure(policy)
            checks.append({"name": "project_policy", "status": "ok", "detail": str(policy.path)})
        else:
            checks.append(
                {
                    "name": "project_policy",
                    "status": "missing",
                    "detail": "no .codex/antigravity-policy.json found",
                }
            )
            actions.append(
                _readiness_action(
                    _runtime_cli_command(
                        "init-project",
                        "--workspace",
                        str(workspace),
                        "--profile",
                        "read-only",
                    ),
                    "Create project-local runtime policy and routing guidance. Review an existing AGENTS.md first; add --force only to append the marked block.",
                )
            )
    except (RequestError, OSError) as exc:
        policy = ProjectPolicy(path=None, project_root=None, data={})
        checks.append({"name": "project_policy", "status": "invalid", "detail": str(exc)})
        actions.append(
            _readiness_action(
                _runtime_cli_command("validate-policy", "--workspace", str(workspace)),
                "Repair the reported project policy error before delegation.",
            )
        )

    agents_path = workspace / "AGENTS.md"
    try:
        agents_text = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else ""
        routing_error = None
    except (OSError, UnicodeError) as exc:
        agents_text = ""
        routing_error = str(exc)
    routing_ready = "antigravity-delegate:start" in agents_text and routing_error is None
    checks.append(
        {
            "name": "agent_routing",
            "status": "ok" if routing_ready else ("invalid" if routing_error else "missing"),
            "detail": (
                f"marked Antigravity block in {agents_path}"
                if routing_ready
                else (
                    f"cannot read {agents_path}: {routing_error}"
                    if routing_error
                    else f"no marked Antigravity block in {agents_path}"
                )
            ),
        }
    )
    if not routing_ready and policy.enabled:
        actions.append(
            _readiness_action(
                _runtime_cli_command(
                    "init-project",
                    "--workspace",
                    str(workspace),
                    "--profile",
                    "read-only",
                    "--force",
                ),
                "Keep Codex routing guidance aligned with the enforceable project policy.",
            )
        )

    try:
        permission_audit = onboarding.audit_permissions(workspace)
        permission_ready = bool(permission_audit.get("read_allowed")) and bool(
            permission_audit.get("write_denied")
        )
        checks.append(
            {
                "name": "read_only_permissions",
                "status": "ok" if permission_ready else "missing",
                "detail": {
                    "settings_path": permission_audit["settings_path"],
                    "read_allowed": permission_audit.get("read_allowed"),
                    "write_denied": permission_audit.get("write_denied"),
                },
            }
        )
        if not permission_ready:
            actions.append(
                _readiness_action(
                    _runtime_cli_command(
                        "permissions", "sync", "--workspace", str(workspace)
                    ),
                    "Explicitly add the current workspace read/deny-write pair.",
                )
            )
        stale = list(permission_audit.get("stale_workspace_paths", []))
        checks.append(
            {
                "name": "stale_permission_paths",
                "status": "warning" if stale else "ok",
                "detail": stale,
            }
        )
        if stale:
            actions.append(
                _readiness_action(
                    _runtime_cli_command("permissions", "prune", "--stale"),
                    "Preview obsolete absolute-path rules; rerun with --yes only after review.",
                )
            )
    except (OSError, ValueError, onboarding.PermissionSettingsError) as exc:
        permission_ready = False
        checks.append(
            {"name": "read_only_permissions", "status": "invalid", "detail": str(exc)}
        )

    blocking_names = {
        check["name"]
        for check in checks
        if check["status"] in {"missing", "invalid"}
    }
    ready_to_delegate = not bool(
        blocking_names
        & {"antigravity_cli", "authentication_and_model_access", "model_mapping"}
    )
    safe_project_ready = ready_to_delegate and policy.enabled and routing_ready and permission_ready
    summary = (
        "Antigravity is fully ready for safe project delegation."
        if safe_project_ready
        else f"Antigravity onboarding is incomplete: {len(actions)} action(s) recommended."
    )
    return {
        "content": [{"type": "text", "text": summary + "\n\n" + "\n".join(
            f"[{check['status'].upper()}] {check['name']}: {check['detail']}" for check in checks
        )}],
        "structuredContent": {
            "workspace": str(workspace),
            "model_tier": model_tier,
            "ready_to_delegate": ready_to_delegate,
            "safe_project_ready": safe_project_ready,
            "checks": checks,
            "actions": actions,
            "no_changes_made": True,
        },
        "isError": False,
    }


def _policy_prompt(policy: ProjectPolicy) -> str:
    if not policy.enabled:
        return "No project Antigravity policy was found. Follow the caller's task and mode."

    instructions = _string_list(policy, "worker_instructions") or []
    required_sections = _string_list(policy, "required_output_sections") or []
    lines = [f"Enforced project policy: {policy.path}"]
    if instructions:
        lines.append("PROJECT EXECUTION RULES")
        lines.extend(f"- {instruction}" for instruction in instructions)
    if required_sections:
        lines.append("REQUIRED OUTPUT SECTIONS")
        lines.extend(required_sections)
    return "\n".join(lines)


def _worker_prompt(request: DelegationRequest) -> str:
    return f"""You are an Antigravity worker invoked by a primary Codex agent.

WORKSPACE
{request.workspace}

ROUTING METADATA
- task_kind: {request.task_kind}
- model_tier: {request.model_tier}
- requested_mode: {request.mode}
- output_limit: approximately {request.max_output_chars} characters

{_policy_prompt(request.policy)}

TASK
{request.task}
"""


def _child_environment(policy: ProjectPolicy) -> dict[str, str]:
    base_names = {
        "HOME",
        "PATH",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "TERM",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    forwarded = _string_list(policy, "forward_env") if policy.enabled else None
    allowed_names = base_names | set(forwarded or [])
    return {
        key: value
        for key, value in os.environ.items()
        if key in allowed_names or key.startswith("LC_")
    }


def _worker_failure(detail: str, workspace: Path) -> RequestError:
    lowered = detail.lower()
    if "read_file" in lowered and any(
        marker in lowered for marker in ("permission", "denied", "auto-denied", "not allowed")
    ):
        return OnboardingError(
            "Antigravity cannot read this workspace with the current headless permissions.",
            code="workspace_read_permission_missing",
            actions=[
                _runtime_cli_command(
                    "permissions", "audit", "--workspace", str(workspace)
                ),
                _runtime_cli_command(
                    "permissions", "sync", "--workspace", str(workspace)
                ),
                _runtime_cli_command(
                    "doctor", "--deep", "--workspace", str(workspace)
                ),
            ],
        )
    if any(marker in lowered for marker in ("oauth", "log in", "login required", "unauthenticated")):
        return OnboardingError(
            "Antigravity authentication is incomplete or expired.",
            code="authentication_required",
            actions=[
                _runtime_cli_command("auth", "--workspace", str(workspace)),
                _runtime_cli_command(
                    "doctor", "--deep", "--workspace", str(workspace)
                ),
            ],
        )
    return RequestError(detail)


def run_delegation(request: DelegationRequest) -> dict[str, Any]:
    model = _model_name(request)
    mode_cli_args: list[str] = []
    if request.policy.enabled:
        configured_args = request.policy.data.get("mode_cli_args", {}).get(request.mode, [])
        if not isinstance(configured_args, list) or any(
            not isinstance(item, str) for item in configured_args
        ):
            raise RequestError(f"invalid mode_cli_args for {request.mode}")
        mode_cli_args = configured_args
    command = adapter.build_print_command(
        executable=_agy_executable(),
        model=model,
        prompt=_worker_prompt(request),
        timeout_seconds=request.timeout_seconds,
        mode_args=mode_cli_args,
    )
    task_digest = hashlib.sha256(request.task.encode("utf-8")).hexdigest()[:12]
    try:
        completed = subprocess.run(
            command,
            cwd=request.workspace,
            env=_child_environment(request.policy),
            text=True,
            capture_output=True,
            timeout=request.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RequestError(
            f"Antigravity timed out after {request.timeout_seconds}s (task {task_digest})"
        ) from exc

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        detail = stderr[-2_000:] if stderr else "no stderr returned"
        raise _worker_failure(
            f"Antigravity exited with code {completed.returncode} "
            f"(task {task_digest}): {detail}",
            request.workspace,
        )
    if not stdout:
        detail = stderr[-2_000:] if stderr else "no stderr returned"
        raise _worker_failure(
            f"Antigravity exited successfully but produced no output "
            f"(task {task_digest}): {detail}",
            request.workspace,
        )

    truncated = len(stdout) > request.max_output_chars
    if truncated:
        stdout = stdout[: request.max_output_chars].rstrip() + "\n\n[output truncated by MCP server]"
    return {
        "content": [{"type": "text", "text": stdout}],
        "structuredContent": {
            "task_digest": task_digest,
            "task_kind": request.task_kind,
            "model_tier": request.model_tier,
            "model": model,
            "mode": request.mode,
            "workspace": str(request.workspace),
            "project_policy": str(request.policy.path) if request.policy.path else None,
            "truncated": truncated,
        },
        "isError": False,
    }


def _error_result(error: str | Exception) -> dict[str, Any]:
    message = str(error)
    structured: dict[str, Any] = {"rejected": True, "reason": message}
    if isinstance(error, OnboardingError):
        structured.update(
            {
                "onboarding_required": True,
                "code": error.code,
                "actions": error.actions,
                "no_changes_made": True,
            }
        )
    return {
        "content": [{"type": "text", "text": f"Delegation rejected: {message}"}],
        "structuredContent": structured,
        "isError": True,
    }


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": SERVER_INSTRUCTIONS,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": [_tool_definition(), _readiness_tool_definition()]},
        }
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict):
            result = _error_result("tools/call params must be an object")
        elif params.get("name") not in ENABLED_TOOL_NAMES:
            result = _error_result(f"unknown tool: {params.get('name')}")
        elif params.get("name") == READINESS_TOOL_NAME:
            try:
                result = check_readiness(params.get("arguments", {}))
            except (RequestError, OSError, ValueError) as exc:
                result = _error_result(exc)
        else:
            try:
                request = validate_arguments(params.get("arguments", {}))
                result = run_delegation(request)
            except (RequestError, OSError, ValueError) as exc:
                result = _error_result(exc)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    for raw_line in stdin:
        if not raw_line.strip():
            continue
        try:
            message = json.loads(raw_line)
            if not isinstance(message, dict):
                raise ValueError("message must be a JSON object")
            response = handle_request(message)
        except (json.JSONDecodeError, ValueError) as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
        except Exception as exc:
            print(f"Unexpected MCP server error: {exc}", file=sys.stderr, flush=True)
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": "Internal error"},
            }
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            stdout.flush()
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
