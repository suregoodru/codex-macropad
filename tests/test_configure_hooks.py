import json
import tempfile
import unittest
from pathlib import Path

from scripts.configure_hooks import install_hooks, managed_command, uninstall_hooks

EXPECTED_EVENTS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "Stop",
    "SessionEnd",
)


class ConfigureHooksTests(unittest.TestCase):
    def test_install_preserves_existing_hooks_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "hooks.json"
            existing = {
                "description": "existing config",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "/existing/policy"}],
                        }
                    ]
                },
            }
            config_path.write_text(json.dumps(existing), encoding="utf-8")

            install_hooks(config_path, Path("/installed/codex_hook.py"))
            install_hooks(config_path, Path("/installed/codex_hook.py"))

            configured = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(configured["description"], "existing config")
            self.assertEqual(
                configured["hooks"]["PreToolUse"][0],
                existing["hooks"]["PreToolUse"][0],
            )
            self.assertEqual(len(configured["hooks"]["PreToolUse"]), 2)
            for event_name in EXPECTED_EVENTS:
                expected_count = 2 if event_name == "PreToolUse" else 1
                self.assertEqual(len(configured["hooks"][event_name]), expected_count)

    def test_install_uses_explicit_python_command_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "hooks.json"
            hook_path = Path("/installed/path with spaces/codex_hook.py")

            install_hooks(config_path, hook_path)

            configured = json.loads(config_path.read_text(encoding="utf-8"))
            expected_command = managed_command(hook_path)
            for event_name in EXPECTED_EVENTS:
                handler = configured["hooks"][event_name][-1]["hooks"][0]
                self.assertEqual(handler["command"], expected_command)
                expected_timeout = 3 if event_name == "SessionEnd" else 5
                self.assertEqual(handler["timeout"], expected_timeout)

    def test_uninstall_removes_only_managed_handler(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "hooks.json"
            hook_path = Path("/installed/codex_hook.py")
            command = managed_command(hook_path)
            existing = {"hooks": {}}
            for event_name in EXPECTED_EVENTS:
                existing["hooks"][event_name] = [
                    {"hooks": [{"type": "command", "command": command, "timeout": 5}]}
                ]
            existing["hooks"]["Stop"][0]["hooks"].append(
                {"type": "command", "command": "/keep/me"}
            )
            existing["hooks"]["PreToolUse"].insert(
                0,
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "/existing/policy"}],
                },
            )
            config_path.write_text(json.dumps(existing), encoding="utf-8")

            uninstall_hooks(config_path, hook_path)

            configured = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                configured["hooks"]["Stop"],
                [{"hooks": [{"type": "command", "command": "/keep/me"}]}],
            )
            self.assertEqual(
                configured["hooks"]["PreToolUse"],
                [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {"type": "command", "command": "/existing/policy"}
                        ],
                    }
                ],
            )
            for event_name in EXPECTED_EVENTS:
                if event_name not in ("PreToolUse", "Stop"):
                    self.assertNotIn(event_name, configured["hooks"])

    def test_uninstall_is_safe_when_config_does_not_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "hooks.json"

            uninstall_hooks(config_path, Path("/installed/codex_hook.py"))

            self.assertFalse(config_path.exists())


if __name__ == "__main__":
    unittest.main()
