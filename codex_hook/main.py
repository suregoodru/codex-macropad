#!/usr/bin/env python3
"""Translate Codex lifecycle events into shared macropad status state."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Optional

STATE_VERSION = 3
RECENT_PHASE_TTL_SECONDS = 1.5
DONE_TTL_SECONDS = 12.0
ACTIVE_TTL_SECONDS = 6 * 60 * 60.0
WORKSPACE_LIMIT_BYTES = 20
MANAGED_EVENTS = {
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "Stop",
    "SessionEnd",
}
DEFAULT_STATE_PATH = (
    Path.home() / "Library" / "Application Support" / "CodexMacropad" / "state.json"
)
DEFAULT_HELPER_PATH = DEFAULT_STATE_PATH.parent / "MacropadDisplay"
ACTIVITY_PHASES = frozenset(
    {"analyze", "research", "review", "edit", "verify", "run"}
)
TOOL_PHASES = ACTIVITY_PHASES - {"analyze"}
AUTO_REVIEWERS = frozenset({"auto_review", "guardian_subagent"})
USER_INPUT_TOOLS = frozenset({"request_user_input"})
TRANSCRIPT_TERMINAL_EVENTS = frozenset({"task_complete", "turn_aborted"})

VERIFY_COMMAND = re.compile(
    r"(?:\bpytest\b|\bpython3?\s+-m\s+unittest\b|\bswift\s+test\b|"
    r"\bnpm\s+(?:test|run\s+(?:test|lint|build))\b|\bnpx\s+tsc\b|"
    r"\bcargo\s+test\b|\bgo\s+test\b|\bmvn\w*\s+test\b|"
    r"\bgradle\w*\s+test\b|\bgit\s+diff\s+--check\b)",
    re.IGNORECASE,
)
REVIEW_COMMAND = re.compile(r"\b(?:gh\s+pr|glab\s+mr)\b", re.IGNORECASE)
RESEARCH_COMMAND = re.compile(
    r"(?:^|[;&|]\s*)(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*"
    r"(?:rg|grep|find|fd|ls|cat|head|tail|jq|sed\s+-n)\b|"
    r"\bgit\s+(?:status|diff|log|show)\b",
    re.IGNORECASE,
)
RESEARCH_ACTIONS = (
    "read",
    "search",
    "find",
    "list",
    "get",
    "fetch",
    "query",
    "open",
)


class StateVersionError(ValueError):
    """Raised when a state file belongs to an unsupported schema version."""


def classify_tool(tool_name: Any, tool_input: Any) -> str:
    """Classify a tool call without retaining or semantically analyzing its input."""
    name = tool_name if isinstance(tool_name, str) else ""
    lowered = name.casefold()
    values = tool_input if isinstance(tool_input, Mapping) else {}
    command_value = values.get("command")
    command = command_value if isinstance(command_value, str) else ""

    if lowered == "bash":
        if VERIFY_COMMAND.search(command):
            return "verify"
        if REVIEW_COMMAND.search(command):
            return "review"
        if RESEARCH_COMMAND.search(command):
            return "research"
        return "run"
    if lowered == "apply_patch":
        return "edit"
    if any(token in lowered for token in ("merge_request", "pull_request")):
        return "review"
    action = lowered.rsplit("__", 1)[-1]
    if lowered in {"web__run", "view_image"} or action.startswith(RESEARCH_ACTIONS):
        return "research"
    return "run"


def empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "sessions": {}}


def normalize_workspace(cwd: str) -> str:
    """Return a private, display-safe basename of at most 20 UTF-8 bytes."""
    sanitized = "".join(
        character
        for character in cwd
        if not unicodedata.category(character).startswith("C")
    )
    try:
        workspace = Path(sanitized).resolve(strict=False).name
    except (OSError, RuntimeError, ValueError):
        workspace = ""
    workspace = workspace or "workspace"

    result = ""
    for character in workspace:
        candidate = result + character
        if len(candidate.encode("utf-8")) > WORKSPACE_LIMIT_BYTES:
            break
        result = candidate
    return result or "workspace"


def is_internal_memories_cwd(value: Any) -> bool:
    """Return whether cwd belongs to Codex's internal memory workspace."""
    if not isinstance(value, str) or not value:
        return False
    try:
        cwd = Path(value).expanduser()
        if not cwd.is_absolute():
            return False
        resolved_cwd = cwd.resolve(strict=False)
        memories_root = (Path.home() / ".codex" / "memories").resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved_cwd == memories_root or memories_root in resolved_cwd.parents


