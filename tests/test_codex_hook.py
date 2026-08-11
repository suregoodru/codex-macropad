import contextlib
import copy
import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Optional
from unittest import mock

import codex_hook.main as hook


def payload(
    event: str,
    session: str = "s1",
    cwd: str = "/work/macropad",
    **extra: object,
) -> dict[str, object]:
    return {
        "hook_event_name": event,
        "session_id": session,
        "cwd": cwd,
        "turn_id": "turn-1",
        **extra,
    }


def write_transcript(
    directory: str,
    *contexts: tuple[str, str],
) -> Path:
    path = Path(directory) / "rollout.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    "type": "turn_context",
                    "payload": {
                        "turn_id": turn_id,
                        "approvals_reviewer": reviewer,
                    },
                }
            )
            for turn_id, reviewer in contexts
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def terminal_record(event_type: object, turn_id: object, **extra: object) -> str:
    return json.dumps(
        {
            "type": "event_msg",
            "payload": {"type": event_type, "turn_id": turn_id, **extra},
        }
    )


def submit_turn(
    state_path: Path,
    *,
    session_id: str = "s1",
    turn_id: str = "turn-a",
    cwd: str = "/work/macropad",
    now: Optional[float] = None,
) -> None:
    hook.handle_event(
        payload("UserPromptSubmit", session_id, cwd, turn_id=turn_id),
        state_path=state_path,
        helper_path=None,
        now=time.time() if now is None else now,
    )


class ToolClassifierTests(unittest.TestCase):
    def test_classifies_supported_tools_without_semantic_input(self) -> None:
        cases = [
            ("apply_patch", {"command": "private patch"}, "edit"),
            ("Bash", {"command": "python3 -m unittest discover -s tests -v"}, "verify"),
            ("Bash", {"command": "swift test --disable-sandbox"}, "verify"),
            ("Bash", {"command": "git diff --check"}, "verify"),
            ("Bash", {"command": "rg -n 'schema' ."}, "research"),
            ("Bash", {"command": "git status --short"}, "research"),
            ("Bash", {"command": "gh pr view 42"}, "review"),
            ("mcp__gitlab__get_merge_request", {"project": "private"}, "review"),
            ("mcp__jira__search_issues", {"jql": "private"}, "research"),
            ("web__run", {"search_query": [{"q": "private"}]}, "research"),
            ("update_plan", {"plan": "private"}, "run"),
            ("mcp__jira__update_issue", {"summary": "private"}, "run"),
            (None, None, "run"),
        ]

        for tool_name, tool_input, expected in cases:
            with self.subTest(tool_name=tool_name, tool_input=tool_input):
                self.assertEqual(hook.classify_tool(tool_name, tool_input), expected)

    def test_verify_precedes_read_only_command_detection(self) -> None:
        self.assertEqual(
            hook.classify_tool("Bash", {"command": "rg x . && git diff --check"}),
            "verify",
        )


