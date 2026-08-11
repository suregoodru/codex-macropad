#!/usr/bin/env python3
"""Idempotently add or remove Codex Macropad lifecycle hooks."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any, Dict

MANAGED_EVENTS = (
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "Stop",
    "SessionEnd",
)


def managed_command(hook_path: Path) -> str:
    return f"/usr/bin/python3 {shlex.quote(str(hook_path))}"


def install_hooks(config_path: Path, hook_path: Path) -> None:
    config = _load_config(config_path)
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks.json field 'hooks' must be an object")

    command = managed_command(hook_path)
    for event_name in MANAGED_EVENTS:
        groups = hooks.setdefault(event_name, [])
        if not isinstance(groups, list):
            raise ValueError(f"hooks.json event '{event_name}' must be an array")
        if not _contains_command(groups, command):
            groups.append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "timeout": 3 if event_name == "SessionEnd" else 5,
                        }
                    ]
                }
            )

    _write_config_atomically(config_path, config)


def uninstall_hooks(config_path: Path, hook_path: Path) -> None:
    if not config_path.exists():
        return

    config = _load_config(config_path)
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return

    command = managed_command(hook_path)
    for event_name in MANAGED_EVENTS:
        groups = hooks.get(event_name)
        if not isinstance(groups, list):
            continue

        retained_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                retained_groups.append(group)
                continue
            retained_handlers = [
                handler
                for handler in group["hooks"]
                if not isinstance(handler, dict) or handler.get("command") != command
            ]
            if retained_handlers:
                retained_group = dict(group)
                retained_group["hooks"] = retained_handlers
                retained_groups.append(retained_group)

        if retained_groups:
            hooks[event_name] = retained_groups
        else:
            hooks.pop(event_name, None)

    _write_config_atomically(config_path, config)


def _contains_command(groups: list[Any], command: str) -> bool:
    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        if any(
            isinstance(handler, dict) and handler.get("command") == command
            for handler in handlers
        ):
            return True
    return False


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("hooks.json root must be an object")
    return value


def _write_config_atomically(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    previous_mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            json.dump(value, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.chmod(temporary_name, previous_mode)
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--hook", type=Path, required=True)
    arguments = parser.parse_args()

    if arguments.action == "install":
        install_hooks(arguments.config, arguments.hook)
    else:
        uninstall_hooks(arguments.config, arguments.hook)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