def effective_approvals_reviewer(payload: Mapping[str, Any]) -> Optional[str]:
    """Read the effective reviewer for this turn without retaining transcript data."""
    transcript_path = payload.get("transcript_path")
    turn_id = payload.get("turn_id")
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    if not isinstance(turn_id, str) or not turn_id:
        return None

    reviewer: Optional[str] = None
    try:
        with Path(transcript_path).open("r", encoding="utf-8") as transcript:
            for line in transcript:
                if '"turn_context"' not in line or turn_id not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                context = record.get("payload") if isinstance(record, dict) else None
                if (
                    not isinstance(record, dict)
                    or record.get("type") != "turn_context"
                    or not isinstance(context, dict)
                    or context.get("turn_id") != turn_id
                ):
                    continue
                value = context.get("approvals_reviewer")
                reviewer = value if isinstance(value, str) else None
    except (OSError, UnicodeError):
        return None
    return reviewer


def transcript_terminal_event(line: str, turn_id: str) -> Optional[str]:
    """Return the supported terminal event for one exact transcript turn."""
    if turn_id not in line:
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    event = record.get("payload") if isinstance(record, dict) else None
    event_type = event.get("type") if isinstance(event, dict) else None
    if (
        not isinstance(record, dict)
        or record.get("type") != "event_msg"
        or not isinstance(event, dict)
        or event.get("turn_id") != turn_id
        or not isinstance(event_type, str)
        or event_type not in TRANSCRIPT_TERMINAL_EVENTS
    ):
        return None
    return event_type


def _new_session(payload: Mapping[str, Any], now: float) -> dict[str, Any]:
    turn_id = payload.get("turn_id")
    return {
        "workspace": normalize_workspace(str(payload.get("cwd") or "")),
        "status": "working",
        "turn_id": turn_id if isinstance(turn_id, str) and turn_id else None,
        "turn_started_at": now,
        "updated_at": now,
        "expires_at": now + ACTIVE_TTL_SECONDS,
        "active_tools": {},
        "recent_phase": None,
        "recent_phase_until": None,
    }


def _touch_session(
    session: dict[str, Any],
    payload: Mapping[str, Any],
    now: float,
    ttl: float,
) -> None:
    session["workspace"] = normalize_workspace(str(payload.get("cwd") or ""))
    turn_id = payload.get("turn_id")
    if session["turn_id"] is None and isinstance(turn_id, str) and turn_id:
        session["turn_id"] = turn_id
    session["updated_at"] = now
    session["expires_at"] = now + ttl