class CodexHookStateTests(unittest.TestCase):
    def test_internal_codex_memories_session_is_ignored(self) -> None:
        state, changed = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload(
                "UserPromptSubmit",
                cwd=str(Path.home() / ".codex" / "memories"),
            ),
            100.0,
        )

        self.assertFalse(changed)
        self.assertEqual(state["sessions"], {})

    def test_user_workspace_named_memories_is_tracked(self) -> None:
        state, changed = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("UserPromptSubmit", cwd="/work/memories"),
            100.0,
        )

        self.assertTrue(changed)
        self.assertEqual(state["sessions"]["s1"]["workspace"], "memories")

    def test_internal_memories_event_removes_only_its_existing_session(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("UserPromptSubmit", "internal", "/work/temporary"),
            100.0,
        )
        state, _ = hook.apply_event(
            state,
            payload("UserPromptSubmit", "parallel", "/work/parallel"),
            101.0,
        )
        parallel_before = copy.deepcopy(state["sessions"]["parallel"])

        state, changed = hook.apply_event(
            state,
            payload(
                "PreToolUse",
                "internal",
                str(Path.home() / ".codex" / "memories"),
                tool_name="Bash",
                tool_use_id="tool-1",
            ),
            102.0,
        )

        self.assertTrue(changed)
        self.assertEqual(set(state["sessions"]), {"parallel"})
        self.assertEqual(state["sessions"]["parallel"], parallel_before)

    def test_transcript_terminal_parser_matches_only_exact_turn(self) -> None:
        cases = (
            ("not-json", None),
            (
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "task_complete", "turn_id": "turn-a"},
                    }
                ),
                None,
            ),
            (terminal_record("task_complete", "other"), None),
            (terminal_record([], "turn-a"), None),
            (
                terminal_record(
                    "task_complete",
                    "turn-a",
                    last_agent_message="private",
                ),
                "task_complete",
            ),
            (terminal_record("turn_aborted", "turn-a", reason="interrupted"), "turn_aborted"),
        )

        for line, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    hook.transcript_terminal_event(line, "turn-a"),
                    expected,
                )

    def test_prompt_starts_new_analyze_turn(self) -> None:
        state, changed = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("UserPromptSubmit", prompt="private prompt"),
            100.0,
        )

        self.assertTrue(changed)
        self.assertEqual(
            state["sessions"]["s1"],
            {
                "workspace": "macropad",
                "status": "working",
                "turn_id": "turn-1",
                "turn_started_at": 100.0,
                "updated_at": 100.0,
                "expires_at": 21_700.0,
                "active_tools": {},
                "recent_phase": None,
                "recent_phase_until": None,
            },
        )

    def test_pre_and_post_tool_track_id_and_recent_phase(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        state, _ = hook.apply_event(
            state,
            payload(
                "PreToolUse",
                tool_name="Bash",
                tool_use_id="tool-1",
                tool_input={"command": "rg secret ."},
            ),
            101.0,
        )

        self.assertEqual(
            state["sessions"]["s1"]["active_tools"]["tool-1"],
            {"phase": "research", "started_at": 101.0},
        )

        state, _ = hook.apply_event(
            state,
            payload("PostToolUse", tool_use_id="tool-1", tool_response="private"),
            105.0,
        )
        session = state["sessions"]["s1"]
        self.assertEqual(session["active_tools"], {})
        self.assertEqual(session["recent_phase"], "research")
        self.assertEqual(session["recent_phase_until"], 106.5)

    def test_request_user_input_shows_approval_until_tool_returns(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        state, _ = hook.apply_event(
            state,
            payload(
                "PreToolUse",
                tool_name="request_user_input",
                tool_use_id="question-1",
                tool_input={"questions": []},
            ),
            101.0,
        )

        self.assertEqual(state["sessions"]["s1"]["status"], "approval")

        state, _ = hook.apply_event(
            state, payload("PostToolUse", tool_use_id="question-1"), 102.0
        )

        self.assertEqual(state["sessions"]["s1"]["status"], "working")

    def test_request_user_input_post_clears_its_approval_with_other_tool_active(
        self,
    ) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        state, _ = hook.apply_event(
            state,
            payload(
                "PreToolUse",
                tool_name="request_user_input",
                tool_use_id="question-1",
                tool_input={"questions": []},
            ),
            101.0,
        )

        self.assertEqual(state["sessions"]["s1"]["status"], "approval")

        state, _ = hook.apply_event(
            state,
            payload(
                "PreToolUse",
                tool_name="Bash",
                tool_use_id="background",
                tool_input={"command": "tail -F state.json"},
            ),
            102.0,
        )

        self.assertEqual(state["sessions"]["s1"]["status"], "approval")

        state, _ = hook.apply_event(
            state, payload("PostToolUse", tool_use_id="question-1"), 103.0
        )

        session = state["sessions"]["s1"]
        self.assertEqual(session["status"], "working")
        self.assertEqual(set(session["active_tools"]), {"background"})

    def test_parallel_post_removes_only_matching_tool(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        for tool_id, command, now in (
            ("read", "rg schema .", 101.0),
            ("check", "swift test", 102.0),
        ):
            state, _ = hook.apply_event(
                state,
                payload(
                    "PreToolUse",
                    tool_name="Bash",
                    tool_use_id=tool_id,
                    tool_input={"command": command},
                ),
                now,
            )

        state, _ = hook.apply_event(
            state, payload("PostToolUse", tool_use_id="check"), 103.0
        )

        self.assertEqual(set(state["sessions"]["s1"]["active_tools"]), {"read"})

    def test_new_prompt_clears_lost_tools_and_restarts_timer(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        state, _ = hook.apply_event(
            state,
            payload(
                "PreToolUse",
                tool_name="Bash",
                tool_use_id="lost",
                tool_input={"command": "sleep 1"},
            ),
            101.0,
        )
        state, _ = hook.apply_event(
            state, payload("UserPromptSubmit", turn_id="turn-2"), 200.0
        )
        session = state["sessions"]["s1"]

        self.assertEqual(session["turn_id"], "turn-2")
        self.assertEqual(session["turn_started_at"], 200.0)
        self.assertEqual(session["active_tools"], {})

    def test_approval_preserves_timer_and_pre_tool_resumes_working(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        state, _ = hook.apply_event(state, payload("PermissionRequest"), 110.0)

        self.assertEqual(state["sessions"]["s1"]["status"], "approval")
        self.assertEqual(state["sessions"]["s1"]["turn_started_at"], 100.0)

        state, _ = hook.apply_event(
            state,
            payload(
                "PreToolUse",
                tool_name="Bash",
                tool_use_id="allowed",
                tool_input={"command": "true"},
            ),
            120.0,
        )

        self.assertEqual(state["sessions"]["s1"]["status"], "working")

    def test_last_post_clears_pending_approval(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        state, _ = hook.apply_event(
            state,
            payload(
                "PreToolUse",
                tool_name="Bash",
                tool_use_id="allowed",
                tool_input={"command": "true"},
            ),
            101.0,
        )
        state, _ = hook.apply_event(state, payload("PermissionRequest"), 102.0)
        state, _ = hook.apply_event(
            state, payload("PostToolUse", tool_use_id="allowed"), 103.0
        )

        self.assertEqual(state["sessions"]["s1"]["status"], "working")
        self.assertEqual(state["sessions"]["s1"]["active_tools"], {})

    def test_post_preserves_approval_while_another_tool_is_active(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        for tool_id in ("tool-one", "tool-two"):
            state, _ = hook.apply_event(
                state,
                payload(
                    "PreToolUse",
                    tool_name="Bash",
                    tool_use_id=tool_id,
                    tool_input={"command": "true"},
                ),
                101.0,
            )
        state, _ = hook.apply_event(state, payload("PermissionRequest"), 102.0)
        state, _ = hook.apply_event(
            state, payload("PostToolUse", tool_use_id="tool-one"), 103.0
        )

        self.assertEqual(state["sessions"]["s1"]["status"], "approval")

        state, _ = hook.apply_event(
            state, payload("PostToolUse", tool_use_id="tool-two"), 104.0
        )

        self.assertEqual(state["sessions"]["s1"]["status"], "working")

    def test_unknown_post_does_not_remove_active_tool(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        state, _ = hook.apply_event(
            state,
            payload(
                "PreToolUse",
                tool_name="Bash",
                tool_use_id="known",
                tool_input={"command": "true"},
            ),
            101.0,
        )
        state, changed = hook.apply_event(
            state, payload("PostToolUse", tool_use_id="unknown"), 102.0
        )

        self.assertFalse(changed)
        self.assertEqual(set(state["sessions"]["s1"]["active_tools"]), {"known"})

    def test_stop_clears_tools_and_freezes_done_timestamp(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        state, _ = hook.apply_event(state, payload("Stop"), 154.0)
        session = state["sessions"]["s1"]

        self.assertEqual(session["status"], "done")
        self.assertEqual(session["active_tools"], {})
        self.assertEqual(session["updated_at"], 154.0)
        self.assertEqual(session["expires_at"], 166.0)

    def test_turn_aborted_removes_only_matching_parallel_session(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("UserPromptSubmit", "stopped", "/work/stopped", turn_id="turn-a"),
            100.0,
        )
        state, _ = hook.apply_event(
            state,
            payload("UserPromptSubmit", "parallel", "/work/parallel", turn_id="turn-b"),
            101.0,
        )
        parallel_before = copy.deepcopy(state["sessions"]["parallel"])

        state, changed = hook.apply_transcript_terminal(
            state,
            session_id="stopped",
            turn_id="turn-a",
            event_type="turn_aborted",
            now=110.0,
        )

        self.assertTrue(changed)
        self.assertEqual(set(state["sessions"]), {"parallel"})
        self.assertEqual(state["sessions"]["parallel"], parallel_before)

    def test_task_complete_finishes_turn_when_stop_was_not_applied(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("UserPromptSubmit", turn_id="turn-a"),
            100.0,
        )
        state, _ = hook.apply_event(
            state,
            payload(
                "PreToolUse",
                turn_id="turn-a",
                tool_name="Bash",
                tool_use_id="lost-tool",
                tool_input={"command": "true"},
            ),
            101.0,
        )

        state, changed = hook.apply_transcript_terminal(
            state,
            session_id="s1",
            turn_id="turn-a",
            event_type="task_complete",
            now=154.0,
        )

        self.assertTrue(changed)
        session = state["sessions"]["s1"]
        self.assertEqual(session["status"], "done")
        self.assertEqual(session["active_tools"], {})
        self.assertIsNone(session["recent_phase"])
        self.assertIsNone(session["recent_phase_until"])
        self.assertEqual(session["updated_at"], 154.0)
        self.assertEqual(session["expires_at"], 166.0)

    def test_old_terminal_event_does_not_stop_new_turn_in_same_session(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("UserPromptSubmit", turn_id="old-turn"),
            100.0,
        )
        state, _ = hook.apply_event(
            state,
            payload("UserPromptSubmit", turn_id="new-turn"),
            200.0,
        )
        current_before = copy.deepcopy(state["sessions"]["s1"])

        state, changed = hook.apply_transcript_terminal(
            state,
            session_id="s1",
            turn_id="old-turn",
            event_type="turn_aborted",
            now=210.0,
        )

        self.assertFalse(changed)
        self.assertEqual(state["sessions"]["s1"], current_before)

    def test_late_stop_for_old_turn_does_not_finish_new_turn(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("UserPromptSubmit", turn_id="old-turn"),
            100.0,
        )
        state, _ = hook.apply_event(
            state,
            payload("UserPromptSubmit", turn_id="new-turn"),
            200.0,
        )
        current_before = copy.deepcopy(state["sessions"]["s1"])

        state, changed = hook.apply_event(
            state,
            payload("Stop", turn_id="old-turn"),
            210.0,
        )

        self.assertFalse(changed)
        self.assertEqual(state["sessions"]["s1"], current_before)

    def test_stop_after_abort_does_not_resurrect_parallel_session(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("UserPromptSubmit", "stopped", turn_id="turn-a"),
            100.0,
        )
        state, _ = hook.apply_event(
            state,
            payload("UserPromptSubmit", "parallel", turn_id="turn-b"),
            101.0,
        )
        state, _ = hook.apply_transcript_terminal(
            state,
            session_id="stopped",
            turn_id="turn-a",
            event_type="turn_aborted",
            now=110.0,
        )
        parallel_before = copy.deepcopy(state["sessions"]["parallel"])

        state, changed = hook.apply_event(
            state,
            payload("Stop", "stopped", turn_id="turn-a"),
            111.0,
        )

        self.assertFalse(changed)
        self.assertEqual(set(state["sessions"]), {"parallel"})
        self.assertEqual(state["sessions"]["parallel"], parallel_before)

    def test_session_end_removes_only_matching_session(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("UserPromptSubmit", "s1", "/work/one"),
            1.0,
        )
        state, _ = hook.apply_event(
            state,
            payload("PermissionRequest", "s2", "/work/two"),
            2.0,
        )

        state, changed = hook.apply_event(state, payload("SessionEnd", "s1"), 10.0)

        self.assertTrue(changed)
        self.assertEqual(set(state["sessions"]), {"s2"})

    def test_unknown_tool_events_do_not_create_session(self) -> None:
        state, changed = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("PostToolUse", tool_use_id="unknown"),
            100.0,
        )

        self.assertFalse(changed)
        self.assertEqual(state["sessions"], {})

    def test_event_without_session_id_is_ignored(self) -> None:
        state, changed = hook.apply_event(
            {"version": 3, "sessions": {}},
            {"hook_event_name": "Stop", "cwd": "/work/macropad"},
            100.0,
        )

        self.assertFalse(changed)
        self.assertEqual(state["sessions"], {})

    def test_expired_session_is_removed_without_resurrecting_stop(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}},
            payload("UserPromptSubmit", "expired", "/work/old"),
            1.0,
        )

        state, changed = hook.apply_event(state, payload("Stop", "fresh"), 21_601.0)

        self.assertTrue(changed)
        self.assertEqual(state["sessions"], {})

    def test_workspace_is_safe_utf8_basename(self) -> None:
        workspace = hook.normalize_workspace("/tmp/очень-\x00длинное-название-проекта")

        self.assertNotIn("\x00", workspace)
        self.assertLessEqual(len(workspace.encode("utf-8")), 20)
        workspace.encode("utf-8").decode("utf-8")

    def test_workspace_falls_back_for_root_cwd(self) -> None:
        self.assertEqual(hook.normalize_workspace("/"), "workspace")


class CodexHookFileTests(unittest.TestCase):
    def test_internal_codex_memories_event_does_not_start_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            internal_payload = payload(
                "UserPromptSubmit",
                cwd=str(Path.home() / ".codex" / "memories"),
                transcript_path=str(Path(directory) / "rollout.jsonl"),
            )

            with mock.patch.object(hook, "_start_helper") as start_helper:
                with mock.patch.object(
                    hook, "_start_transcript_watcher"
                ) as start_watcher:
                    result = hook.handle_event(
                        internal_payload,
                        state_path=state_path,
                        helper_path=None,
                        now=100.0,
                    )

            self.assertIsNone(result)
            self.assertFalse(state_path.exists())
            start_helper.assert_not_called()
            start_watcher.assert_not_called()

    def test_internal_event_refreshes_display_after_removing_stale_session(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            submit_turn(state_path, session_id="internal", cwd="/work/temporary")
            internal_payload = payload(
                "PreToolUse",
                "internal",
                str(Path.home() / ".codex" / "memories"),
                tool_name="Bash",
                tool_use_id="tool-1",
            )

            with mock.patch.object(hook, "_start_helper") as start_helper:
                result = hook.handle_event(
                    internal_payload,
                    state_path=state_path,
                    helper_path=None,
                    now=100.0,
                )

            self.assertIs(result, start_helper.return_value)
            start_helper.assert_called_once_with(state_path, None)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["sessions"], {})

    def test_current_turn_check_rejects_expired_state_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            submit_turn(state_path, now=100.0)

            with mock.patch.object(
                hook.time,
                "time",
                return_value=100.0 + hook.ACTIVE_TTL_SECONDS,
            ):
                current = hook._is_current_turn(state_path, "s1", "turn-a")

            self.assertFalse(current)

    def test_watcher_uses_renewed_state_expiry_instead_of_launch_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            transcript_path = root / "rollout.jsonl"
            transcript_path.touch()
            submit_turn(state_path, now=100.0)
            hook.handle_event(
                payload(
                    "PreToolUse",
                    turn_id="turn-a",
                    tool_name="Bash",
                    tool_use_id="long-tool",
                    tool_input={"command": "true"},
                ),
                state_path=state_path,
                helper_path=None,
                now=100.0 + 5 * 60 * 60,
            )

            def append_terminal(_: float) -> None:
                transcript_path.write_text(
                    terminal_record("turn_aborted", "turn-a") + "\n",
                    encoding="utf-8",
                )

            with mock.patch.object(
                hook.time,
                "time",
                return_value=101.0 + hook.ACTIVE_TTL_SECONDS,
            ):
                with mock.patch.object(
                    hook.time,
                    "monotonic",
                    side_effect=(0.0, hook.ACTIVE_TTL_SECONDS + 1.0),
                ):
                    with mock.patch.object(
                        hook.time,
                        "sleep",
                        side_effect=append_terminal,
                    ):
                        changed = hook.watch_transcript(
                            transcript_path=transcript_path,
                            state_path=state_path,
                            helper_path=None,
                            session_id="s1",
                            turn_id="turn-a",
                            poll_interval=0.25,
                        )

            self.assertTrue(changed)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["sessions"], {})

    def test_watcher_applies_matching_terminal_record_to_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            transcript_path = root / "rollout.jsonl"
            now = time.time()
            submit_turn(
                state_path,
                session_id="stopped",
                turn_id="turn-a",
                cwd="/work/stopped",
                now=now,
            )
            submit_turn(
                state_path,
                session_id="parallel",
                turn_id="turn-b",
                cwd="/work/parallel",
                now=now + 1.0,
            )
            state_before = json.loads(state_path.read_text(encoding="utf-8"))
            parallel_before = copy.deepcopy(
                state_before["sessions"]["parallel"]
            )
            transcript_path.write_text(
                "\n".join(
                    (
                        "not-json",
                        terminal_record("task_complete", "other-turn"),
                        terminal_record(
                            "turn_aborted",
                            "turn-a",
                            reason="interrupted",
                        ),
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            changed = hook.watch_transcript(
                transcript_path=transcript_path,
                state_path=state_path,
                helper_path=None,
                session_id="stopped",
                turn_id="turn-a",
                poll_interval=0.0,
                timeout=0.1,
            )

            self.assertTrue(changed)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(set(state["sessions"]), {"parallel"})
            self.assertEqual(state["sessions"]["parallel"], parallel_before)

    def test_watcher_skips_invalid_utf8_before_valid_terminal_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            transcript_path = root / "rollout.jsonl"
            submit_turn(state_path)
            terminal = terminal_record("turn_aborted", "turn-a").encode("utf-8")
            transcript_path.write_bytes(b"\xff\n" + terminal + b"\n")

            changed = hook.watch_transcript(
                transcript_path=transcript_path,
                state_path=state_path,
                helper_path=None,
                session_id="s1",
                turn_id="turn-a",
                poll_interval=0.0,
                timeout=0.1,
            )

            self.assertTrue(changed)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["sessions"], {})

    def test_watcher_starts_helper_after_terminal_state_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            transcript_path = root / "rollout.jsonl"
            helper_path = root / "MacropadDisplay"
            helper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            helper_path.chmod(0o755)
            submit_turn(state_path)
            transcript_path.write_text(
                terminal_record("turn_aborted", "turn-a", reason="interrupted")
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(hook.subprocess, "Popen") as launch:
                changed = hook.watch_transcript(
                    transcript_path=transcript_path,
                    state_path=state_path,
                    helper_path=helper_path,
                    session_id="s1",
                    turn_id="turn-a",
                    poll_interval=0.0,
                    timeout=0.1,
                )

            self.assertTrue(changed)
            launch.assert_called_once_with(
                [str(helper_path), "--state-file", str(state_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=mock.ANY,
                start_new_session=True,
                close_fds=True,
            )

    def test_watcher_observes_terminal_event_appended_after_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            transcript_path = root / "rollout.jsonl"
            transcript_path.touch()
            submit_turn(state_path)

            def append_terminal() -> None:
                time.sleep(0.02)
                transcript_path.write_text(
                    terminal_record("turn_aborted", "turn-a") + "\n",
                    encoding="utf-8",
                )

            writer = threading.Thread(target=append_terminal)
            writer.start()
            changed = hook.watch_transcript(
                transcript_path=transcript_path,
                state_path=state_path,
                helper_path=None,
                session_id="s1",
                turn_id="turn-a",
                poll_interval=0.005,
                timeout=1.0,
            )
            writer.join(timeout=2)

            self.assertTrue(changed)
            self.assertFalse(writer.is_alive())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["sessions"], {})

    def test_concurrent_watchers_preserve_unrelated_parallel_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            now = time.time()
            for offset, (session_id, turn_id) in enumerate(
                (
                    ("aborted", "turn-a"),
                    ("completed", "turn-b"),
                    ("survivor", "turn-c"),
                )
            ):
                submit_turn(
                    state_path,
                    session_id=session_id,
                    turn_id=turn_id,
                    cwd=f"/work/{session_id}",
                    now=now + offset,
                )
            state_before = json.loads(state_path.read_text(encoding="utf-8"))
            survivor_before = copy.deepcopy(
                state_before["sessions"]["survivor"]
            )
            events = {
                "aborted": ("turn-a", "turn_aborted"),
                "completed": ("turn-b", "task_complete"),
            }
            transcripts: dict[str, Path] = {}
            for session_id, (turn_id, event_type) in events.items():
                transcript_path = root / f"{session_id}.jsonl"
                transcript_path.write_text(
                    terminal_record(event_type, turn_id) + "\n",
                    encoding="utf-8",
                )
                transcripts[session_id] = transcript_path

            barrier = threading.Barrier(3)
            results: dict[str, bool] = {}

            def reconcile(session_id: str) -> None:
                turn_id, _ = events[session_id]
                barrier.wait()
                results[session_id] = hook.watch_transcript(
                    transcript_path=transcripts[session_id],
                    state_path=state_path,
                    helper_path=None,
                    session_id=session_id,
                    turn_id=turn_id,
                    poll_interval=0.0,
                    timeout=1.0,
                )

            threads = [
                threading.Thread(target=reconcile, args=(session_id,))
                for session_id in events
            ]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=2)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(results, {"aborted": True, "completed": True})
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(set(state["sessions"]), {"completed", "survivor"})
            self.assertEqual(state["sessions"]["completed"]["status"], "done")
            self.assertEqual(state["sessions"]["survivor"], survivor_before)

    def test_permission_request_uses_current_turn_reviewer(self) -> None:
        for reviewer, expected in (
            ("auto_review", "working"),
            ("guardian_subagent", "working"),
            ("user", "approval"),
            ("future_reviewer", "approval"),
        ):
            with self.subTest(reviewer=reviewer):
                with tempfile.TemporaryDirectory() as directory:
                    transcript = write_transcript(
                        directory,
                        ("other-turn", "user"),
                        ("turn-1", reviewer),
                    )
                    state_path = Path(directory) / "state.json"
                    for offset, event in enumerate(
                        (
                            payload(
                                "UserPromptSubmit",
                                transcript_path=str(transcript),
                            ),
                            payload(
                                "PermissionRequest",
                                transcript_path=str(transcript),
                            ),
                        )
                    ):
                        hook.handle_event(
                            event,
                            state_path=state_path,
                            helper_path=None,
                            now=100.0 + offset,
                        )

                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        state["sessions"]["s1"]["status"],
                        expected,
                    )

    def test_malformed_transcript_line_does_not_hide_current_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transcript = write_transcript(directory, ("turn-1", "auto_review"))
            valid = transcript.read_text(encoding="utf-8")
            transcript.write_text("not-json\n" + valid, encoding="utf-8")
            state_path = Path(directory) / "state.json"
            for offset, event in enumerate(
                (
                    payload("UserPromptSubmit", transcript_path=str(transcript)),
                    payload("PermissionRequest", transcript_path=str(transcript)),
                )
            ):
                hook.handle_event(
                    event,
                    state_path=state_path,
                    helper_path=None,
                    now=100.0 + offset,
                )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["sessions"]["s1"]["status"], "working")

    def test_permission_without_transcript_falls_back_to_approval(self) -> None:
        state, _ = hook.apply_event(
            {"version": 3, "sessions": {}}, payload("UserPromptSubmit"), 100.0
        )
        state, _ = hook.apply_event(state, payload("PermissionRequest"), 101.0)

        self.assertEqual(state["sessions"]["s1"]["status"], "approval")

    def test_stop_writes_private_versioned_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"

            hook.handle_event(
                {
                    **payload("UserPromptSubmit"),
                    "prompt": "private prompt",
                    "transcript_path": "/private/transcript.jsonl",
                },
                state_path=state_path,
                helper_path=None,
                now=90.0,
            )
            hook.handle_event(
                {
                    **payload("Stop"),
                    "last_assistant_message": "private answer",
                    "transcript_path": "/private/transcript.jsonl",
                },
                state_path=state_path,
                helper_path=None,
                now=100.0,
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["version"], 3)
            self.assertEqual(
                state["sessions"],
                {
                    "s1": {
                        "workspace": "macropad",
                        "status": "done",
                        "turn_id": "turn-1",
                        "turn_started_at": 90.0,
                        "updated_at": 100.0,
                        "expires_at": 112.0,
                        "active_tools": {},
                        "recent_phase": None,
                        "recent_phase_until": None,
                    }
                },
            )
            serialized = state_path.read_text(encoding="utf-8")
            self.assertNotIn("private prompt", serialized)
            self.assertNotIn("private answer", serialized)
            self.assertNotIn("transcript", serialized)

    def test_v2_state_is_migrated_atomically_on_next_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "sessions": {
                            "s1": {
                                "workspace": "macropad",
                                "status": "approval",
                                "updated_at": 90.0,
                                "expires_at": 21_690.0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            hook.handle_event(
                payload(
                    "PreToolUse",
                    tool_name="Bash",
                    tool_use_id="t1",
                    tool_input={"command": "rg private ."},
                ),
                state_path=state_path,
                helper_path=None,
                now=100.0,
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["version"], 3)
            self.assertEqual(state["sessions"]["s1"]["turn_started_at"], 90.0)
            self.assertEqual(
                state["sessions"]["s1"]["active_tools"]["t1"]["phase"],
                "research",
            )

    def test_corrupt_state_is_replaced_by_valid_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text("not-json", encoding="utf-8")

            hook.handle_event(
                payload("UserPromptSubmit"),
                state_path=state_path,
                helper_path=None,
                now=10.0,
            )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["sessions"]["s1"]["status"], "working")

    def test_v3_state_does_not_persist_hook_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            events = [
                payload("UserPromptSubmit", prompt="private prompt"),
                payload(
                    "PreToolUse",
                    tool_name="Bash",
                    tool_use_id="tool-1",
                    tool_input={"command": "private command"},
                ),
                payload(
                    "PostToolUse",
                    tool_name="Bash",
                    tool_use_id="tool-1",
                    tool_input={"command": "private command"},
                    tool_response="private response",
                ),
                payload("Stop", last_assistant_message="private answer"),
            ]
            for offset, event in enumerate(events):
                hook.handle_event(
                    event,
                    state_path=state_path,
                    helper_path=None,
                    now=100.0 + offset,
                )

            serialized = state_path.read_text(encoding="utf-8")
            session = json.loads(serialized)["sessions"]["s1"]
            self.assertEqual(
                set(session),
                {
                    "workspace",
                    "status",
                    "turn_id",
                    "turn_started_at",
                    "updated_at",
                    "expires_at",
                    "active_tools",
                    "recent_phase",
                    "recent_phase_until",
                },
            )
            for private_value in (
                "private prompt",
                "private command",
                "private response",
                "private answer",
            ):
                self.assertNotIn(private_value, serialized)

    def test_unknown_schema_is_preserved_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            original = '{"version":99,"sessions":{"keep":{}}}\n'
            state_path.write_text(original, encoding="utf-8")

            diagnostics = io.StringIO()
            with contextlib.redirect_stderr(diagnostics):
                process = hook.handle_event(
                    payload("Stop"),
                    state_path=state_path,
                    helper_path=None,
                    now=100.0,
                )

            self.assertIsNone(process)
            self.assertEqual(state_path.read_text(encoding="utf-8"), original)
            self.assertIn("unsupported state schema version 99", diagnostics.getvalue())

    def test_concurrent_events_keep_both_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            barrier = threading.Barrier(3)

            def update(session: str) -> None:
                barrier.wait()
                hook.handle_event(
                    payload("UserPromptSubmit", session, f"/work/{session}"),
                    state_path=state_path,
                    helper_path=None,
                    now=100.0,
                )

            threads = [
                threading.Thread(target=update, args=("one",)),
                threading.Thread(target=update, args=("two",)),
            ]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=2)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(set(state["sessions"]), {"one", "two"})

    def test_main_returns_zero_without_stdout_or_private_error_text(self) -> None:
        stdin = io.StringIO(json.dumps(payload("UserPromptSubmit")))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(hook.sys, "stdin", stdin):
            with mock.patch.object(
                hook,
                "handle_event",
                side_effect=RuntimeError("private command"),
            ):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                    stderr
                ):
                    exit_code = hook.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("private command", stderr.getvalue())
        self.assertIn("hook failed: RuntimeError", stderr.getvalue())

    def test_watcher_cli_reconciles_aborted_turn_in_separate_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            transcript_path = root / "rollout.jsonl"
            state_path = root / "state.json"
            helper_path = root / "MacropadDisplay"
            hook.handle_event(
                payload("UserPromptSubmit", turn_id="turn-a"),
                state_path=state_path,
                helper_path=None,
                now=time.time(),
            )
            transcript_path.write_text(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "turn_aborted",
                            "turn_id": "turn-a",
                            "reason": "interrupted",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            arguments = [
                hook.sys.executable,
                str(Path(hook.__file__).resolve()),
                "--watch-turn",
                "--transcript-path",
                str(transcript_path),
                "--state-path",
                str(state_path),
                "--helper-path",
                str(helper_path),
                "--session-id",
                "s1",
                "--turn-id",
                "turn-a",
            ]

            result = subprocess.run(
                arguments,
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["sessions"], {})

    def test_recognized_event_starts_detached_helper_with_state_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            helper_path = root / "MacropadDisplay"
            helper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            helper_path.chmod(0o755)
            process = mock.Mock()

            with mock.patch.object(
                hook.subprocess,
                "Popen",
                return_value=process,
            ) as launch:
                with mock.patch.object(
                    hook.subprocess,
                    "run",
                    side_effect=AssertionError("launchctl must not be used"),
                ):
                    result = hook.handle_event(
                        payload("UserPromptSubmit"),
                        state_path=state_path,
                        helper_path=helper_path,
                        now=100.0,
                    )

            self.assertIs(result, process)
            launch.assert_called_once_with(
                [
                    str(helper_path),
                    "--state-file",
                    str(state_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=mock.ANY,
                start_new_session=True,
                close_fds=True,
            )

    def test_prompt_starts_detached_watcher_for_exact_session_and_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            transcript_path = root / "rollout.jsonl"
            transcript_path.touch()
            helper_path = root / "MacropadDisplay"
            helper_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            helper_path.chmod(0o755)
            helper_process = mock.Mock()
            watcher_process = mock.Mock()

            with mock.patch.object(
                hook.subprocess,
                "Popen",
                side_effect=(helper_process, watcher_process),
            ) as launch:
                result = hook.handle_event(
                    payload(
                        "UserPromptSubmit",
                        "session-a",
                        turn_id="turn-a",
                        transcript_path=str(transcript_path),
                    ),
                    state_path=state_path,
                    helper_path=helper_path,
                    now=100.0,
                )

            self.assertIs(result, helper_process)
            self.assertEqual(launch.call_count, 2)
            self.assertEqual(
                launch.call_args_list[1],
                mock.call(
                    [
                        hook.sys.executable,
                        str(Path(hook.__file__).resolve()),
                        "--watch-turn",
                        "--transcript-path",
                        str(transcript_path),
                        "--state-path",
                        str(state_path),
                        "--helper-path",
                        str(helper_path),
                        "--session-id",
                        "session-a",
                        "--turn-id",
                        "turn-a",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=mock.ANY,
                    start_new_session=True,
                    close_fds=True,
                ),
            )


if __name__ == "__main__":
    unittest.main()
