#!/usr/bin/env python3
"""Install and remove the local Codex Macropad companion safely."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import tempfile
from pathlib import Path

try:
    from .configure_hooks import install_hooks, uninstall_hooks
except ImportError:
    from configure_hooks import install_hooks, uninstall_hooks

MANAGED_BUNDLE_ID = "ru.suregood.codex-macropad.spotify"
MANAGED_RUNTIME_FILES = (
    "MacropadDisplay",
    "codex_hook.py",
    "state.json",
    "state.lock",
    "controller.lock",
    "rgb-baseline.json",
    "codex-macropad.log",
)


def install_files(project_root: Path, user_home: Path) -> None:
    source_helper = project_root / "build" / "MacropadDisplay"
    source_hook = project_root / "codex_hook" / "main.py"
    destination_app, data_directory, destination_helper, destination_hook, hooks_path = _paths(
        user_home
    )

    if not source_helper.is_file():
        raise RuntimeError(f"Display helper is missing: {source_helper}")
    if not source_hook.is_file():
        raise RuntimeError(f"Hook source is missing: {source_hook}")

    if destination_app.exists() and _bundle_id(destination_app) == MANAGED_BUNDLE_ID:
        shutil.rmtree(destination_app)

    data_directory.mkdir(parents=True, exist_ok=True)
    _copy_executable_atomically(source_helper, destination_helper)
    _copy_executable_atomically(source_hook, destination_hook)
    install_hooks(hooks_path, destination_hook)


def uninstall_files(user_home: Path) -> None:
    destination_app, data_directory, destination_helper, destination_hook, hooks_path = _paths(
        user_home
    )
    uninstall_hooks(hooks_path, destination_hook)

    for file_name in MANAGED_RUNTIME_FILES:
        (data_directory / file_name).unlink(missing_ok=True)
    if data_directory.is_dir() and not any(data_directory.iterdir()):
        data_directory.rmdir()

    if destination_app.exists() and _bundle_id(destination_app) == MANAGED_BUNDLE_ID:
        shutil.rmtree(destination_app)


def _paths(user_home: Path) -> tuple[Path, Path, Path, Path, Path]:
    home = user_home.resolve()
    destination_app = home / "Applications" / "Spotify.app"
    data_directory = home / "Library" / "Application Support" / "CodexMacropad"
    destination_helper = data_directory / "MacropadDisplay"
    destination_hook = data_directory / "codex_hook.py"
    hooks_path = home / ".codex" / "hooks.json"
    return destination_app, data_directory, destination_helper, destination_hook, hooks_path


def _bundle_id(app_path: Path) -> str | None:
    plist_path = app_path / "Contents" / "Info.plist"
    try:
        with plist_path.open("rb") as plist_file:
            value = plistlib.load(plist_file).get("CFBundleIdentifier")
    except (FileNotFoundError, plistlib.InvalidFileException):
        return None
    return value if isinstance(value, str) else None


def _copy_executable_atomically(source: Path, destination: Path) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        shutil.copy2(source, temporary_path)
        temporary_path.chmod(0o755)
        with temporary_path.open("rb") as temporary_file:
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--project-root", type=Path)
    arguments = parser.parse_args()

    if arguments.action == "install":
        if arguments.project_root is None:
            parser.error("--project-root is required for install")
        install_files(arguments.project_root.resolve(), arguments.home.resolve())
    else:
        uninstall_files(arguments.home.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
