from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from antigravity_mcp import adapter, server


class WorkspaceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()
        self.env = patch.dict(
            os.environ,
            {
                "ANTIGRAVITY_POLICY_SEARCH_ROOT": str(self.workspace),
                "ANTIGRAVITY_FLASH_MODEL": "Gemini Flash Test",
                "ANTIGRAVITY_PRO_MODEL": "Gemini Pro Test",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp_dir.cleanup()

    def write_policy(self, **overrides: object) -> Path:
        data: dict[str, object] = {
            "version": 1,
            "allowed_task_kinds": ["repository_exploration", "second_opinion"],
            "allowed_model_tiers": ["flash", "pro"],
            "allow_explicit_model": False,
            "allowed_modes": ["read_only"],
            "model_tier_task_kinds": {"pro": ["second_opinion"]},
            "min_task_chars": 40,
            "max_output_chars": 12_000,
            "max_timeout_seconds": 900,
            "forbidden_task_patterns": [
                {"pattern": "security|payment", "label": "sensitive work"}
            ],
            "worker_instructions": ["Do not modify files."],
            "required_output_sections": ["SUMMARY", "EVIDENCE"],
            "forward_env": [],
        }
        data.update(overrides)
        policy_path = self.workspace / server.POLICY_RELATIVE_PATH
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(json.dumps(data), encoding="utf-8")
        return policy_path

    def arguments(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "task": (
                "Inspect the repository entry points and return concise evidence with file "
                "paths and symbols."
            ),
            "task_kind": "repository_exploration",
            "workspace": str(self.workspace),
        }
        arguments.update(overrides)
        return arguments


class GlobalOpennessTests(WorkspaceTestCase):
    def test_no_project_policy_means_no_semantic_restrictions(self) -> None:
        request = server.validate_arguments(
            self.arguments(
                task="Review payment security and directly modify files.",
                task_kind="project_specific_custom_task",
                model_tier="custom-tier",
                model="Exact Custom Model",
                mode="workspace_write",
            )
        )
        self.assertFalse(request.policy.enabled)
        self.assertEqual(request.task_kind, "project_specific_custom_task")
        self.assertEqual(request.mode, "workspace_write")

    def test_tool_schema_has_no_global_task_or_model_enum(self) -> None:
        properties = server._tool_definition()["inputSchema"]["properties"]
        self.assertNotIn("enum", properties["task_kind"])
        self.assertNotIn("enum", properties["model_tier"])
        self.assertNotIn("enum", properties["mode"])


class ProjectPolicyTests(WorkspaceTestCase):
    def test_project_policy_is_loaded_from_parent(self) -> None:
        policy_path = self.write_policy()
        child = self.workspace / "src" / "feature"
        child.mkdir(parents=True)
        request = server.validate_arguments(self.arguments(workspace=str(child)))
        self.assertEqual(request.policy.path, policy_path)

    def test_project_rejects_disallowed_task_kind(self) -> None:
        self.write_policy()
        with self.assertRaisesRegex(server.RequestError, "rejects task_kind"):
            server.validate_arguments(self.arguments(task_kind="boilerplate_draft"))

    def test_project_restricts_pro_by_task_kind(self) -> None:
        self.write_policy()
        with self.assertRaisesRegex(server.RequestError, "does not allow pro"):
            server.validate_arguments(self.arguments(model_tier="pro"))
        request = server.validate_arguments(
            self.arguments(task_kind="second_opinion", model_tier="pro")
        )
        self.assertEqual(request.model_tier, "pro")

    def test_project_rejects_sensitive_and_write_tasks(self) -> None:
        self.write_policy()
        with self.assertRaisesRegex(server.RequestError, "sensitive work"):
            server.validate_arguments(
                self.arguments(task="Review the payment module security and give a concise report.")
            )
        with self.assertRaisesRegex(server.RequestError, "rejects mode"):
            server.validate_arguments(self.arguments(mode="workspace_write"))

    def test_project_can_ignore_explicitly_negated_forbidden_term(self) -> None:
        self.write_policy(
            forbidden_task_patterns=[
                {
                    "pattern": "delete|删除",
                    "label": "destructive work",
                    "ignore_negated": True,
                }
            ]
        )
        request = server.validate_arguments(
            self.arguments(
                task="Inspect the repository only. Do not delete files; return concise evidence."
            )
        )
        self.assertEqual(request.mode, "read_only")
        with self.assertRaisesRegex(server.RequestError, "destructive work"):
            server.validate_arguments(
                self.arguments(task="Delete generated files and return a concise report afterward.")
            )

    def test_project_rejects_explicit_model(self) -> None:
        self.write_policy()
        with self.assertRaisesRegex(server.RequestError, "explicit model"):
            server.validate_arguments(self.arguments(model="Any Model"))

    def test_project_prompt_contains_its_rules_and_output_contract(self) -> None:
        policy_path = self.write_policy()
        request = server.validate_arguments(self.arguments())
        prompt = server._worker_prompt(request)
        self.assertIn(str(policy_path), prompt)
        self.assertIn("Do not modify files.", prompt)
        self.assertIn("SUMMARY", prompt)
        self.assertIn("EVIDENCE", prompt)


class RunnerTests(WorkspaceTestCase):
    @patch("antigravity_mcp.server._agy_executable", return_value="/fake/agy")
    @patch("antigravity_mcp.server.subprocess.run")
    def test_runner_uses_argument_array_and_project_policy(
        self, run: Mock, _executable: Mock
    ) -> None:
        policy_path = self.write_policy()
        request = server.validate_arguments(self.arguments())
        run.return_value = subprocess.CompletedProcess([], 0, "SUMMARY\nDone", "")
        result = server.run_delegation(request)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["/fake/agy", "--model", "Gemini Flash Test"])
        self.assertEqual(result["structuredContent"]["project_policy"], str(policy_path))
        self.assertFalse(result["isError"])

    @patch("antigravity_mcp.server._agy_executable", return_value="/fake/agy")
    @patch("antigravity_mcp.server.subprocess.run")
    def test_project_controls_environment_forwarding(
        self, run: Mock, _executable: Mock
    ) -> None:
        self.write_policy(forward_env=["PROJECT_SAFE_VALUE"])
        with patch.dict(
            os.environ,
            {"PROJECT_SAFE_VALUE": "yes", "PROJECT_SECRET": "no"},
            clear=False,
        ):
            request = server.validate_arguments(self.arguments())
            run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
            server.run_delegation(request)
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["PROJECT_SAFE_VALUE"], "yes")
        self.assertNotIn("PROJECT_SECRET", child_env)

    @patch("antigravity_mcp.server._agy_executable", return_value="/fake/agy")
    @patch("antigravity_mcp.server.subprocess.run")
    def test_empty_success_output_is_reported_as_failure(
        self, run: Mock, _executable: Mock
    ) -> None:
        request = server.validate_arguments(self.arguments())
        run.return_value = subprocess.CompletedProcess(
            [], 0, "", 'read_file permission was auto-denied'
        )
        with self.assertRaises(server.OnboardingError) as raised:
            server.run_delegation(request)
        self.assertEqual(raised.exception.code, "workspace_read_permission_missing")
        self.assertTrue(raised.exception.actions)