def apply_event(
    state: dict[str, Any],
    payload: Mapping[str, Any],
    now: float,
    *,
    approvals_reviewer: Optional[str] = None,
) -> tuple[dict[str, Any], bool]:
    """Apply one supported hook event to an already validated state value."""
    sessions: dict[str, dict[str, Any]] = state["sessions"]
    expired = [
        session_id
        for session_id, session in sessions.items()
        if session["expires_at"] <= now
    ]
    for session_id in expired:
        del sessions[session_id]
    changed = bool(expired)

    event_name = payload.get("hook_event_name")
    session_id = payload.get("session_id")
    if (
        event_name not in MANAGED_EVENTS
        or not isinstance(session_id, str)
        or not session_id
    ):
        return state, changed

    if is_internal_memories_cwd(payload.get("cwd")):
        removed = sessions.pop(session_id, None) is not None
        return state, changed or removed

    if event_name == "SessionEnd":
        removed = sessions.pop(session_id, None) is not None
        return state, changed or removed

    if event_name == "UserPromptSubmit":
        sessions[session_id] = _new_session(payload, now)
        return state, True

    session = sessions.get(session_id)
    if session is None:
        if event_name == "PermissionRequest":
            session = _new_session(payload, now)
            sessions[session_id] = session
        else:
            return state, changed

    if event_name == "PermissionRequest":
        session["status"] = (
            "working" if approvals_reviewer in AUTO_REVIEWERS else "approval"
        )
    elif event_name == "PreToolUse":
        tool_use_id = payload.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            return state, changed
        awaits_user_input = payload.get("tool_name") in USER_INPUT_TOOLS
        session["status"] = (
            "approval"
            if awaits_user_input
            or any(
                active_tool.get("awaits_user_input")
                for active_tool in session["active_tools"].values()
            )
            else "working"
        )
        tool_state = {
            "phase": classify_tool(
                payload.get("tool_name"),
                payload.get("tool_input"),
            ),
            "started_at": now,
        }
        if awaits_user_input:
            tool_state["awaits_user_input"] = True
        session["active_tools"][tool_use_id] = tool_state
        session["recent_phase"] = None
        session["recent_phase_until"] = None
    elif event_name == "PostToolUse":
        tool_use_id = payload.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            return state, changed
        tool = session["active_tools"].pop(tool_use_id, None)
        if tool is None:
            return state, changed
        if tool.get("awaits_user_input"):
            session["status"] = (
                "approval"
                if any(
                    active_tool.get("awaits_user_input")
                    for active_tool in session["active_tools"].values()
                )
                else "working"
            )
        elif session["status"] != "approval" or not session["active_tools"]:
            session["status"] = "working"
        session["recent_phase"] = tool["phase"]
        session["recent_phase_until"] = now + RECENT_PHASE_TTL_SECONDS
    elif event_name == "Stop":
        turn_id = payload.get("turn_id")
        if (
            not isinstance(turn_id, str)
            or not turn_id
            or session["turn_id"] not in {None, turn_id}
        ):
            return state, changed
        session["status"] = "done"
        session["active_tools"] = {}
        session["recent_phase"] = None
        session["recent_phase_until"] = None

    ttl = DONE_TTL_SECONDS if session["status"] == "done" else ACTIVE_TTL_SECONDS
    _touch_session(session, payload, now, ttl)
    return state, True


def apply_transcript_terminal(
    state: dict[str, Any],
    *,
    session_id: str,
    turn_id: str,
    event_type: str,
    now: float,
) -> tuple[dict[str, Any], bool]:
    """Apply a terminal transcript event to its exact active turn."""
    session = state["sessions"].get(session_id)
    if session is None or session["turn_id"] != turn_id:
        return state, False
    if event_type == "turn_aborted":
        del state["sessions"][session_id]
        return state, True
    if event_type != "task_complete" or session["status"] == "done":
        return state, False

    session["status"] = "done"
    session["active_tools"] = {}
    session["recent_phase"] = None
    session["recent_phase_until"] = None
    session["updated_at"] = now
    session["expires_at"] = now + DONE_TTL_SECONDS
    return state, True


def _is_current_turn(state_path: Path, session_id: str, turn_id: str) -> bool:
    try:
        state, _ = _load_state(state_path)
    except StateVersionError:
        return False
    session = state["sessions"].get(session_id)
    return (
        session is not None
        and session["turn_id"] == turn_id
        and session["expires_at"] > time.time()
    )


