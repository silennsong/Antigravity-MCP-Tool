from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from antigravity_mcp import adapter, cli, config, server


class ConfigTests(unittest.TestCase):
    def test_model_configuration_round_trip_and_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "config.json"
            with patch.dict(
                os.environ,
                {"ANTIGRAVITY_DELEGATE_CONFIG": str(target)},
                clear=False,
            ):
                config.save_config({"models": {"flash": "Stored Flash"}})
                self.assertEqual(config.configured_model("flash"), "Stored Flash")
                with patch.dict(
                    os.environ,
                    {"ANTIGRAVITY_FLASH_MODEL": "Environment Flash"},
                    clear=False,
                ):
                    self.assertEqual(
                        config.configured_model("flash"), "Environment Flash"
                    )
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


class AdapterTests(unittest.TestCase):
    def test_print_command_uses_timeout_and_project_mode_arguments(self) -> None:
        command = adapter.build_print_command(
            executable="/agy",
            model="Gemini Flash",
            prompt="Inspect this repository",
            timeout_seconds=90,
            mode_args=["--mode", "plan", "--sandbox"],
        )
        self.assertEqual(
            command,
            [
                "/agy",
                "--model",
                "Gemini Flash",
                "--print-timeout",
                "90s",
                "--mode",
                "plan",
                "--sandbox",
                "-p",
                "Inspect this repository",
            ],
        )


class ProjectInitializationTests(unittest.TestCase):
    def test_init_project_creates_routing_policy_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            result = cli.command_init_project(
                argparse.Namespace(
                    workspace=str(workspace),
                    profile="read-only",
                    force=False,
                    configure_agy_permissions=False,
                )
            )
            self.assertEqual(result, 0)
            self.assertIn(
                cli.AGENTS_START,
                (workspace / "AGENTS.md").read_text(encoding="utf-8"),
            )
            policy_path = workspace / server.POLICY_RELATIVE_PATH
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            self.assertEqual(policy["mode_cli_args"]["read_only"][0:2], ["--mode", "plan"])
            self.assertTrue(
                (workspace / ".codex/antigravity-policy.schema.json").is_file()
            )
            loaded = server.load_project_policy(workspace)
            server.validate_project_policy_structure(loaded)

    def test_existing_agents_file_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / "AGENTS.md").write_text("Existing rules\n", encoding="utf-8")
            result = cli.command_init_project(
                argparse.Namespace(
                    workspace=str(workspace),
                    profile="open",
                    force=False,
                    configure_agy_permissions=False,
                )
            )
            self.assertEqual(result, 1)
            self.assertFalse((workspace / server.POLICY_RELATIVE_PATH).exists())

    def test_read_only_permissions_are_scoped_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "project"
            workspace.mkdir()
            settings = Path(temporary) / "settings.json"
            settings.write_text(
                json.dumps({"permissions": {"allow": ["read_url(google.com)"]}}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"ANTIGRAVITY_CLI_SETTINGS": str(settings)},
                clear=False,
            ):
                cli._configure_read_only_agy_permissions(workspace)
                cli._configure_read_only_agy_permissions(workspace)
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertIn("read_url(google.com)", data["permissions"]["allow"])
            resolved = workspace.resolve()
            self.assertEqual(
                data["permissions"]["allow"].count(f"read_file({resolved})"), 1
            )
            self.assertEqual(
                data["permissions"]["deny"], [f"write_file({resolved})"]
            )


class InstallTests(unittest.TestCase):
    def test_install_registers_stable_console_command(self) -> None:
        completed_missing = subprocess.CompletedProcess([], 1, "", "missing")
        completed_added = subprocess.CompletedProcess([], 0, "added", "")
        args = argparse.Namespace(
            name="antigravity_delegate",
            replace=False,
            startup_timeout=10,
            tool_timeout=86400,
        )
        with patch("antigravity_mcp.cli.shutil.which", return_value="/usr/bin/codex"), patch(
            "antigravity_mcp.cli._console_command",
            return_value=["/stable/bin/antigravity-delegate-mcp", "server"],
        ), patch(
            "antigravity_mcp.cli._run",
            side_effect=[completed_missing, completed_added],
        ) as run, patch(
            "antigravity_mcp.cli._configure_codex_mcp_section",
            return_value=Path("/tmp/config.toml"),
        ):
            self.assertEqual(cli.command_install(args), 0)
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/usr/bin/codex",
                "mcp",
                "add",
                "antigravity_delegate",
                "--",
                "/stable/bin/antigravity-delegate-mcp",
                "server",
            ],
        )

    def test_advanced_config_preserves_unrelated_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary)
            config_path = codex_home / "config.toml"
            config_path.write_text(
                'model = "test"\n\n[mcp_servers.antigravity_delegate]\n'
                'command = "/stable/server"\nargs = ["server"]\n\n[features]\nmemories = true\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}, clear=False):
                cli._configure_codex_mcp_section(
                    "antigravity_delegate", startup_timeout=10, tool_timeout=86400
                )
            updated = config_path.read_text(encoding="utf-8")
            self.assertIn('model = "test"', updated)
            self.assertIn("tool_timeout_sec = 86400", updated)
            self.assertIn('"check_antigravity_readiness"', updated)
            self.assertIn("[features]\nmemories = true", updated)

    def test_install_agy_downloads_official_installer(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        with patch("antigravity_mcp.cli._agy_path", return_value=None), patch(
            "antigravity_mcp.cli.urllib.request.urlretrieve"
        ) as download, patch(
            "antigravity_mcp.cli._run", return_value=completed
        ) as run:
            self.assertEqual(cli.command_install_agy(argparse.Namespace()), 0)
        self.assertEqual(download.call_args.args[0], cli.AGY_INSTALLER_URL)
        self.assertEqual(run.call_args.args[0][0], "bash")


if __name__ == "__main__":
    unittest.main()
