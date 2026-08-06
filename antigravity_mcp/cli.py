"""Installer, diagnostics, authentication, configuration, and project bootstrap CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Sequence

from . import __version__
from . import adapter, config, onboarding, server


MCP_NAME = "antigravity_delegate"
AGY_INSTALLER_URL = "https://antigravity.google/cli/install.sh"
AGENTS_START = "<!-- antigravity-delegate:start -->"
AGENTS_END = "<!-- antigravity-delegate:end -->"


def _console_command() -> list[str] | None:
    invoked = Path(sys.argv[0]).expanduser().resolve()
    if invoked.is_file() and invoked.name == "antigravity-delegate-mcp":
        return [str(invoked), "server"]
    executable = shutil.which("antigravity-delegate-mcp")
    if executable:
        return [str(Path(executable).resolve()), "server"]
    return None


def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, check=False, **kwargs)


def _codex_config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    return (Path(codex_home).expanduser() if codex_home else Path.home() / ".codex") / "config.toml"


def _configure_codex_mcp_section(
    name: str, *, startup_timeout: int, tool_timeout: int
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("MCP name may contain only letters, numbers, underscore, and hyphen")
    path = _codex_config_path()
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header = f"[mcp_servers.{name}]"
    try:
        start = lines.index(header)
    except ValueError as exc:
        raise ValueError(f"Codex did not create {header} in {path}") from exc
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("["):
            end = index
            break
    managed_keys = {
        "startup_timeout_sec",
        "tool_timeout_sec",
        "enabled",
        "required",
        "enabled_tools",
        "default_tools_approval_mode",
    }
    body = [
        line
        for line in lines[start + 1 : end]
        if line.split("=", 1)[0].strip() not in managed_keys
    ]
    while body and body[-1] == "":
        body.pop()
    body.extend(
        [
            f"startup_timeout_sec = {startup_timeout}",
            f"tool_timeout_sec = {tool_timeout}",
            "enabled = true",
            "required = false",
            "enabled_tools = ["
            + ", ".join(f'"{name}"' for name in server.ENABLED_TOOL_NAMES)
            + "]",
            'default_tools_approval_mode = "auto"',
            "",
        ]
    )
    updated = lines[: start + 1] + body + lines[end:]
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    os.chmod(temporary, path.stat().st_mode & 0o777)
    temporary.replace(path)
    return path


def command_install(args: argparse.Namespace) -> int:
    codex = shutil.which("codex")
    if codex is None:
        print("ERROR: `codex` was not found on PATH.", file=sys.stderr)
        return 1
    server_command = _console_command()
    if server_command is None:
        print(
            "ERROR: install the package first so `antigravity-delegate-mcp` is on PATH.",
            file=sys.stderr,
        )
        return 1

    existing = _run([codex, "mcp", "get", args.name], capture_output=True)
    if existing.returncode == 0:
        if not args.replace:
            print(f"MCP `{args.name}` is already registered. Use --replace to update it.")
            return 0
        removed = _run([codex, "mcp", "remove", args.name], capture_output=True)
        if removed.returncode != 0:
            print(removed.stderr.strip(), file=sys.stderr)
            return removed.returncode

    added = _run([codex, "mcp", "add", args.name, "--", *server_command], capture_output=True)
    if added.returncode != 0:
        print((added.stderr or added.stdout).strip(), file=sys.stderr)
        return added.returncode
    try:
        config_path = _configure_codex_mcp_section(
            args.name,
            startup_timeout=args.startup_timeout,
            tool_timeout=args.tool_timeout,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: MCP registered but advanced configuration failed: {exc}", file=sys.stderr)
        return 1
    print(f"Registered global Codex MCP `{args.name}`: {' '.join(server_command)}")
    print(f"Updated timeouts and tool policy in {config_path}")
    print("Restart Codex, then use /mcp or run `codex mcp list`.")
    return 0


def command_install_agy(_args: argparse.Namespace) -> int:
    """Download and run the official Antigravity CLI installer."""
    existing = _agy_path()
    if existing:
        print(f"Antigravity CLI is already installed: {existing}")
        return 0
    try:
        with tempfile.TemporaryDirectory() as temporary:
            installer = Path(temporary) / "install.sh"
            urllib.request.urlretrieve(AGY_INSTALLER_URL, installer)
            completed = _run(["bash", str(installer)])
    except (OSError, urllib.error.URLError) as exc:
        print(f"ERROR: could not download Antigravity CLI installer: {exc}", file=sys.stderr)
        return 1
    if completed.returncode:
        print("ERROR: Antigravity CLI installer failed", file=sys.stderr)
        return completed.returncode
    print("Installed the official Antigravity CLI. Run `auth` next.")
    return 0


def _agy_path() -> str | None:
    configured = config.configured_agy_command()
    if configured:
        expanded = str(Path(configured).expanduser()) if os.sep in configured else configured
        return shutil.which(expanded) or (expanded if Path(expanded).is_file() else None)
    return shutil.which("agy") or (
        str(Path.home() / ".local/bin/agy")
        if (Path.home() / ".local/bin/agy").is_file()
        else None
    )


def command_auth(args: argparse.Namespace) -> int:
    executable = _agy_path()
    if executable is None:
        print(
            "ERROR: `agy` is not installed. Official installer: "
            "https://antigravity.google/cli/install.sh",
            file=sys.stderr,
        )
        return 1
    workspace = Path(args.workspace).expanduser().resolve()
    print("Launching Antigravity. Complete Google OAuth and workspace trust in the terminal.")
    print("Exit Antigravity after onboarding to continue setup.")
    return subprocess.call([executable], cwd=workspace)


def _parse_tier_assignments(values: Sequence[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected TIER=MODEL, got: {value}")
        tier, model = value.split("=", 1)
        if not tier.strip() or not model.strip():
            raise ValueError(f"expected non-empty TIER=MODEL, got: {value}")
        parsed[tier.strip()] = model.strip()
    return parsed


def command_configure_models(args: argparse.Namespace) -> int:
    executable = _agy_path()
    if executable is None:
        print("ERROR: `agy` is not installed.", file=sys.stderr)
        return 1
    listed = _run([executable, "models"], capture_output=True, timeout=60)
    if listed.returncode != 0:
        print((listed.stderr or listed.stdout).strip(), file=sys.stderr)
        print("Run `antigravity-delegate-mcp auth` first.", file=sys.stderr)
        return listed.returncode or 1
    available_text = listed.stdout.strip()
    print("Available Antigravity models:\n")
    print(available_text)

    try:
        models = _parse_tier_assignments(args.tier)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.flash:
        models["flash"] = args.flash
    if args.pro:
        models["pro"] = args.pro
    if not models and not args.non_interactive:
        flash = input("\nExact model for tier `flash` (blank to skip): ").strip()
        pro = input("Exact model for tier `pro` (blank to skip): ").strip()
        if flash:
            models["flash"] = flash
        if pro:
            models["pro"] = pro
    if not models:
        print("No model mappings supplied; configuration unchanged.")
        return 0

    available_models = {
        line.strip().lstrip("-* ").strip()
        for line in available_text.splitlines()
        if line.strip()
    }
    unavailable = sorted(set(models.values()) - available_models)
    if unavailable:
        print(
            "ERROR: model name(s) not returned by `agy models`: " + ", ".join(unavailable),
            file=sys.stderr,
        )
        return 2

    data = config.load_config()
    stored_models = dict(data.get("models", {}))
    stored_models.update(models)
    data["models"] = stored_models
    data["agy_command"] = str(Path(executable).resolve())
    target = config.save_config(data)
    print(f"Saved model mappings to {target}")
    for tier, model in sorted(models.items()):
        print(f"  {tier}: {model}")
    return 0


def _resource_text(relative: str) -> str:
    return resources.files("antigravity_mcp").joinpath(relative).read_text(encoding="utf-8")


def _write_or_replace_agents_block(path: Path, block: str, force: bool) -> None:
    marked = f"{AGENTS_START}\n{block.rstrip()}\n{AGENTS_END}"
    if not path.exists():
        path.write_text("# Agent guidance\n\n" + marked + "\n", encoding="utf-8")
        return
    existing = path.read_text(encoding="utf-8")
    if AGENTS_START in existing and AGENTS_END in existing:
        before, remainder = existing.split(AGENTS_START, 1)
        _, after = remainder.split(AGENTS_END, 1)
        path.write_text(before + marked + after, encoding="utf-8")
        return
    if not force:
        raise FileExistsError(
            f"{path} already exists without an Antigravity block; rerun with --force to append"
        )
    separator = "" if existing.endswith("\n\n") else "\n\n"
    path.write_text(existing + separator + marked + "\n", encoding="utf-8")


def _agy_cli_settings_path() -> Path:
    return onboarding.settings_path()


def _configure_read_only_agy_permissions(workspace: Path) -> Path:
    result = onboarding.sync_read_only_permissions(workspace)
    return Path(result["settings_path"])


def _print_permission_audit(audit: dict[str, object]) -> None:
    print(f"Antigravity permission settings: {audit['settings_path']}")
    if audit.get("workspace"):
        print(f"  workspace: {audit['workspace']}")
        print(f"  read allowed: {str(bool(audit.get('read_allowed'))).lower()}")
        print(f"  write denied: {str(bool(audit.get('write_denied'))).lower()}")
    stale = list(audit.get("stale_workspace_paths", []))
    print(f"  stale workspace paths: {len(stale)}")
    for path in stale:
        print(f"    - {path}")


def command_permissions(args: argparse.Namespace) -> int:
    try:
        if args.permissions_command == "audit":
            workspace = (
                Path(args.workspace).expanduser().resolve(strict=True)
                if args.workspace
                else None
            )
            audit = onboarding.audit_permissions(workspace)
            _print_permission_audit(audit)
            if audit.get("stale_workspace_paths"):
                print(
                    "Run `antigravity-delegate-mcp permissions prune --stale` to preview "
                    "removal; add `--yes` only after review."
                )
            return 0
        if args.permissions_command == "sync":
            workspace = Path(args.workspace).expanduser().resolve(strict=True)
            audit = onboarding.sync_read_only_permissions(workspace)
            added = list(audit.get("added_rules", []))
            print(f"Synchronized explicit read-only permissions for {workspace}")
            print(f"  added rules: {len(added)}")
            _print_permission_audit(audit)
            if audit.get("stale_workspace_paths"):
                print("WARNING: stale historical paths remain; audit and prune them explicitly.")
            return 0
        if args.permissions_command == "prune":
            result = onboarding.prune_stale_permissions(apply=args.yes)
            stale_rules = list(result["stale_rules"])
            if not stale_rules:
                print("No stale Antigravity file-permission rules found.")
                return 0
            print(("Removed" if args.yes else "Would remove") + f" {len(stale_rules)} stale rule(s):")
            for rule in stale_rules:
                print(f"  - {rule}")
            if not args.yes:
                print("Review the list, then rerun with `--yes` to apply.")
            return 0
    except (OSError, ValueError, onboarding.PermissionSettingsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("ERROR: unknown permissions command", file=sys.stderr)
    return 2


def command_init_project(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        print(f"ERROR: workspace is not a directory: {workspace}", file=sys.stderr)
        return 1
    policy_path = workspace / server.POLICY_RELATIVE_PATH
    if policy_path.exists() and not args.force:
        print(f"ERROR: policy already exists: {policy_path}; use --force", file=sys.stderr)
        return 1
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_text = _resource_text(f"templates/policy-{args.profile}.json")
    policy_path.write_text(policy_text.rstrip() + "\n", encoding="utf-8")
    schema_path = policy_path.parent / "antigravity-policy.schema.json"
    schema_path.write_text(
        _resource_text("schemas/antigravity-policy-v1.schema.json").rstrip() + "\n",
        encoding="utf-8",
    )
    try:
        _write_or_replace_agents_block(
            workspace / "AGENTS.md",
            _resource_text(f"templates/agents-{args.profile}.md"),
            args.force,
        )
    except FileExistsError as exc:
        policy_path.unlink(missing_ok=True)
        schema_path.unlink(missing_ok=True)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Initialized Antigravity profile `{args.profile}` in {workspace}")
    print(f"  policy: {policy_path}")
    print(f"  schema: {schema_path}")
    print(f"  routing: {workspace / 'AGENTS.md'}")
    if args.profile == "read-only" and args.configure_agy_permissions:
        try:
            settings_path = _configure_read_only_agy_permissions(workspace)
        except (OSError, ValueError) as exc:
            print(
                f"ERROR: project files created, but Antigravity permissions failed: {exc}",
                file=sys.stderr,
            )
            return 1
        print(f"  Antigravity permissions: {settings_path}")
    return 0


def _validate_policy_file(workspace: Path) -> tuple[bool, str]:
    try:
        policy = server.load_project_policy(workspace)
        if not policy.enabled:
            return False, "no project policy found"
        server.validate_project_policy_structure(policy)
    except (server.RequestError, OSError) as exc:
        return False, str(exc)
    return True, str(policy.path)


def command_validate_policy(args: argparse.Namespace) -> int:
    ok, message = _validate_policy_file(Path(args.workspace).expanduser().resolve())
    print(("OK: " if ok else "ERROR: ") + message)
    return 0 if ok else 1


def command_doctor(args: argparse.Namespace) -> int:
    failures = 0
    warnings = 0

    def report(level: str, message: str) -> None:
        nonlocal failures, warnings
        if level == "FAIL":
            failures += 1
        elif level == "WARN":
            warnings += 1
        print(f"[{level}] {message}")

    report("OK", f"Python {sys.version.split()[0]} (requires >=3.10)")
    codex = shutil.which("codex")
    if codex:
        registered = _run([codex, "mcp", "get", args.name], capture_output=True)
        report(
            "OK" if registered.returncode == 0 else "FAIL",
            f"Codex MCP `{args.name}` "
            + ("is registered" if registered.returncode == 0 else "is not registered"),
        )
    else:
        report("FAIL", "`codex` not found on PATH")

    executable = _agy_path()
    if executable:
        try:
            version = adapter.read_version(executable)
            report(
                "OK" if version.supported else "WARN",
                f"Antigravity CLI {version.raw or 'unknown version'} at {executable}",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            report("FAIL", f"cannot run Antigravity CLI: {exc}")
        if args.deep:
            try:
                generation = _run(
                    [
                        executable,
                        "--model",
                        config.configured_model("flash") or "gemini-3.6-flash-low",
                        "--print-timeout",
                        "60s",
                        "-p",
                        "Return exactly: AGY_DOCTOR_OK",
                    ],
                    capture_output=True,
                    timeout=75,
                )
                output = generation.stdout.strip()
                report(
                    "OK" if generation.returncode == 0 and output else "FAIL",
                    "Antigravity headless generation "
                    + ("works" if generation.returncode == 0 and output else "failed"),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                report("FAIL", f"Antigravity headless generation failed: {exc}")
    else:
        report("FAIL", "`agy` not found; install from https://antigravity.google/cli/install.sh")

    try:
        data = config.load_config()
        models = data.get("models", {})
        if models:
            report("OK", "configured model tiers: " + ", ".join(sorted(models)))
        else:
            report("WARN", "no model tiers configured; run `configure-models`")
    except config.ConfigError as exc:
        report("FAIL", str(exc))

    workspace = Path(args.workspace).expanduser().resolve()
    policy_ok, policy_message = _validate_policy_file(workspace)
    if policy_ok:
        report("OK", f"project policy valid: {policy_message}")
    else:
        report("WARN", policy_message)
        print(
            "  ACTION: antigravity-delegate-mcp init-project "
            f"--workspace {shlex.quote(str(workspace))} --profile read-only"
        )

    agents_path = workspace / "AGENTS.md"
    if agents_path.is_file():
        try:
            agents_text = agents_path.read_text(encoding="utf-8")
            report(
                "OK" if AGENTS_START in agents_text else "WARN",
                "project routing guidance "
                + ("contains the Antigravity block" if AGENTS_START in agents_text else "has no Antigravity block"),
            )
            if AGENTS_START not in agents_text:
                print("  ACTION: review AGENTS.md, then rerun init-project with --force to append")
        except OSError as exc:
            report("WARN", f"cannot inspect AGENTS.md: {exc}")
    else:
        report("WARN", "AGENTS.md is missing")

    try:
        permission_audit = onboarding.audit_permissions(workspace)
        read_ready = bool(permission_audit.get("read_allowed"))
        write_guard = bool(permission_audit.get("write_denied"))
        report(
            "OK" if read_ready and write_guard else "WARN",
            "Antigravity read-only permission pair "
            + ("is ready" if read_ready and write_guard else "is incomplete"),
        )
        if not read_ready or not write_guard:
            print(
                "  ACTION: antigravity-delegate-mcp permissions sync "
                f"--workspace {shlex.quote(str(workspace))}"
            )
        stale_paths = list(permission_audit.get("stale_workspace_paths", []))
        if stale_paths:
            report("WARN", f"{len(stale_paths)} stale historical permission path(s) found")
            for stale_path in stale_paths:
                print(f"    - {stale_path}")
            print("  ACTION: antigravity-delegate-mcp permissions prune --stale")
    except (OSError, ValueError, onboarding.PermissionSettingsError) as exc:
        report("FAIL", f"cannot inspect Antigravity permissions: {exc}")

    if args.deep and executable:
        try:
            policy = server.load_project_policy(workspace)
            allowed_kinds = (
                policy.data.get("allowed_task_kinds", []) if policy.enabled else []
            )
            task_kind = (
                "repository_exploration"
                if "repository_exploration" in allowed_kinds or not allowed_kinds
                else allowed_kinds[0]
            )
            allowed_tiers = (
                policy.data.get("allowed_model_tiers", []) if policy.enabled else []
            )
            model_tier = "flash" if "flash" in allowed_tiers or not allowed_tiers else allowed_tiers[0]
            allowed_modes = policy.data.get("allowed_modes", []) if policy.enabled else []
            mode = "read_only" if "read_only" in allowed_modes or not allowed_modes else allowed_modes[0]
            maximum_output = policy.data.get("max_output_chars", 2000) if policy.enabled else 2000
            maximum_timeout = policy.data.get("max_timeout_seconds", 120) if policy.enabled else 120
            request = server.validate_arguments(
                {
                    "task": (
                        "Inspect the workspace guidance and policy files. Return a concise "
                        "evidence-backed list of relevant file paths and no change proposal."
                    ),
                    "task_kind": task_kind,
                    "workspace": str(workspace),
                    "model_tier": model_tier,
                    "mode": mode,
                    "max_output_chars": min(2000, maximum_output),
                    "timeout_seconds": min(120, maximum_timeout),
                }
            )
            server.run_delegation(request)
            report("OK", "full MCP-to-Antigravity workspace probe")
        except (server.RequestError, OSError, subprocess.SubprocessError) as exc:
            report("FAIL", f"full MCP-to-Antigravity workspace probe failed: {exc}")

    initialize = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": server.DEFAULT_PROTOCOL_VERSION},
        }
    )
    report("OK" if initialize else "FAIL", "MCP initialize self-test")
    print(f"\nDoctor completed: {failures} failure(s), {warnings} warning(s).")
    return 1 if failures else 0


def command_setup(args: argparse.Namespace) -> int:
    install_args = argparse.Namespace(
        name=args.name,
        replace=args.replace,
        startup_timeout=args.startup_timeout,
        tool_timeout=args.tool_timeout,
    )
    result = command_install(install_args)
    if result:
        return result
    if args.auth:
        result = command_auth(argparse.Namespace(workspace=args.workspace))
        if result:
            return result
    if args.configure_models:
        result = command_configure_models(
            argparse.Namespace(flash=None, pro=None, tier=[], non_interactive=False)
        )
        if result:
            return result
    if args.init_project:
        result = command_init_project(
            argparse.Namespace(
                workspace=args.workspace,
                profile=args.profile,
                force=args.force,
                configure_agy_permissions=args.configure_agy_permissions,
            )
        )
        if result:
            return result
    return command_doctor(
        argparse.Namespace(name=args.name, workspace=args.workspace, deep=args.deep)
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="antigravity-delegate-mcp")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("server", help="Run the STDIO MCP server").set_defaults(
        handler=lambda _args: server.main()
    )
    subparsers.add_parser(
        "install-agy", help="Download and run the official Antigravity CLI installer"
    ).set_defaults(handler=command_install_agy)
    install = subparsers.add_parser("install", help="Register the global Codex MCP")
    install.add_argument("--name", default=MCP_NAME)
    install.add_argument("--replace", action="store_true")
    install.add_argument("--startup-timeout", type=int, default=10)
    install.add_argument("--tool-timeout", type=int, default=86400)
    install.set_defaults(handler=command_install)

    auth = subparsers.add_parser("auth", help="Launch agy for Google OAuth onboarding")
    auth.add_argument("--workspace", default=".")
    auth.set_defaults(handler=command_auth)

    models = subparsers.add_parser("configure-models", help="List and save model mappings")
    models.add_argument("--flash")
    models.add_argument("--pro")
    models.add_argument("--tier", action="append", default=[], metavar="TIER=MODEL")
    models.add_argument("--non-interactive", action="store_true")
    models.set_defaults(handler=command_configure_models)

    init = subparsers.add_parser("init-project", help="Create project routing and policy")
    init.add_argument("--workspace", default=".")
    init.add_argument("--profile", choices=["read-only", "open"], default="read-only")
    init.add_argument("--force", action="store_true")
    init.add_argument(
        "--agy-permissions",
        action=argparse.BooleanOptionalAction,
        default=True,
        dest="configure_agy_permissions",
        help="configure scoped read/deny-write Antigravity CLI grants for read-only profile",
    )
    init.set_defaults(handler=command_init_project)

    permissions = subparsers.add_parser(
        "permissions", help="Audit or explicitly update Antigravity file permissions"
    )
    permission_subparsers = permissions.add_subparsers(
        dest="permissions_command", required=True
    )
    permission_audit = permission_subparsers.add_parser(
        "audit", help="Read permission readiness and stale paths without changing settings"
    )
    permission_audit.add_argument("--workspace")
    permission_audit.set_defaults(handler=command_permissions)
    permission_sync = permission_subparsers.add_parser(
        "sync", help="Explicitly add the current workspace read/deny-write pair"
    )
    permission_sync.add_argument("--workspace", required=True)
    permission_sync.set_defaults(handler=command_permissions)
    permission_prune = permission_subparsers.add_parser(
        "prune", help="Preview or remove file rules whose absolute paths no longer exist"
    )
    permission_prune.add_argument("--stale", action="store_true", required=True)
    permission_prune.add_argument(
        "--yes", action="store_true", help="apply the reviewed removal; default is preview"
    )
    permission_prune.set_defaults(handler=command_permissions)

    validate = subparsers.add_parser("validate-policy", help="Validate the closest policy")
    validate.add_argument("--workspace", default=".")
    validate.set_defaults(handler=command_validate_policy)

    doctor = subparsers.add_parser("doctor", help="Check installation and project readiness")
    doctor.add_argument("--name", default=MCP_NAME)
    doctor.add_argument("--workspace", default=".")
    doctor.add_argument("--deep", action="store_true")
    doctor.set_defaults(handler=command_doctor)

    setup = subparsers.add_parser("setup", help="Register, optionally auth/configure, and diagnose")
    setup.add_argument("--name", default=MCP_NAME)
    setup.add_argument("--workspace", default=".")
    setup.add_argument("--replace", action="store_true")
    setup.add_argument("--startup-timeout", type=int, default=10)
    setup.add_argument("--tool-timeout", type=int, default=86400)
    setup.add_argument("--deep", action="store_true")
    setup.add_argument("--auth", action="store_true")
    setup.add_argument("--configure-models", action="store_true")
    setup.add_argument(
        "--init-project",
        action="store_true",
        help="create project-local policy and routing after global setup",
    )
    setup.add_argument("--profile", choices=["read-only", "open"], default="read-only")
    setup.add_argument(
        "--force",
        action="store_true",
        help="append the marked routing block when AGENTS.md already exists",
    )
    setup.add_argument(
        "--agy-permissions",
        action=argparse.BooleanOptionalAction,
        default=True,
        dest="configure_agy_permissions",
    )
    setup.set_defaults(handler=command_setup)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