def _apply_transcript_terminal_to_file(
    *,
    state_path: Path,
    session_id: str,
    turn_id: str,
    event_type: str,
    now: float,
) -> bool:
    lock_path = state_path.with_name("state.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state, migrated = _load_state(state_path)
        except StateVersionError:
            return False
        state, changed = apply_transcript_terminal(
            state,
            session_id=session_id,
            turn_id=turn_id,
            event_type=event_type,
            now=now,
        )
        if migrated or changed:
            _write_json_atomically(state_path, state)
        return changed


def _start_helper(
    state_path: Path,
    helper_path: Optional[Path],
) -> Optional[subprocess.Popen[bytes]]:
    if helper_path is None or not helper_path.is_file():
        return None
    log_path = state_path.with_name("codex-macropad.log")
    with log_path.open("ab") as log_file:
        return subprocess.Popen(
            [str(helper_path), "--state-file", str(state_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            start_new_session=True,
            close_fds=True,
        )


def _start_transcript_watcher(
    payload: Mapping[str, Any],
    *,
    state_path: Path,
    helper_path: Optional[Path],
) -> Optional[subprocess.Popen[bytes]]:
    transcript_path = payload.get("transcript_path")
    session_id = payload.get("session_id")
    turn_id = payload.get("turn_id")
    if (
        helper_path is None
        or not helper_path.is_file()
        or not isinstance(transcript_path, str)
        or not transcript_path
        or not isinstance(session_id, str)
        or not session_id
        or not isinstance(turn_id, str)
        or not turn_id
    ):
        return None

    log_path = state_path.with_name("codex-macropad.log")
    with log_path.open("ab") as log_file:
        return subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--watch-turn",
                "--transcript-path",
                transcript_path,
                "--state-path",
                str(state_path),
                "--helper-path",
                str(helper_path),
                "--session-id",
                session_id,
                "--turn-id",
                turn_id,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            start_new_session=True,
            close_fds=True,
        )


def watch_transcript(
    *,
    transcript_path: Path,
    state_path: Path,
    helper_path: Optional[Path],
    session_id: str,
    turn_id: str,
    poll_interval: float = 0.25,
    timeout: Optional[float] = None,
) -> bool:
    """Watch one transcript until its exact turn reaches a terminal event."""
    deadline = None if timeout is None else time.monotonic() + timeout
    transcript = None
    pending = b""
    try:
        while _is_current_turn(state_path, session_id, turn_id):
            if transcript is None:
                try:
                    transcript = transcript_path.open("rb")
                except OSError:
                    transcript = None
            if transcript is not None:
                event_type = None
                while True:
                    chunk = transcript.readline()
                    if not chunk:
                        break
                    pending += chunk
                    if not pending.endswith(b"\n"):
                        continue
                    try:
                        line = pending.decode("utf-8")
                    except UnicodeDecodeError:
                        line = ""
                    pending = b""
                    event_type = transcript_terminal_event(line, turn_id)
                    if event_type is not None:
                        break
                if event_type is None and pending:
                    try:
                        line = pending.decode("utf-8")
                    except UnicodeDecodeError:
                        line = ""
                    event_type = transcript_terminal_event(line, turn_id)
                if event_type is not None:
                    changed = _apply_transcript_terminal_to_file(
                        state_path=state_path,
                        session_id=session_id,
                        turn_id=turn_id,
                        event_type=event_type,
                        now=time.time(),
                    )
                    if changed:
                        _start_helper(state_path, helper_path)
                    return changed
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(max(0.0, poll_interval))
    except OSError:
        return False
    finally:
        if transcript is not None:
            transcript.close()
    return False


def handle_event(
    payload: Mapping[str, Any],
    *,
    state_path: Path,
    helper_path: Optional[Path],
    now: float,
) -> Optional[subprocess.Popen[bytes]]:
    """Apply one Codex hook event and start the detached singleton helper."""
    event_name = payload.get("hook_event_name")
    session_id = payload.get("session_id")
    if (
        event_name not in MANAGED_EVENTS
        or not isinstance(session_id, str)
        or not session_id
    ):
        return None
    ignored = is_internal_memories_cwd(payload.get("cwd"))

    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name("state.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state, migrated = _load_state(state_path)
        except StateVersionError as error:
            print(f"CodexMacropad: {error}", file=sys.stderr)
            return None
        approvals_reviewer = (
            effective_approvals_reviewer(payload)
            if event_name == "PermissionRequest"
            else None
        )
        state, changed = apply_event(
            state,
            payload,
            now,
            approvals_reviewer=approvals_reviewer,
        )
        if migrated or changed:
            _write_json_atomically(state_path, state)

    if ignored:
        return _start_helper(state_path, helper_path) if migrated or changed else None

    helper_process = _start_helper(state_path, helper_path)
    if event_name == "UserPromptSubmit":
        _start_transcript_watcher(
            payload,
            state_path=state_path,
            helper_path=helper_path,
        )
    return helper_process


def migrate_v2_state(value: Mapping[str, Any]) -> dict[str, Any]:
    sessions: dict[str, Any] = {}
    for session_id, old in value["sessions"].items():
        sessions[session_id] = {
            "workspace": old["workspace"],
            "status": old["status"],
            "turn_id": None,
            "turn_started_at": old["updated_at"],
            "updated_at": old["updated_at"],
            "expires_at": old["expires_at"],
            "active_tools": {},
            "recent_phase": None,
            "recent_phase_until": None,
        }
    return {"version": STATE_VERSION, "sessions": sessions}


def _load_state(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return empty_state(), False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return empty_state(), False

    if _is_valid_v3_state(value):
        return value, False
    if _is_valid_v2_state(value):
        return migrate_v2_state(value), True
    if (
        isinstance(value, dict)
        and "version" in value
        and value.get("version") not in {2, 3}
    ):
        raise StateVersionError(f"unsupported state schema version {value.get('version')!r}")
    return empty_state(), False


def _is_timestamp(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_valid_v2_state(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("version") != 2:
        return False
    sessions = value.get("sessions")
    if not isinstance(sessions, dict):
        return False
    return all(
        isinstance(session_id, str)
        and isinstance(session, dict)
        and isinstance(session.get("workspace"), str)
        and isinstance(session.get("status"), str)
        and session.get("status") in {"working", "approval", "done"}
        and _is_timestamp(session.get("updated_at"))
        and _is_timestamp(session.get("expires_at"))
        for session_id, session in sessions.items()
    )


def _is_valid_v3_state(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("version") != 3:
        return False
    sessions = value.get("sessions")
    if not isinstance(sessions, dict):
        return False
    for session_id, session in sessions.items():
        if not isinstance(session_id, str) or not isinstance(session, dict):
            return False
        if not isinstance(session.get("workspace"), str):
            return False
        status = session.get("status")
        if not isinstance(status, str) or status not in {"working", "approval", "done"}:
            return False
        turn_id = session.get("turn_id")
        if turn_id is not None and not isinstance(turn_id, str):
            return False
        if not all(
            _is_timestamp(session.get(key))
            for key in ("turn_started_at", "updated_at", "expires_at")
        ):
            return False
        tools = session.get("active_tools")
        if not isinstance(tools, dict):
            return False
        for tool_id, tool in tools.items():
            phase = tool.get("phase") if isinstance(tool, dict) else None
            if (
                not isinstance(tool_id, str)
                or not tool_id
                or not isinstance(tool, dict)
                or not isinstance(phase, str)
                or phase not in TOOL_PHASES
                or not _is_timestamp(tool.get("started_at"))
            ):
                return False
        recent_phase = session.get("recent_phase")
        recent_until = session.get("recent_phase_until")
        if not (
            (recent_phase is None and recent_until is None)
            or (
                isinstance(recent_phase, str)
                and recent_phase in TOOL_PHASES
                and _is_timestamp(recent_until)
            )
        ):
            return False
    return True


def _write_json_atomically(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            json.dump(value, temporary, ensure_ascii=False, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _run_watcher_from_arguments(arguments: list[str]) -> None:
    if not arguments or arguments[0] != "--watch-turn":
        return
    values = arguments[1:]
    if len(values) != 10:
        return
    options = dict(zip(values[::2], values[1::2]))
    expected = {
        "--transcript-path",
        "--state-path",
        "--helper-path",
        "--session-id",
        "--turn-id",
    }
    if set(options) != expected or any(not options[key] for key in expected):
        return
    watch_transcript(
        transcript_path=Path(options["--transcript-path"]),
        state_path=Path(options["--state-path"]),
        helper_path=Path(options["--helper-path"]),
        session_id=options["--session-id"],
        turn_id=options["--turn-id"],
    )


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--watch-turn":
        try:
            _run_watcher_from_arguments(sys.argv[1:])
        except Exception as error:
            print(
                f"CodexMacropad: watcher failed: {type(error).__name__}",
                file=sys.stderr,
            )
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0

    try:
        handle_event(
            payload,
            state_path=Path(
                os.environ.get("CODEX_MACROPAD_STATE_PATH", DEFAULT_STATE_PATH)
            ),
            helper_path=Path(
                os.environ.get("CODEX_MACROPAD_HELPER_PATH", DEFAULT_HELPER_PATH)
            ),
            now=time.time(),
        )
    except Exception as error:
        print(
            f"CodexMacropad: hook failed: {type(error).__name__}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
