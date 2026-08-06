from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugins" / "antigravity-mcp"
SKILL_ROOT = PLUGIN_ROOT / "skills" / "gemini-delegation-router"
BUNDLE = PLUGIN_ROOT / "mcp" / "antigravity_mcp.pyz"


class PluginPackageTests(unittest.TestCase):
    def test_marketplace_points_to_portable_plugin(self) -> None:
        marketplace = json.loads(
            (REPOSITORY_ROOT / ".agents/plugins/marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marketplace["name"], "antigravity-tools")
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "antigravity-mcp")
        self.assertEqual(entry["source"]["path"], "./plugins/antigravity-mcp")

    def test_manifest_mcp_and_skill_are_connected(self) -> None:
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        mcp = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        openai_yaml = (SKILL_ROOT / "agents/openai.yaml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(manifest["name"], PLUGIN_ROOT.name)
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        server = mcp["mcpServers"]["antigravity_delegate"]
        self.assertEqual(server["cwd"], ".")
        self.assertEqual(server["args"][-1], "server")
        self.assertNotIn("/Users/", json.dumps(mcp))
        self.assertIn("过程中可以调用 Gemini MCP", skill)
        self.assertIn("allow_implicit_invocation: true", openai_yaml)
        self.assertIn('value: "antigravity_delegate"', openai_yaml)

    def test_bundled_mcp_starts_outside_the_source_checkout(self) -> None:
        request = (
            '{"jsonrpc":"2.0","id":1,"method":"initialize",'
            '"params":{"protocolVersion":"2025-06-18"}}\n'
            '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
        )
        with tempfile.TemporaryDirectory() as temporary:
            copied_bundle = Path(temporary) / "portable-antigravity.pyz"
            copied_bundle.write_bytes(BUNDLE.read_bytes())
            completed = subprocess.run(
                [sys.executable, str(copied_bundle), "server"],
                input=request,
                text=True,
                capture_output=True,
                cwd=temporary,
                timeout=10,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("check_antigravity_readiness", completed.stdout)
        self.assertIn("delegate_to_antigravity", completed.stdout)

    def test_committed_bundle_matches_current_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rebuilt = Path(temporary) / "rebuilt.pyz"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPOSITORY_ROOT / "scripts/build_plugin_bundle.py"),
                    "--output",
                    str(rebuilt),
                ],
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(BUNDLE.read_bytes(), rebuilt.read_bytes())


if __name__ == "__main__":
    unittest.main()
