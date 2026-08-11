import json
import plistlib
import tempfile
import unittest
from pathlib import Path

from scripts.installation import install_files, uninstall_files


MANAGED_BUNDLE_ID = "ru.suregood.codex-macropad.spotify"
REAL_SPOTIFY_BUNDLE_ID = "com.spotify.client"


def make_project(root: Path) -> Path:
    helper = root / "build" / "MacropadDisplay"
    helper.parent.mkdir(parents=True)
    helper.write_text("helper-binary", encoding="utf-8")
    helper.chmod(0o755)
    hook = root / "codex_hook" / "main.py"
    hook.parent.mkdir(parents=True)
    hook.write_text("# hook\n", encoding="utf-8")
    return root


def make_spotify_bundle(path: Path, bundle_id: str) -> None:
    contents = path / "Contents"
    contents.mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as plist_file:
        plistlib.dump({"CFBundleIdentifier": bundle_id}, plist_file)


def bundle_id(path: Path) -> str:
    with (path / "Contents" / "Info.plist").open("rb") as plist_file:
        return plistlib.load(plist_file)["CFBundleIdentifier"]


def install_without_error(test_case: unittest.TestCase, project: Path, home: Path) -> None:
    try:
        install_files(project, home)
    except Exception as error:
        test_case.fail(f"helper installation raised {error!r}")


class InstallationTests(unittest.TestCase):
    def test_install_copies_helper_and_preserves_existing_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = make_project(root / "project")
            home = root / "home"
            hooks_path = home / ".codex" / "hooks.json"
            hooks_path.parent.mkdir(parents=True)
            hooks_path.write_text(
                json.dumps({"hooks": {"PreToolUse": [{"hooks": []}]}}),
                encoding="utf-8",
            )

            install_without_error(self, project, home)

            installed_helper = (
                home
                / "Library"
                / "Application Support"
                / "CodexMacropad"
                / "MacropadDisplay"
            )
            self.assertEqual(
                installed_helper.read_text(encoding="utf-8"),
                "helper-binary",
            )
            self.assertNotEqual(installed_helper.stat().st_mode & 0o111, 0)
            installed_hook = installed_helper.with_name("codex_hook.py")
            self.assertEqual(installed_hook.read_text(encoding="utf-8"), "# hook\n")
            configured = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(configured["hooks"]["PreToolUse"][0], {"hooks": []})
            self.assertEqual(len(configured["hooks"]["PreToolUse"]), 2)
            for event_name in (
                "UserPromptSubmit",
                "PreToolUse",
                "PermissionRequest",
                "PostToolUse",
                "Stop",
                "SessionEnd",
            ):
                self.assertIn(event_name, configured["hooks"])

    def test_reinstall_replaces_running_helper_inode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = make_project(root / "project")
            home = root / "home"
            install_without_error(self, project, home)
            installed_helper = (
                home
                / "Library"
                / "Application Support"
                / "CodexMacropad"
                / "MacropadDisplay"
            )
            original_inode = installed_helper.stat().st_ino
            (project / "build" / "MacropadDisplay").write_text(
                "updated-helper-binary",
                encoding="utf-8",
            )

            install_without_error(self, project, home)

            self.assertEqual(
                installed_helper.read_text(encoding="utf-8"),
                "updated-helper-binary",
            )
            self.assertNotEqual(installed_helper.stat().st_ino, original_inode)

    def test_install_removes_only_managed_legacy_spotify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = make_project(root / "project")
            home = root / "home"
            legacy_app = home / "Applications" / "Spotify.app"
            make_spotify_bundle(legacy_app, MANAGED_BUNDLE_ID)

            install_without_error(self, project, home)

            self.assertFalse(legacy_app.exists())

    def test_install_preserves_real_spotify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = make_project(root / "project")
            home = root / "home"
            real_app = home / "Applications" / "Spotify.app"
            make_spotify_bundle(real_app, REAL_SPOTIFY_BUNDLE_ID)

            install_without_error(self, project, home)

            self.assertEqual(bundle_id(real_app), REAL_SPOTIFY_BUNDLE_ID)

    def test_uninstall_removes_only_managed_runtime_files_hooks_and_legacy_app(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = make_project(root / "project")
            home = root / "home"
            install_without_error(self, project, home)
            data_directory = home / "Library" / "Application Support" / "CodexMacropad"
            for file_name in (
                "state.json",
                "state.lock",
                "controller.lock",
                "rgb-baseline.json",
                "codex-macropad.log",
            ):
                (data_directory / file_name).write_text("{}", encoding="utf-8")
            notes_path = data_directory / "notes.txt"
            notes_path.write_text("keep", encoding="utf-8")
            legacy_app = home / "Applications" / "Spotify.app"
            make_spotify_bundle(legacy_app, MANAGED_BUNDLE_ID)
            hooks_path = home / ".codex" / "hooks.json"
            configured = json.loads(hooks_path.read_text(encoding="utf-8"))
            user_pre_tool_group = {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "/existing/policy"}],
            }
            configured["hooks"]["PreToolUse"].insert(0, user_pre_tool_group)
            hooks_path.write_text(json.dumps(configured), encoding="utf-8")

            uninstall_files(home)

            self.assertTrue(data_directory.is_dir())
            self.assertEqual(notes_path.read_text(encoding="utf-8"), "keep")
            self.assertEqual(
                sorted(path.name for path in data_directory.iterdir()),
                ["notes.txt"],
            )
            self.assertFalse(legacy_app.exists())
            remaining = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(
                remaining["hooks"]["PreToolUse"],
                [user_pre_tool_group],
            )
            for event_name in (
                "UserPromptSubmit",
                "PermissionRequest",
                "PostToolUse",
                "Stop",
                "SessionEnd",
            ):
                self.assertNotIn(event_name, remaining["hooks"])

    def test_uninstall_preserves_real_spotify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = make_project(root / "project")
            home = root / "home"
            install_without_error(self, project, home)
            real_app = home / "Applications" / "Spotify.app"
            make_spotify_bundle(real_app, REAL_SPOTIFY_BUNDLE_ID)

            uninstall_files(home)

            self.assertEqual(bundle_id(real_app), REAL_SPOTIFY_BUNDLE_ID)


if __name__ == "__main__":
    unittest.main()