class ProtocolTests(unittest.TestCase):
    def test_initialize_and_tools_list(self) -> None:
        stdin = io.StringIO(
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n'
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        )
        stdout = io.StringIO()
        self.assertEqual(server.serve(stdin, stdout), 0)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("general Antigravity CLI entrypoint", lines[0])
        self.assertIn(server.TOOL_NAME, lines[1])
        self.assertIn(server.READINESS_TOOL_NAME, lines[1])

    def test_onboarding_error_is_structured_for_agents(self) -> None:
        result = server._error_result(
            server.OnboardingError(
                "missing prerequisite",
                code="test_missing",
                actions=["run a command"],
            )
        )
        self.assertTrue(result["structuredContent"]["onboarding_required"])
        self.assertEqual(result["structuredContent"]["code"], "test_missing")
        self.assertTrue(result["structuredContent"]["no_changes_made"])


class ReadinessTests(WorkspaceTestCase):
    def test_runtime_command_targets_the_installed_plugin_archive(self) -> None:
        archive_module = "/portable/plugin/mcp/antigravity_mcp.pyz/antigravity_mcp/server.py"
        with patch.object(server, "__file__", archive_module):
            command = server._runtime_cli_command(
                "doctor", "--workspace", "/portable/project with spaces"
            )
        self.assertIn("/portable/plugin/mcp/antigravity_mcp.pyz", command)
        self.assertIn("'/portable/project with spaces'", command)
        self.assertNotIn("cd ", command)

    @patch("antigravity_mcp.server._agy_executable", return_value="/fake/agy")
    @patch(
        "antigravity_mcp.server.adapter.read_version",
        return_value=adapter.VersionCheck(raw="1.1.10", parsed=(1, 1, 10)),
    )
    @patch("antigravity_mcp.server.subprocess.run")
    def test_first_run_reports_actions_without_writing(
        self, run: Mock, _version: Mock, _executable: Mock
    ) -> None:
        settings = self.workspace / "global-settings.json"
        run.return_value = subprocess.CompletedProcess(
            [], 0, "Gemini Flash Test\nGemini Pro Test\n", ""
        )
        with patch.dict(
            os.environ,
            {"ANTIGRAVITY_CLI_SETTINGS": str(settings)},
            clear=False,
        ):
            result = server.check_readiness(
                {"workspace": str(self.workspace), "model_tier": "flash"}
            )
        structured = result["structuredContent"]
        self.assertTrue(structured["ready_to_delegate"])
        self.assertFalse(structured["safe_project_ready"])
        self.assertTrue(structured["no_changes_made"])
        self.assertFalse(settings.exists())
        commands = [action["command"] for action in structured["actions"]]
        self.assertTrue(any("init-project" in command for command in commands))
        self.assertTrue(any("permissions sync" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
