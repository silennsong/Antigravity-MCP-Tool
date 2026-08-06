from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from antigravity_mcp import onboarding


class PermissionLifecycleTests(unittest.TestCase):
    def test_audit_sync_and_prune_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "current project"
            workspace.mkdir()
            stale = root / "renamed old project"
            settings = root / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "permissions": {
                            "allow": [f"read_file({stale})", "read_url(example.com)"],
                            "deny": [f"write_file({stale})"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"ANTIGRAVITY_CLI_SETTINGS": str(settings)},
                clear=False,
            ):
                before = settings.read_text(encoding="utf-8")
                audit = onboarding.audit_permissions(workspace)
                self.assertFalse(audit["read_allowed"])
                self.assertEqual(audit["stale_workspace_paths"], [str(stale)])
                self.assertEqual(settings.read_text(encoding="utf-8"), before)

                synced = onboarding.sync_read_only_permissions(workspace)
                self.assertTrue(synced["read_allowed"])
                self.assertTrue(synced["write_denied"])
                self.assertEqual(len(synced["added_rules"]), 2)

                preview = onboarding.prune_stale_permissions(apply=False)
                self.assertFalse(preview["applied"])
                self.assertIn(f"read_file({stale})", preview["stale_rules"])
                self.assertIn(f"read_file({stale})", settings.read_text(encoding="utf-8"))

                applied = onboarding.prune_stale_permissions(apply=True)
                self.assertTrue(applied["applied"])
                updated = settings.read_text(encoding="utf-8")
                self.assertNotIn(str(stale), updated)
                self.assertIn("read_url(example.com)", updated)
                self.assertIn(str(workspace.resolve()), updated)

    def test_missing_settings_audit_does_not_create_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "project"
            workspace.mkdir()
            settings = root / "missing" / "settings.json"
            with patch.dict(
                os.environ,
                {"ANTIGRAVITY_CLI_SETTINGS": str(settings)},
                clear=False,
            ):
                audit = onboarding.audit_permissions(workspace)
            self.assertFalse(audit["settings_exists"])
            self.assertFalse(settings.exists())


if __name__ == "__main__":
    unittest.main()
