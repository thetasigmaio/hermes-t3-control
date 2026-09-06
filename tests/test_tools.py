from __future__ import annotations

import copy
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import schemas
import tools
import auth
from tests.support import (
    LoopbackServer,
    Response,
    detail_snapshot,
    latest_turn,
    message,
    proposed_plan,
    session,
    shell_snapshot,
    source_proposed_plan,
)


class FakeContext:
    def __init__(
        self,
        base_url: str,
        *,
        default_runtime_mode: object = None,
        auth_mode: object = "external-token",
        default_instance_id: object = None,
        default_model: object = None,
        default_reasoning_effort: object = None,
        default_model_aliases: object = None,
    ) -> None:
        self.base_url = base_url
        self.default_runtime_mode = default_runtime_mode
        self.auth_mode = auth_mode
        self.default_instance_id = default_instance_id
        self.default_model = default_model
        self.default_reasoning_effort = default_reasoning_effort
        self.default_model_aliases = default_model_aliases

    def get_config(self, key: str, default: object = None) -> object:
        if key == "base_url":
            return self.base_url
        if key == "default_runtime_mode":
            return self.default_runtime_mode
        if key == "auth_mode":
            return self.auth_mode
        if key == "default_instance_id":
            return self.default_instance_id
        if key == "default_model":
            return self.default_model
        if key == "default_reasoning_effort":
            return self.default_reasoning_effort
        if key == "default_model_aliases":
            return self.default_model_aliases
        return default


def invoke(
    server: LoopbackServer,
    operation,
    args: object,
    *,
    context: FakeContext | None = None,
    credential: str | None = None,
    **kwargs: object,
) -> dict:
    handler = tools.bind_handler(context or FakeContext(server.base_url), operation)
    resolved_credential = server.token if credential is None else credential
    with mock.patch.object(
        tools,
        "_profile_secret",
        return_value=resolved_credential,
    ), mock.patch.object(auth, "_profile_secret", return_value=resolved_credential):
        return json.loads(handler(args, **kwargs))


def captured_dispatch(store: list[dict]):
    def responder(request: dict) -> Response:
        command = json.loads(request["body"])
        store.append(command)
        return Response(value={"sequence": len(store)})

    return responder


def projected_message(
    *,
    message_id: str,
    role: str,
    text: str,
    turn_id: str | None,
    streaming: bool = False,
    created_at: str = "2026-08-21T12:00:00Z",
    updated_at: str | None = None,
) -> dict:
    return {
        "id": message_id,
        "role": role,
        "text": text,
        "turnId": turn_id,
        "streaming": streaming,
        "createdAt": created_at,
        "updatedAt": updated_at or created_at,
    }


def activity(
    *,
    activity_id: str,
    kind: str,
    payload: dict,
    sequence: int,
    created_at: str,
    turn_id: str | None = "turn-1",
) -> dict:
    return {
        "id": activity_id,
        "tone": "approval" if kind.startswith("approval.") else "info",
        "kind": kind,
        "summary": kind,
        "payload": payload,
        "turnId": turn_id,
        "sequence": sequence,
        "createdAt": created_at,
    }


def with_projection_fields(detail: dict, *, thread_sequence: int | None = None) -> dict:
    projected = copy.deepcopy(detail)
    projected["thread"].setdefault("activities", [])
    projected["thread"].setdefault("checkpoints", [])
    if "page" in projected:
        projected["page"]["threadSequence"] = (
            projected["snapshotSequence"]
            if thread_sequence is None
            else thread_sequence
        )
    return projected


def projection_responder(shell: dict, detail: dict):
    def responder(request: dict) -> Response:
        if request["path"] == "/api/orchestration/shell":
            return Response(value=shell)
        if request["path"].startswith("/api/orchestration/threads/"):
            return Response(value=detail)
        return Response(status=404, value={"code": "not_found"})

    return responder


def settlement_descriptor(*, enabled: bool | None = True) -> dict:
    descriptor = {
        "environmentId": "environment-test",
        "serverVersion": "0.0.34-nightly.20260820.1141",
    }
    if enabled is not None:
        descriptor["capabilities"] = {"threadSettlement": enabled}
    return descriptor


class ReadToolTests(unittest.TestCase):
    def test_threads_and_detail_reads(self) -> None:
        shell = shell_snapshot(sequence=3)
        detail = detail_snapshot(sequence=4)
        with LoopbackServer([Response(value=shell), Response(value=detail)]) as server:
            listed = invoke(
                server,
                tools.t3_threads,
                {"view": "raw"},
                ignored_context=True,
            )
            read = invoke(
                server,
                tools.t3_thread_read,
                {
                    "thread_id": "thread-1",
                    "view": "raw",
                    "turn_limit": 7,
                    "before_cursor": "older",
                },
            )
            self.assertTrue(listed["ok"])
            self.assertEqual(listed["shell"]["snapshotSequence"], 3)
            self.assertTrue(read["ok"])
            self.assertEqual(
                server.requests[1]["path"],
                "/api/orchestration/threads/thread-1?turnLimit=7&beforeCursor=older",
            )

    def test_nonfinite_additive_json_never_reaches_public_result_serialization(self) -> None:
        raw = json.dumps(shell_snapshot(extra=True)).replace(
            '"futureTopLevelField": [1, 2, 3]',
            '"futureTopLevelField": 1e999',
        ).encode("utf-8")
        with LoopbackServer([Response(body=raw)]) as server:
            result = invoke(server, tools.t3_threads, {"view": "raw"})

        self.assertEqual(result["error_code"], "response_schema_error")
        self.assertNotIn(server.token, json.dumps(result))

    def test_validation_and_secret_fail_closed_before_http(self) -> None:
        with LoopbackServer([]) as server:
            invalid = invoke(server, tools.t3_thread_read, {"thread_id": "x", "unknown": True})
            incomplete = invoke(
                server,
                tools.t3_thread_create,
                {"project_id": "project-1", "title": "Thread", "instance_id": "codex"},
            )
            handler = tools.bind_handler(FakeContext(server.base_url), tools.t3_threads)
            with mock.patch.object(
                tools, "_profile_secret", side_effect=RuntimeError("hidden")
            ), mock.patch.object(
                auth, "_profile_secret", side_effect=RuntimeError("hidden")
            ):
                unavailable = json.loads(handler({}))
            self.assertEqual(invalid["error_code"], "invalid_input")
            self.assertEqual(incomplete["error_code"], "invalid_input")
            self.assertEqual(unavailable["error_code"], "configuration_error")
            self.assertNotIn("hidden", json.dumps(unavailable))
            self.assertEqual(server.requests, [])

    def test_whitespace_secret_and_unexpected_exception_are_sanitized_before_http(self) -> None:
        with LoopbackServer([]) as server:
            handler = tools.bind_handler(FakeContext(server.base_url), tools.t3_threads)
            for invalid_secret in (
                "   ",
                f" {server.token}",
                f"{server.token}\n",
                f"{server.token}\r\nInjected: yes",
                f"{server.token} internal",
                f"{server.token}\tinternal",
                f"{server.token}\N{LATIN SMALL LETTER E WITH ACUTE}",
            ):
                with self.subTest(secret_shape=(len(invalid_secret), invalid_secret.strip() == invalid_secret)):
                    with mock.patch.object(
                        tools, "_profile_secret", return_value=invalid_secret
                    ), mock.patch.object(
                        auth, "_profile_secret", return_value=invalid_secret
                    ):
                        self.assertTrue(tools.check_t3_available())
                        result = json.loads(handler({}))
                    self.assertEqual(result["error_code"], "configuration_error")
                    self.assertNotIn(invalid_secret, json.dumps(result))

            exception_text = "unexpected-" + server.token
            with mock.patch.object(
                tools,
                "_make_client",
                side_effect=RuntimeError(exception_text),
            ):
                internal = json.loads(handler({}))
            encoded = json.dumps(internal)
            self.assertEqual(internal["error_code"], "internal_error")
            self.assertNotIn(server.token, encoded)
            self.assertNotIn(exception_text, encoded)
            self.assertEqual(server.requests, [])


class AgentFacingReadToolTests(unittest.TestCase):
    def _compact_shell(self) -> dict:
        now = datetime.now(timezone.utc)

        def stamp(minutes_ago: int) -> str:
            return (
                (now - timedelta(minutes=minutes_ago))
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )

        shell = shell_snapshot(sequence=41)
        project = shell["projects"][0]
        project.update(
            {
                "id": "project-alpha",
                "title": "Mission Alpha",
                "workspaceRoot": "/work/alpha",
            }
        )
        other_project = copy.deepcopy(project)
        other_project.update(
            {
                "id": "project-beta",
                "title": "Mission Beta",
                "workspaceRoot": "/work/beta",
            }
        )

        def compact_thread(
            thread_id: str,
            *,
            project_id: str = "project-alpha",
            title: str,
            updated_at: str,
            status: str = "ready",
            pending_approval: bool = False,
        ) -> dict:
            current = session(status=status)
            current["threadId"] = thread_id
            current["updatedAt"] = updated_at
            thread = copy.deepcopy(shell["threads"][0])
            thread.update(
                {
                    "id": thread_id,
                    "projectId": project_id,
                    "title": title,
                    "updatedAt": updated_at,
                    "session": current,
                    "latestUserMessageAt": updated_at,
                    "hasPendingApprovals": pending_approval,
                    "hasPendingUserInput": False,
                    "hasActionableProposedPlan": False,
                }
            )
            return thread

        tied = stamp(2)
        shell["projects"] = [project, other_project]
        shell["threads"] = [
            compact_thread("thread-b", title="Needle beta", updated_at=tied),
            compact_thread("thread-a", title="NEEDLE alpha", updated_at=tied),
            compact_thread(
                "thread-blocked",
                title="Needle blocked",
                updated_at=stamp(1),
                status="running",
                pending_approval=True,
            ),
            compact_thread(
                "thread-running",
                title="Other",
                updated_at=stamp(3),
                status="running",
            ),
            compact_thread(
                "thread-old",
                title="Needle old",
                updated_at=stamp(2_000),
            ),
            compact_thread(
                "thread-other-project",
                project_id="project-beta",
                title="Needle elsewhere",
                updated_at=stamp(1),
            ),
        ]
        shell["updatedAt"] = stamp(0)
        return shell

    def test_threads_default_compact_filters_then_sorts_and_limits(self) -> None:
        shell = self._compact_shell()
        with LoopbackServer([Response(value=shell)]) as server:
            result = invoke(
                server,
                tools.t3_threads,
                {
                    "project": "ALPHA",
                    "workspace": "/work/alpha",
                    "title_query": "needle",
                    "lifecycle": "ready",
                    "updated_within_minutes": 60,
                    "limit": 2,
                },
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["view"], "compact")
        self.assertEqual(
            [thread["id"] for thread in result["threads"]],
            ["thread-a", "thread-b"],
        )
        first = result["threads"][0]
        self.assertEqual(
            first["project"],
            {
                "id": "project-alpha",
                "title": "Mission Alpha",
                "workspace": "/work/alpha",
            },
        )
        self.assertEqual(first["lifecycle"], "ready")
        self.assertEqual(first["liveness"], "settled")
        self.assertIn("runtime_mode", first)
        self.assertIn("interaction_mode", first)
        self.assertIn("latest_user_message_at", first)

    def test_threads_compact_lists_empty_projects_without_raw_shell(self) -> None:
        shell = self._compact_shell()
        shell["projects"].append(
            {
                "id": "project-empty",
                "title": "Empty fixture",
                "workspaceRoot": "/work/empty",
                "defaultModelSelection": copy.deepcopy(
                    shell["projects"][0]["defaultModelSelection"]
                ),
            }
        )
        with LoopbackServer([Response(value=shell)]) as server:
            result = invoke(server, tools.t3_threads, {"limit": 50})

        self.assertTrue(result["ok"])
        self.assertEqual(result["project_count"], 3)
        self.assertFalse(result["projects_truncated"])
        self.assertIn(
            {
                "id": "project-empty",
                "title": "Empty fixture",
                "workspace": "/work/empty",
            },
            result["projects"],
        )

    def test_compact_uses_canonical_background_liveness(self) -> None:
        for background_liveness in ("working", "monitoring"):
            with self.subTest(background_liveness=background_liveness):
                shell = self._compact_shell()
                thread = shell["threads"][0]
                thread["title"] = "Canonical background fixture"
                current_session = session(status="ready")
                current_session["threadId"] = thread["id"]
                thread["session"] = current_session
                thread["latestTurn"] = latest_turn(state="completed")
                thread["backgroundLiveness"] = background_liveness
                with LoopbackServer([Response(value=shell)]) as server:
                    result = invoke(
                        server,
                        tools.t3_threads,
                        {"title_query": thread["title"], "require_one": True},
                    )

                projected = result["threads"][0]
                self.assertEqual(projected["lifecycle"], "running")
                self.assertEqual(projected["liveness"], background_liveness)

    def test_background_liveness_precedes_terminal_session_state(self) -> None:
        for status in ("error", "stopped"):
            with self.subTest(status=status):
                shell = self._compact_shell()
                thread = shell["threads"][0]
                thread["title"] = f"Terminal background {status}"
                current_session = session(status=status)
                current_session["threadId"] = thread["id"]
                thread["session"] = current_session
                thread["latestTurn"] = latest_turn(state="error")
                thread["backgroundLiveness"] = "working"
                thread["hasPendingApprovals"] = False
                thread["hasPendingUserInput"] = False
                with LoopbackServer([Response(value=shell)]) as server:
                    result = invoke(
                        server,
                        tools.t3_threads,
                        {"title_query": thread["title"], "require_one": True},
                    )

                projected = result["threads"][0]
                self.assertEqual(projected["lifecycle"], "running")
                self.assertEqual(projected["liveness"], "working")

    def test_threads_explicit_raw_is_legacy_exact_and_require_one_fails_closed(self) -> None:
        shell = self._compact_shell()
        with LoopbackServer([Response(value=shell)]) as server:
            raw = invoke(server, tools.t3_threads, {"view": "raw"})
        self.assertEqual(raw, {"ok": True, "shell": shell})

        for title_query in ("does not exist", "needle"):
            with self.subTest(title_query=title_query):
                with LoopbackServer([Response(value=shell)]) as server:
                    failed = invoke(
                        server,
                        tools.t3_threads,
                        {
                            "view": "compact",
                            "project": "project-alpha",
                            "title_query": title_query,
                            "require_one": True,
                            "limit": 50,
                        },
                    )
                self.assertEqual(failed["error_code"], "conflict")
                self.assertEqual([item["method"] for item in server.requests], ["GET"])

    def test_threads_project_workspace_and_blocked_lifecycle_aliases_are_explicit(self) -> None:
        shell = self._compact_shell()
        cases = (
            ({"project": "mission alpha", "title_query": "blocked"}, "thread-blocked"),
            ({"project": "alpha", "title_query": "blocked"}, "thread-blocked"),
            ({"workspace": "alpha", "title_query": "blocked"}, "thread-blocked"),
            ({"lifecycle": "blocked"}, "thread-blocked"),
        )
        for filters, expected_id in cases:
            with self.subTest(filters=filters):
                with LoopbackServer([Response(value=shell)]) as server:
                    result = invoke(
                        server,
                        tools.t3_threads,
                        {"view": "compact", "require_one": True, **filters},
                    )
                self.assertTrue(result["ok"])
                self.assertEqual(result["threads"][0]["id"], expected_id)
                self.assertEqual(len(result["threads"]), 1)

    def test_thread_read_defaults_to_bounded_material_projection(self) -> None:
        shell = self._compact_shell()
        plan_old = proposed_plan(plan_id="plan-old", plan_markdown="Old")
        plan_old["updatedAt"] = "2026-08-21T11:00:00Z"
        plan_new = proposed_plan(plan_id="plan-new", plan_markdown="Ship it")
        plan_new["updatedAt"] = "2026-08-21T12:05:00Z"
        implemented = proposed_plan(
            plan_id="plan-done",
            implemented_at="2026-08-21T12:04:00Z",
            implementation_thread_id="thread-1",
        )
        running_session = session(status="running", active_turn_id="turn-1")
        running_session["lastError"] = "provider needs attention"
        detail = with_projection_fields(
            detail_snapshot(
                sequence=52,
                messages=[
                    projected_message(
                        message_id="assistant-old",
                        role="assistant",
                        text="old answer",
                        turn_id="turn-0",
                        created_at="2026-08-21T11:00:00Z",
                    ),
                    projected_message(
                        message_id="user-new",
                        role="user",
                        text="new request",
                        turn_id="turn-1",
                        created_at="2026-08-21T12:01:00Z",
                    ),
                    projected_message(
                        message_id="assistant-new",
                        role="assistant",
                        text="working update",
                        turn_id="turn-1",
                        streaming=True,
                        created_at="2026-08-21T12:02:00Z",
                        updated_at="2026-08-21T12:03:00Z",
                    ),
                ],
                turn=latest_turn(turn_id="turn-1", state="running"),
                current_session=running_session,
                proposed_plans=[plan_old, implemented, plan_new],
            ),
            thread_sequence=17,
        )
        detail["thread"]["activities"] = [
            activity(
                activity_id="approval-resolved-first",
                kind="approval.resolved",
                payload={"requestId": "approval-closed"},
                sequence=2,
                created_at="2026-08-21T12:00:02Z",
            ),
            activity(
                activity_id="approval-requested-first",
                kind="approval.requested",
                payload={
                    "requestId": "approval-closed",
                    "requestKind": "file-read",
                    "requestType": "file_read_approval",
                },
                sequence=1,
                created_at="2026-08-21T12:00:01Z",
            ),
            activity(
                activity_id="approval-open",
                kind="approval.requested",
                payload={
                    "requestId": "approval-open",
                    "requestKind": "command",
                    "requestType": "exec_command_approval",
                    "detail": "Run the focused tests",
                },
                sequence=4,
                created_at="2026-08-21T12:02:00Z",
            ),
            activity(
                activity_id="input-open",
                kind="user-input.requested",
                payload={
                    "requestId": "input-open",
                    "questions": [
                        {
                            "id": "scope",
                            "header": "Scope",
                            "question": "Which scope?",
                            "options": [
                                {"label": "Focused", "description": "Only focused tests"}
                            ],
                            "multiSelect": False,
                        }
                    ],
                },
                sequence=5,
                created_at="2026-08-21T12:02:01Z",
            ),
            activity(
                activity_id="plan-progress",
                kind="turn.plan.updated",
                payload={
                    "plan": [{"step": "Implement", "status": "inProgress"}],
                    "explanation": "Working",
                },
                sequence=6,
                created_at="2026-08-21T12:02:02Z",
            ),
        ]
        detail["thread"]["planProgress"] = {
            "step": "Implement",
            "completedSteps": 1,
            "totalSteps": 3,
        }
        detail["thread"]["projectId"] = "project-alpha"
        responder = projection_responder(shell, detail)
        with LoopbackServer([responder, responder]) as server:
            result = invoke(
                server,
                tools.t3_thread_read,
                {"thread_id": "thread-1"},
            )

        self.assertTrue(result["ok"])
        self.assertIn("view", result)
        self.assertEqual(result["view"], "material")
        self.assertEqual(result["snapshot_sequence"], 52)
        self.assertEqual(result["thread_sequence"], 17)
        self.assertEqual(result["page"]["has_more"], False)
        projected = result["thread"]
        self.assertEqual(
            projected["project"],
            {"id": "project-alpha", "title": "Mission Alpha", "workspace": "/work/alpha"},
        )
        self.assertEqual(projected["lifecycle"], "blocked")
        self.assertEqual(projected["liveness"], "action_required")
        self.assertEqual(projected["runtime_mode"], "approval-required")
        self.assertEqual(projected["interaction_mode"], "default")
        self.assertEqual(projected["session"]["active_turn_id"], "turn-1")
        self.assertEqual(projected["last_error"], "provider needs attention")
        self.assertEqual(projected["latest_user_update"]["id"], "user-new")
        self.assertEqual(projected["latest_assistant_update"]["id"], "assistant-new")
        self.assertTrue(projected["latest_assistant_update"]["streaming"])
        self.assertEqual(projected["actionable_plan"]["id"], "plan-new")
        self.assertEqual(
            projected["plan_progress"],
            {"step": "Implement", "completedSteps": 1, "totalSteps": 3},
        )
        self.assertEqual(
            [(request["kind"], request["request_id"]) for request in projected["pending_requests"]],
            [("approval", "approval-open"), ("user_input", "input-open")],
        )
        self.assertEqual(projected["settled_at"], None)
        self.assertEqual(projected["latest_user_message_at"], "2026-08-21T12:01:00Z")
        self.assertEqual(projected["created_at"], "2026-08-21T12:00:00Z")
        self.assertEqual(projected["updated_at"], "2026-08-21T12:00:00Z")
        self.assertEqual(
            sorted(request["method"] for request in server.requests),
            ["GET", "GET"],
        )

    def test_material_uses_canonical_plan_progress_and_respects_clear(self) -> None:
        shell = self._compact_shell()
        canonical = {
            "step": "Canonical background work",
            "completedSteps": 1,
            "totalSteps": 3,
        }
        historical = activity(
            activity_id="stale-plan-progress",
            kind="turn.plan.updated",
            payload={"step": "Stale historical work", "status": "completed"},
            sequence=5,
            created_at="2026-08-21T12:00:05Z",
        )
        for plan_progress, expected in ((canonical, canonical), (None, None)):
            with self.subTest(plan_progress=plan_progress):
                detail = with_projection_fields(detail_snapshot(sequence=55))
                detail["thread"]["projectId"] = "project-alpha"
                detail["thread"]["activities"] = [historical]
                detail["thread"]["planProgress"] = plan_progress
                responder = projection_responder(shell, detail)
                with LoopbackServer([responder, responder]) as server:
                    result = invoke(
                        server,
                        tools.t3_thread_read,
                        {"thread_id": "thread-1"},
                    )

                self.assertEqual(result["thread"]["plan_progress"], expected)
                self.assertFalse(result["thread"]["plan_progress_truncated"])

    def test_material_projection_truncates_model_facing_text_with_metadata(self) -> None:
        oversized = "x" * 20_000
        current_session = session(status="error")
        current_session["lastError"] = oversized
        detail = detail_snapshot(
            sequence=12,
            messages=[
                message(
                    message_id="assistant-large",
                    text=oversized,
                    turn_id="turn-1",
                )
            ],
            turn=latest_turn(),
            current_session=current_session,
            proposed_plans=[proposed_plan(plan_markdown=oversized)],
        )
        detail["thread"]["messages"][0]["role"] = "assistant"
        with LoopbackServer(
            [Response(value=shell_snapshot(sequence=12)), Response(value=detail)]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_read,
                {"thread_id": "thread-1"},
            )

        projected = result["thread"]
        assistant = projected["latest_assistant_update"]
        self.assertLessEqual(len(assistant["text"]), 8_192)
        self.assertTrue(assistant["text_truncated"])
        self.assertEqual(assistant["text_original_utf16_units"], 20_000)
        self.assertLessEqual(len(projected["actionable_plan"]["plan_markdown"]), 16_384)
        self.assertTrue(projected["actionable_plan"]["plan_markdown_truncated"])
        self.assertLessEqual(len(projected["last_error"]), 8_192)
        self.assertTrue(projected["last_error_truncated"])

    def test_compact_and_material_share_one_bounded_projection_budget(self) -> None:
        shell = self._compact_shell()
        template = shell["threads"][0]
        large_options = [
            {"id": f"option-{index}", "value": "x" * 512}
            for index in range(64)
        ]
        shell["threads"] = []
        for index in range(50):
            thread = copy.deepcopy(template)
            thread["id"] = f"thread-large-{index}"
            thread["title"] = f"Large {index} " + "t" * 2_000
            thread["modelSelection"]["options"] = copy.deepcopy(large_options)
            thread["session"]["threadId"] = thread["id"]
            shell["threads"].append(thread)

        with LoopbackServer([Response(value=shell)]) as server:
            compact = invoke(server, tools.t3_threads, {"limit": 50})
        self.assertTrue(compact["ok"])
        self.assertTrue(compact["projection_truncated"])
        self.assertLessEqual(
            len(json.dumps(compact, ensure_ascii=False).encode("utf-8")),
            tools.MAX_MODEL_PROJECTION_UTF8_BYTES,
        )

        detail = with_projection_fields(detail_snapshot(sequence=88))
        detail["thread"]["activities"] = [
            activity(
                activity_id=f"approval-{index}",
                kind="approval.requested",
                payload={
                    "requestId": f"request-{index}",
                    "requestKind": "command",
                    "requestType": "exec_command_approval",
                    "detail": "d" * 20_000,
                },
                sequence=index + 1,
                created_at=f"2026-08-21T12:00:{index:02d}Z",
                turn_id="turn-1",
            )
            for index in range(32)
        ]
        detail["thread"]["planProgress"] = {
            "step": "p" * 20_000,
            "completedSteps": 1,
            "totalSteps": 32,
        }
        with LoopbackServer(
            [Response(value=shell_snapshot(sequence=88)), Response(value=detail)]
        ) as server:
            material = invoke(
                server,
                tools.t3_thread_read,
                {"thread_id": "thread-1"},
            )
        self.assertTrue(material["ok"])
        self.assertTrue(material["projection_truncated"])
        self.assertLessEqual(
            len(json.dumps(material, ensure_ascii=False).encode("utf-8")),
            tools.MAX_MODEL_PROJECTION_UTF8_BYTES,
        )

    def test_material_read_drops_request_resolved_by_later_unsequenced_activity(self) -> None:
        requested = activity(
            activity_id="input-requested",
            kind="user-input.requested",
            payload={"requestId": "input-1", "questions": []},
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        resolved = activity(
            activity_id="input-resolved",
            kind="user-input.resolved",
            payload={"requestId": "input-1", "answers": {"confirmed": True}},
            sequence=5,
            created_at="2026-08-21T12:00:01Z",
        )
        resolved.pop("sequence")
        detail = with_projection_fields(
            detail_snapshot(sequence=5, current_session=session(status="ready")),
            thread_sequence=5,
        )
        detail["thread"]["activities"] = [requested, resolved]
        shell = shell_snapshot(sequence=5)
        responder = projection_responder(shell, detail)

        with LoopbackServer([responder, responder]) as server:
            result = invoke(
                server,
                tools.t3_thread_read,
                {"thread_id": "thread-1"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["thread"]["pending_requests"], [])
        self.assertEqual(result["thread"]["lifecycle"], "ready")
        self.assertEqual(result["thread"]["liveness"], "settled")

    def test_material_read_keeps_request_when_later_resolution_is_for_other_turn(
        self,
    ) -> None:
        requested = activity(
            activity_id="input-requested",
            kind="user-input.requested",
            payload={"requestId": "reused-id", "questions": []},
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
            turn_id="turn-new",
        )
        other_turn_resolution = activity(
            activity_id="input-resolved-old-turn",
            kind="user-input.resolved",
            payload={"requestId": "reused-id", "answers": {"scope": "Old"}},
            sequence=5,
            created_at="2026-08-21T12:00:01Z",
            turn_id="turn-old",
        )
        detail = with_projection_fields(
            detail_snapshot(sequence=5, current_session=session(status="running")),
            thread_sequence=5,
        )
        detail["thread"]["activities"] = [requested, other_turn_resolution]
        shell = shell_snapshot(sequence=5)
        responder = projection_responder(shell, detail)

        with LoopbackServer([responder, responder]) as server:
            result = invoke(
                server,
                tools.t3_thread_read,
                {"thread_id": "thread-1"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            [item["request_id"] for item in result["thread"]["pending_requests"]],
            ["reused-id"],
        )
        self.assertEqual(result["thread"]["liveness"], "action_required")

    def test_material_read_closes_one_request_on_unscoped_stale_provider_failure(
        self,
    ) -> None:
        requested = activity(
            activity_id="input-requested",
            kind="user-input.requested",
            payload={"requestId": "input-1", "questions": []},
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
            turn_id="turn-1",
        )
        failed = activity(
            activity_id="input-response-failed",
            kind="provider.user-input.respond.failed",
            payload={"requestId": "input-1", "reason": "stale pending request"},
            sequence=5,
            created_at="2026-08-21T12:00:01Z",
            turn_id=None,
        )
        detail = with_projection_fields(
            detail_snapshot(sequence=5, current_session=session(status="ready")),
            thread_sequence=5,
        )
        detail["thread"]["activities"] = [requested, failed]
        shell = shell_snapshot(sequence=5)
        responder = projection_responder(shell, detail)

        with LoopbackServer([responder, responder]) as server:
            result = invoke(
                server,
                tools.t3_thread_read,
                {"thread_id": "thread-1"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["thread"]["pending_requests"], [])
        self.assertEqual(result["thread"]["liveness"], "settled")

    def test_material_read_keeps_reused_requests_on_unscoped_provider_failure(
        self,
    ) -> None:
        requests = [
            activity(
                activity_id=f"input-requested-{turn_id}",
                kind="user-input.requested",
                payload={"requestId": "reused-id", "questions": []},
                sequence=sequence,
                created_at=f"2026-08-21T12:00:0{sequence}Z",
                turn_id=turn_id,
            )
            for sequence, turn_id in ((1, "turn-1"), (2, "turn-2"))
        ]
        failed = activity(
            activity_id="input-response-failed",
            kind="provider.user-input.respond.failed",
            payload={"requestId": "reused-id", "reason": "unknown pending request"},
            sequence=3,
            created_at="2026-08-21T12:00:03Z",
            turn_id=None,
        )
        detail = with_projection_fields(
            detail_snapshot(sequence=3, current_session=session(status="running")),
            thread_sequence=3,
        )
        detail["thread"]["activities"] = [*requests, failed]
        shell = shell_snapshot(sequence=3)
        responder = projection_responder(shell, detail)

        with LoopbackServer([responder, responder]) as server:
            result = invoke(
                server,
                tools.t3_thread_read,
                {"thread_id": "thread-1"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            [item["request_id"] for item in result["thread"]["pending_requests"]],
            ["reused-id", "reused-id"],
        )
        self.assertEqual(result["thread"]["liveness"], "action_required")

    def test_thread_read_explicit_raw_preserves_detail_contract(self) -> None:
        detail = with_projection_fields(detail_snapshot(sequence=12), thread_sequence=7)
        with LoopbackServer([Response(value=detail)]) as server:
            raw = invoke(
                server,
                tools.t3_thread_read,
                {"thread_id": "thread-1", "view": "raw"},
            )
        self.assertEqual(raw, {"ok": True, "detail": detail})


class PublicArgumentPreflightTests(unittest.TestCase):
    def test_cross_field_runtime_invariants_reject_before_http(self) -> None:
        invalid_cases = (
            (
                "t3_thread_read",
                {"thread_id": "thread-1", "before_cursor": "older"},
            ),
            (
                "t3_thread_create",
                {
                    "project_id": "project-1",
                    "title": "Missing model",
                    "instance_id": "codex-main",
                },
            ),
            (
                "t3_thread_send",
                {
                    "thread_id": "thread-1",
                    "message": "Missing model",
                    "instance_id": "codex-main",
                },
            ),
            (
                "t3_thread_send",
                {
                    "thread_id": "thread-1",
                    "message": "Missing instance",
                    "model": "gpt-current",
                },
            ),
            (
                "t3_thread_send",
                {
                    "thread_id": "thread-1",
                    "message": "Detached options",
                    "model_options": [
                        {"id": "reasoning_effort", "value": "ultra"}
                    ],
                },
            ),
            (
                "t3_thread_create",
                {
                    "project_id": "project-1",
                    "title": "Missing instance",
                    "model": "gpt-current",
                },
            ),
            (
                "t3_thread_create",
                {
                    "project_id": "project-1",
                    "title": "Detached options",
                    "model_options": [
                        {"id": "reasoning_effort", "value": "ultra"}
                    ],
                },
            ),
        )
        with LoopbackServer([]) as server:
            for tool_name, args in invalid_cases:
                with self.subTest(tool=tool_name, fields=tuple(sorted(args))):
                    result = invoke(server, tools.OPERATIONS[tool_name], args)
                    self.assertEqual(result["error_code"], "invalid_input")
                    self.assertEqual(server.requests, [])

    def test_active_credential_in_every_public_string_path_makes_no_http_request(self) -> None:
        base_args = {
            "t3_threads": {"view": "compact"},
            "t3_thread_read": {
                "thread_id": "thread-1",
                "view": "material",
                "turn_limit": 20,
                "before_cursor": "older",
            },
            "t3_thread_create": {
                "project_id": "project-1",
                "title": "Thread",
                "instance_id": "codex-main",
                "model": "gpt-current",
                "model_options": [{"id": "reasoning_effort", "value": "high"}],
                "runtime_mode": "approval-required",
                "interaction_mode": "default",
                "initial_message": "Start",
                "branch": "feature/test",
                "worktree_path": "/work/test",
            },
            "t3_thread_send": {
                "thread_id": "thread-1",
                "message": "Continue",
                "instance_id": "codex-main",
                "model": "gpt-current",
                "model_options": [
                    {"id": "reasoning_effort", "value": "high"}
                ],
                "busy_policy": "reject",
            },
            "t3_thread_set_mode": {
                "thread_id": "thread-1",
                "runtime_mode": "approval-required",
            },
            "t3_thread_implement_plan": {
                "thread_id": "thread-1",
                "plan_id": "plan-1",
            },
            "t3_turn_interrupt": {"thread_id": "thread-1"},
            "t3_session_stop": {"thread_id": "thread-1"},
            "t3_thread_wait": {
                "thread_id": "thread-1",
                "until": "terminal",
                "timeout_seconds": 0,
            },
            "t3_thread_respond": {
                "thread_id": "thread-1",
                "request_id": "approval-1",
                "turn_id": "turn-1",
                "decision": "accept",
            },
            "t3_thread_settle": {"thread_id": "thread-1"},
        }
        public_string_paths = {
            (tool_name, (field_name,))
            for tool_name, schema in schemas.SCHEMAS.items()
            if tool_name in base_args
            for field_name, field_schema in schema["parameters"][
                "properties"
            ].items()
            if field_schema.get("type") == "string"
        }
        public_string_paths.update(
            {
                ("t3_thread_create", ("model_options", "id")),
                ("t3_thread_create", ("model_options", "value")),
                ("t3_thread_send", ("model_options", "id")),
                ("t3_thread_send", ("model_options", "value")),
            }
        )
        if "t3_thread_respond" in schemas.SCHEMAS:
            public_string_paths.add(
                ("t3_thread_respond", ("answers", "question-1"))
            )
        mode_credentials = {
            ("t3_thread_create", ("runtime_mode",)): "auto",
            ("t3_thread_create", ("interaction_mode",)): "plan",
            ("t3_thread_set_mode", ("runtime_mode",)): "auto",
            ("t3_thread_set_mode", ("interaction_mode",)): "plan",
            ("t3_threads", ("view",)): "compact",
            ("t3_threads", ("lifecycle",)): "running",
            ("t3_thread_read", ("view",)): "material",
            ("t3_thread_send", ("busy_policy",)): "queue",
            ("t3_thread_wait", ("until",)): "terminal",
            ("t3_thread_respond", ("decision",)): "accept",
        }
        self.assertEqual(len(public_string_paths), 41)

        with LoopbackServer([]) as server:
            for tool_name, field_path in sorted(public_string_paths):
                with self.subTest(tool=tool_name, path=".".join(field_path)):
                    credential = mode_credentials.get(
                        (tool_name, field_path), server.token
                    )
                    payload = copy.deepcopy(base_args[tool_name])
                    if (
                        tool_name == "t3_thread_set_mode"
                        and field_path == ("interaction_mode",)
                    ):
                        payload.pop("runtime_mode")
                    if field_path[0] == "model_options":
                        payload["model_options"][0][field_path[1]] = credential
                    elif field_path[0] == "answers":
                        payload.pop("decision")
                        payload["answers"] = {field_path[1]: credential}
                    else:
                        payload[field_path[0]] = credential
                    request_count = len(server.requests)
                    result = invoke(
                        server,
                        tools.OPERATIONS[tool_name],
                        payload,
                        credential=credential,
                    )
                    encoded = json.dumps(result)
                    self.assertEqual(result["error_code"], "invalid_input")
                    self.assertFalse(
                        credential in encoded,
                        "The sanitized public-operation error reflected the active credential.",
                    )
                    self.assertEqual(len(server.requests) - request_count, 0)
                    self.assertEqual(
                        sum(
                            request["method"] == "POST"
                            for request in server.requests[request_count:]
                        ),
                        0,
                    )

    def test_non_null_string_schema_and_handler_parity_makes_no_http_request(self) -> None:
        base_args = {
            "t3_threads": {},
            "t3_thread_read": {"thread_id": "thread-1", "turn_limit": 20},
            "t3_thread_create": {"project_id": "project-1", "title": "Thread"},
            "t3_thread_send": {"thread_id": "thread-1", "message": "Continue"},
            "t3_thread_set_mode": {
                "thread_id": "thread-1",
                "runtime_mode": "approval-required",
            },
            "t3_thread_implement_plan": {
                "thread_id": "thread-1",
                "plan_id": "plan-1",
            },
            "t3_turn_interrupt": {"thread_id": "thread-1"},
            "t3_session_stop": {"thread_id": "thread-1"},
            "t3_thread_wait": {"thread_id": "thread-1"},
            "t3_thread_respond": {
                "thread_id": "thread-1",
                "request_id": "approval-1",
                "turn_id": "turn-1",
                "decision": "accept",
            },
            "t3_thread_settle": {"thread_id": "thread-1"},
        }
        schema_matrix = {
            (tool_name, field_name)
            for tool_name, schema in schemas.SCHEMAS.items()
            for field_name, field_schema in schema["parameters"]["properties"].items()
            if field_schema.get("type") == "string"
        }

        with LoopbackServer([]) as server:
            exercised: set[tuple[str, str]] = set()
            for tool_name, field_name in sorted(schema_matrix):
                payload = dict(base_args[tool_name])
                if tool_name == "t3_thread_create" and field_name in {
                    "instance_id",
                    "model",
                }:
                    payload.update(
                        {"instance_id": "codex-main", "model": "gpt-current"}
                    )
                if tool_name == "t3_thread_send" and field_name in {
                    "instance_id",
                    "model",
                }:
                    payload.update(
                        {"instance_id": "codex-main", "model": "gpt-current"}
                    )
                if (
                    tool_name == "t3_thread_set_mode"
                    and field_name == "interaction_mode"
                ):
                    payload.pop("runtime_mode")
                if tool_name == "t3_thread_respond" and field_name == "decision":
                    payload["decision"] = None
                payload[field_name] = None

                with self.subTest(tool=tool_name, field=field_name):
                    request_count = len(server.requests)
                    result = invoke(server, tools.OPERATIONS[tool_name], payload)
                    self.assertEqual(result["error_code"], "invalid_input")
                    self.assertEqual(len(server.requests) - request_count, 0)
                    exercised.add((tool_name, field_name))

            self.assertEqual(exercised, schema_matrix)

        with LoopbackServer([Response(value=detail_snapshot())]) as server:
            omitted = invoke(
                server,
                tools.t3_thread_read,
                {"thread_id": "thread-1", "view": "raw"},
            )
            self.assertTrue(omitted["ok"])
            self.assertEqual(
                server.requests[0]["path"],
                "/api/orchestration/threads/thread-1?turnLimit=20",
            )

    def test_respond_rejects_non_javascript_safe_integers_without_http(self) -> None:
        unsafe = 2**53
        with LoopbackServer([]) as server:
            for answer in (unsafe, -unsafe):
                with self.subTest(answer_sign=1 if answer > 0 else -1):
                    request_count = len(server.requests)
                    result = invoke(
                        server,
                        tools.OPERATIONS["t3_thread_respond"],
                        {
                            "thread_id": "thread-1",
                            "request_id": "input-1",
                            "answers": {"count": answer},
                        },
                    )
                    self.assertEqual(result["error_code"], "invalid_input")
                    self.assertEqual(len(server.requests), request_count)

    def test_respond_rejects_invalid_unicode_in_answer_arrays_without_http(self) -> None:
        with LoopbackServer([]) as server:
            result = invoke(
                server,
                tools.OPERATIONS["t3_thread_respond"],
                {
                    "thread_id": "thread-1",
                    "request_id": "input-1",
                    "answers": {"scope": ["\ud800"]},
                },
            )

        self.assertEqual(result["error_code"], "invalid_input")
        self.assertEqual(server.requests, [])

    def test_respond_rejects_aggregate_answer_budget_before_http(self) -> None:
        with LoopbackServer([]) as server:
            result = invoke(
                server,
                tools.OPERATIONS["t3_thread_respond"],
                {
                    "thread_id": "thread-1",
                    "request_id": "input-1",
                    "turn_id": "turn-1",
                    "answers": {"scope": ["x" * 120_000 for _ in range(5)]},
                },
            )

        self.assertEqual(result["error_code"], "invalid_input")
        self.assertEqual(server.requests, [])


class AgentFacingSendToolTests(unittest.TestCase):
    def test_send_busy_policy_defaults_reject_and_never_posts(self) -> None:
        running = detail_snapshot(
            sequence=3,
            turn=latest_turn(turn_id="turn-active", state="running"),
            current_session=session(status="running", active_turn_id="turn-active"),
        )
        with LoopbackServer(
            [
                Response(value=running),
                Response(status=400, value={"code": "invalid_request"}),
            ]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_send,
                {"thread_id": "thread-1", "message": "Do not queue implicitly"},
            )
        self.assertEqual(result["error_code"], "conflict")
        self.assertEqual([request["method"] for request in server.requests], ["GET"])

    def test_send_reject_policy_never_dispatches_even_after_idle_observation(self) -> None:
        idle = detail_snapshot(
            sequence=3,
            turn=latest_turn(turn_id="turn-old", state="completed"),
            current_session=session(status="ready"),
        )
        with LoopbackServer(
            [Response(value=idle), Response(status=400, value={"code": "must-not-post"})]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_send,
                {
                    "thread_id": "thread-1",
                    "message": "Only send with explicit start-or-queue acknowledgement",
                    "busy_policy": "reject",
                },
            )

        self.assertEqual(result["error_code"], "conflict")
        self.assertIn("busy_policy queue", result["error"])
        self.assertEqual([request["method"] for request in server.requests], ["GET"])

    def test_send_queue_discloses_full_access_and_concurrent_queue_semantics(self) -> None:
        commands: list[dict] = []
        before = detail_snapshot(
            sequence=3,
            turn=latest_turn(turn_id="turn-old", state="completed"),
            current_session=session(status="ready"),
        )
        before["thread"]["runtimeMode"] = "full-access"

        def dispatch(request: dict) -> Response:
            commands.append(json.loads(request["body"]))
            return Response(value={"sequence": 4})

        def queued(_request: dict) -> Response:
            command = commands[0]
            after = detail_snapshot(
                sequence=4,
                messages=[
                    projected_message(
                        message_id=command["message"]["messageId"],
                        role="user",
                        text=command["message"]["text"],
                        turn_id=None,
                        created_at=command["createdAt"],
                    )
                ],
                turn=latest_turn(turn_id="turn-raced", state="running"),
                current_session=session(status="running", active_turn_id="turn-raced"),
            )
            after["thread"]["runtimeMode"] = "full-access"
            return Response(value=after)

        with LoopbackServer([Response(value=before), dispatch, queued]) as server:
            result = invoke(
                server,
                tools.t3_thread_send,
                {
                    "thread_id": "thread-1",
                    "message": "Explicitly allow start or queue",
                    "busy_policy": "queue",
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["command_state"], "queued")
        self.assertIn("may start immediately or queue", result["queue_semantics"])
        self.assertIn("modify or delete files without approval", result["warning"])

    def test_send_queue_accepts_only_exact_persisted_unlinked_message(self) -> None:
        commands: list[dict] = []
        running_session = session(status="running", active_turn_id="turn-active")
        before = with_projection_fields(
            detail_snapshot(
                sequence=3,
                turn=latest_turn(turn_id="turn-active", state="running"),
                current_session=running_session,
            ),
            thread_sequence=8,
        )

        def dispatch(request: dict) -> Response:
            commands.append(json.loads(request["body"]))
            return Response(value={"sequence": 9})

        def queued_readback(request: dict) -> Response:
            command = commands[0]
            persisted = projected_message(
                message_id=command["message"]["messageId"],
                role="user",
                text=command["message"]["text"],
                turn_id=None,
                created_at=command["createdAt"],
            )
            after = with_projection_fields(
                detail_snapshot(
                    sequence=9,
                    messages=[persisted],
                    turn=latest_turn(turn_id="turn-active", state="running"),
                    current_session=running_session,
                ),
                thread_sequence=9,
            )
            return Response(value=after)

        with LoopbackServer(
            [Response(value=before), dispatch, queued_readback]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_send,
                {
                    "thread_id": "thread-1",
                    "message": " queue this exactly ",
                    "busy_policy": "queue",
                },
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["accepted"])
        self.assertEqual(result["verification"], "verified")
        self.assertEqual(result["command_state"], "queued")
        self.assertEqual(result["persisted_message"]["id"], result["message_id"])
        self.assertEqual(result["persisted_message"]["text"], "queue this exactly")
        self.assertIsNone(result["persisted_message"]["turn_id"])
        self.assertEqual(result["latest_turn"]["turnId"], "turn-active")
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["type"], "thread.turn.start")
        self.assertNotIn("busyPolicy", commands[0])

    def test_send_reports_started_completed_error_and_blocked_observed_states(self) -> None:
        cases = (
            ("started", "running", "running", []),
            ("completed", "completed", "ready", []),
            ("error", "error", "error", []),
            (
                "blocked",
                "running",
                "running",
                [
                    activity(
                        activity_id="approval-open",
                        kind="approval.requested",
                        payload={
                            "requestId": "approval-open",
                            "requestKind": "command",
                            "requestType": "exec_command_approval",
                        },
                        sequence=7,
                        created_at="2026-08-21T12:00:01Z",
                        turn_id="turn-new",
                    )
                ],
            ),
        )
        for command_state, turn_state, session_status, activities in cases:
            with self.subTest(command_state=command_state):
                commands: list[dict] = []
                before = with_projection_fields(
                    detail_snapshot(
                        sequence=5,
                        current_session=session(status="ready"),
                    ),
                    thread_sequence=5,
                )

                def dispatch(request: dict) -> Response:
                    commands.append(json.loads(request["body"]))
                    return Response(value={"sequence": 6})

                def readback(request: dict) -> Response:
                    command = commands[0]
                    current_session = session(
                        status=session_status,
                        active_turn_id=("turn-new" if session_status == "running" else None),
                    )
                    after = with_projection_fields(
                        detail_snapshot(
                            sequence=6,
                            messages=[
                                projected_message(
                                    message_id=command["message"]["messageId"],
                                    role="user",
                                    text=command["message"]["text"],
                                    turn_id="turn-new",
                                    created_at=command["createdAt"],
                                )
                            ],
                            turn=latest_turn(turn_id="turn-new", state=turn_state),
                            current_session=current_session,
                        ),
                        thread_sequence=6,
                    )
                    after["thread"]["activities"] = copy.deepcopy(activities)
                    return Response(value=after)

                with LoopbackServer(
                    [Response(value=before), dispatch, readback]
                ) as server:
                    result = invoke(
                        server,
                        tools.t3_thread_send,
                        {
                            "thread_id": "thread-1",
                            "message": "Run",
                            "busy_policy": "queue",
                        },
                    )
                self.assertTrue(result["ok"])
                self.assertIn("command_state", result)
                self.assertEqual(result["command_state"], command_state)
                self.assertEqual(result["persisted_message"]["turn_id"], "turn-new")


class ModelSwitchSendToolTests(unittest.TestCase):
    SOURCE_SELECTION = {
        "instanceId": "codex-secondary",
        "model": "gpt-current",
        "options": [
            {"id": "reasoning_effort", "value": "high"},
            {"id": "web_search", "value": True},
        ],
    }
    TARGET_PAIR = {"instanceId": "codex", "model": "gpt-5.6"}

    @staticmethod
    def _captured_dispatch_at(store: list[dict], sequence: int):
        def responder(request: dict) -> Response:
            store.append(json.loads(request["body"]))
            return Response(value={"sequence": sequence})

        return responder

    @staticmethod
    def _before(
        *,
        sequence: int = 10,
        current_session: dict | None = None,
    ) -> dict:
        resolved_session = copy.deepcopy(
            current_session
            or session(status="ready", provider_instance_id="codex-secondary")
        )
        if "providerInstanceId" in resolved_session:
            resolved_session["providerInstanceId"] = "codex-secondary"
        detail = detail_snapshot(
            sequence=sequence,
            turn=latest_turn(turn_id="turn-old", state="completed"),
            current_session=resolved_session,
        )
        detail["thread"]["modelSelection"] = copy.deepcopy(
            ModelSwitchSendToolTests.SOURCE_SELECTION
        )
        return detail

    def _successful_switch(
        self,
        *,
        args: dict | None = None,
        before: dict | None = None,
        metadata_sequence: int = 11,
        turn_sequence: int = 12,
        final_hook=None,
    ) -> tuple[dict, list[dict], list[dict]]:
        commands: list[dict] = []
        initial = copy.deepcopy(before or self._before())

        def dispatch(request: dict) -> Response:
            commands.append(json.loads(request["body"]))
            sequence = metadata_sequence if len(commands) == 1 else turn_sequence
            return Response(value={"sequence": sequence})

        def metadata_readback(_request: dict) -> Response:
            detail = copy.deepcopy(initial)
            detail["snapshotSequence"] = metadata_sequence
            detail["page"]["snapshotSequence"] = metadata_sequence
            detail["page"]["threadSequence"] = metadata_sequence
            detail["thread"]["modelSelection"] = copy.deepcopy(
                commands[0]["modelSelection"]
            )
            return Response(value=detail)

        def ready_readback(_request: dict) -> Response:
            return metadata_readback(_request)

        def turn_readback(_request: dict) -> Response:
            command = commands[1]
            target = command["modelSelection"]
            switched_session = session(
                status="running",
                active_turn_id="turn-new",
                provider_instance_id=target["instanceId"],
            )
            switched_session["updatedAt"] = command["createdAt"]
            detail = detail_snapshot(
                sequence=max(turn_sequence, metadata_sequence),
                messages=[
                    projected_message(
                        message_id=command["message"]["messageId"],
                        role="user",
                        text=command["message"]["text"],
                        turn_id="turn-new",
                        created_at=command["createdAt"],
                    )
                ],
                turn=latest_turn(turn_id="turn-new", state="running"),
                current_session=switched_session,
            )
            detail["thread"]["modelSelection"] = copy.deepcopy(target)
            detail["thread"]["branch"] = initial["thread"]["branch"]
            detail["thread"]["worktreePath"] = initial["thread"]["worktreePath"]
            if final_hook is not None:
                final_hook(detail, command)
            return Response(value=detail)

        public_args = {
            "thread_id": "thread-1",
            "message": "Continue on the selected provider",
            "instance_id": self.TARGET_PAIR["instanceId"],
            "model": self.TARGET_PAIR["model"],
            "busy_policy": "queue",
            **(args or {}),
        }
        with LoopbackServer(
            [
                Response(value=initial),
                dispatch,
                metadata_readback,
                ready_readback,
                dispatch,
                turn_readback,
            ]
        ) as server:
            result = invoke(server, tools.t3_thread_send, public_args)
            requests = list(server.requests)
        return result, commands, requests

    def test_changed_instance_uses_exact_two_phase_commands_and_compact_success(self) -> None:
        result, commands, requests = self._successful_switch()

        self.assertTrue(result["ok"])
        self.assertEqual(
            [request["method"] for request in requests],
            ["GET", "POST", "GET", "GET", "POST", "GET"],
        )
        self.assertEqual(len(commands), 2)
        self.assertEqual(
            commands[0],
            {
                "type": "thread.meta.update",
                "commandId": commands[0]["commandId"],
                "threadId": "thread-1",
                "modelSelection": self.TARGET_PAIR,
            },
        )
        self.assertEqual(commands[1]["type"], "thread.turn.start")
        self.assertEqual(commands[1]["modelSelection"], commands[0]["modelSelection"])
        self.assertEqual(result["action"], "model_switched_and_turn_started")
        self.assertEqual(
            result["previous_model_selection"], self.SOURCE_SELECTION
        )
        self.assertEqual(result["target_model_selection"], self.TARGET_PAIR)
        self.assertEqual(result["metadata_command_id"], commands[0]["commandId"])
        self.assertEqual(result["turn_command_id"], commands[1]["commandId"])
        self.assertEqual(result["provider_instance_id"], "codex")
        self.assertEqual(result["session_state"], "running")
        self.assertEqual(result["turn_state"], "running")
        self.assertEqual(result["verification"], "verified")
        self.assertIn("TOCTOU", result["race_warning"])
        for forbidden in ("detail", "provider_session", "latest_turn", "lastError"):
            self.assertNotIn(forbidden, result)

    def test_model_options_exact_precedence_preserves_empty_and_omission(self) -> None:
        omitted, omitted_commands, _ = self._successful_switch()
        self.assertTrue(omitted["ok"])
        self.assertNotIn("options", omitted_commands[0]["modelSelection"])

        explicit_empty, empty_commands, _ = self._successful_switch(
            args={"model_options": []}
        )
        self.assertTrue(explicit_empty["ok"])
        self.assertEqual(empty_commands[0]["modelSelection"]["options"], [])

        exact_options = [
            {"id": "reasoning_effort", "value": "xhigh"},
            {"id": "web_search", "value": False},
        ]
        explicit, explicit_commands, _ = self._successful_switch(
            args={"model_options": exact_options}
        )
        self.assertTrue(explicit["ok"])
        self.assertEqual(
            explicit_commands[0]["modelSelection"]["options"], exact_options
        )

        same_pair, same_commands, _ = self._successful_switch(
            args={
                "instance_id": "codex-secondary",
                "model": "gpt-current",
                "model_options": [],
            }
        )
        self.assertTrue(same_pair["ok"])
        self.assertEqual(same_commands[0]["modelSelection"]["options"], [])

    def test_same_explicit_selection_uses_legacy_single_turn_path(self) -> None:
        commands: list[dict] = []
        before = self._before()

        def readback(_request: dict) -> Response:
            command = commands[0]
            after = detail_snapshot(
                sequence=11,
                messages=[
                    message(
                        message_id=command["message"]["messageId"],
                        text=command["message"]["text"],
                        turn_id="turn-new",
                    )
                ],
                turn=latest_turn(turn_id="turn-new", state="running"),
                current_session=session(
                    status="running",
                    active_turn_id="turn-new",
                    provider_instance_id="codex-secondary",
                ),
            )
            after["thread"]["modelSelection"] = copy.deepcopy(
                self.SOURCE_SELECTION
            )
            return Response(value=after)

        for extra in (
            {"instance_id": "codex-secondary", "model": "gpt-current"},
            {
                "instance_id": "codex-secondary",
                "model": "gpt-current",
                "model_options": copy.deepcopy(
                    self.SOURCE_SELECTION["options"]
                ),
            },
        ):
            commands.clear()
            with self.subTest(extra=extra), LoopbackServer(
                [Response(value=before), captured_dispatch(commands), readback]
            ) as server:
                result = invoke(
                    server,
                    tools.t3_thread_send,
                    {
                        "thread_id": "thread-1",
                        "message": "Legacy exact selection",
                        "busy_policy": "queue",
                        **extra,
                    },
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "new_turn_same_thread")
            self.assertEqual([command["type"] for command in commands], ["thread.turn.start"])
            self.assertEqual(
                commands[0]["modelSelection"], self.SOURCE_SELECTION
            )
            self.assertIn("detail", result)

    def test_changed_selection_busy_states_are_retryable_and_never_post(self) -> None:
        cases: list[tuple[str, dict]] = []
        cases.append(
            (
                "starting",
                self._before(current_session=session(status="starting")),
            )
        )
        cases.append(
            (
                "running",
                self._before(current_session=session(status="running")),
            )
        )
        active = self._before(
            current_session=session(status="ready", active_turn_id="turn-active")
        )
        cases.append(("active", active))
        background = self._before()
        background["thread"]["backgroundLiveness"] = "working"
        cases.append(("background", background))
        monitoring = self._before()
        monitoring["thread"]["backgroundLiveness"] = "monitoring"
        cases.append(("monitoring", monitoring))
        pending = self._before()
        pending["thread"]["hasPendingApprovals"] = True
        cases.append(("pending", pending))
        pending_input = self._before()
        pending_input["thread"]["hasPendingUserInput"] = True
        cases.append(("pending_input", pending_input))
        activity_pending = self._before()
        activity_pending["thread"]["activities"] = [
            activity(
                activity_id="approval-open",
                kind="approval.requested",
                payload={
                    "requestId": "approval-open",
                    "requestKind": "command",
                    "requestType": "exec_command_approval",
                },
                sequence=10,
                created_at="2026-08-21T12:00:01Z",
                turn_id="turn-old",
            )
        ]
        cases.append(("activity_pending", activity_pending))

        for name, before in cases:
            with self.subTest(name=name), LoopbackServer(
                [Response(value=before)]
            ) as server:
                result = invoke(
                    server,
                    tools.t3_thread_send,
                    {
                        "thread_id": "thread-1",
                        "message": "Switch only if idle",
                        "instance_id": "codex",
                        "model": "gpt-5.6",
                        "busy_policy": "queue",
                    },
                )
            self.assertEqual(result["error_code"], "model_switch_busy")
            self.assertTrue(result["retryable"])
            self.assertEqual([item["method"] for item in server.requests], ["GET"])
            self.assertIn("interrupt", result["details"]["recovery"].casefold())
            self.assertIn("wait", result["details"]["recovery"].casefold())
            self.assertIn("never stop", result["details"]["recovery"].casefold())

    def test_nonrestorable_preconditions_return_safe_new_thread_recovery(self) -> None:
        stopped_with_stale_busy_projection = self._before(
            current_session=session(status="stopped", active_turn_id="turn-stale")
        )
        stopped_with_stale_busy_projection["thread"]["backgroundLiveness"] = "working"
        stopped_with_stale_busy_projection["thread"]["hasPendingApprovals"] = True
        error_with_stale_busy_projection = self._before(
            current_session=session(status="error", active_turn_id="turn-stale")
        )
        error_with_stale_busy_projection["thread"]["backgroundLiveness"] = "working"
        error_with_stale_busy_projection["thread"]["hasPendingUserInput"] = True
        cases = (
            stopped_with_stale_busy_projection,
            error_with_stale_busy_projection,
            self._before(
                current_session=session(
                    status="ready", provider_instance_id=None
                )
            ),
        )
        for before in cases:
            with LoopbackServer([Response(value=before)]) as server:
                result = invoke(
                    server,
                    tools.t3_thread_send,
                    {
                        "thread_id": "thread-1",
                        "message": "Try unsupported switch",
                        "instance_id": "codex",
                        "model": "gpt-5.6",
                        "busy_policy": "queue",
                    },
                )
            self.assertEqual(result["error_code"], "unsupported_model_switch")
            self.assertFalse(result["retryable"])
            self.assertEqual([item["method"] for item in server.requests], ["GET"])
            recovery = result["details"]["recovery"]
            self.assertEqual(recovery["tool"], "t3_thread_create")
            self.assertEqual(recovery["arguments"]["project_id"], "project-1")
            self.assertEqual(recovery["arguments"]["instance_id"], "codex")
            self.assertEqual(recovery["arguments"]["interaction_mode"], "plan")
            self.assertNotIn("branch", recovery["arguments"])
            self.assertNotIn("worktree_path", recovery["arguments"])
            self.assertIn("operator-approved summary", recovery["guidance"])

    def test_both_model_switch_races_fail_as_concurrent_state_change(self) -> None:
        commands: list[dict] = []
        before = self._before()
        raced_meta = copy.deepcopy(before)
        raced_meta["snapshotSequence"] = 11
        raced_meta["page"]["snapshotSequence"] = 11
        raced_meta["page"]["threadSequence"] = 11
        raced_meta["thread"]["modelSelection"] = copy.deepcopy(self.TARGET_PAIR)
        raced_meta["thread"]["latestTurn"] = latest_turn(
            turn_id="turn-race", state="running"
        )
        with LoopbackServer(
            [
                Response(value=before),
                self._captured_dispatch_at(commands, 11),
                Response(value=raced_meta),
            ]
        ) as server:
            first = invoke(
                server,
                tools.t3_thread_send,
                {
                    "thread_id": "thread-1",
                    "message": "Race one",
                    "instance_id": "codex",
                    "model": "gpt-5.6",
                    "busy_policy": "queue",
                },
            )
        self.assertEqual(first["error_code"], "concurrent_state_change")
        self.assertEqual([item["method"] for item in server.requests].count("POST"), 1)

        commands = []
        metadata = copy.deepcopy(before)
        metadata["snapshotSequence"] = 11
        metadata["page"]["snapshotSequence"] = 11
        metadata["page"]["threadSequence"] = 11
        metadata["thread"]["modelSelection"] = copy.deepcopy(self.TARGET_PAIR)
        second_race = copy.deepcopy(metadata)
        second_race["snapshotSequence"] = 12
        second_race["page"]["snapshotSequence"] = 12
        second_race["page"]["threadSequence"] = 12
        second_race["thread"]["latestTurn"] = latest_turn(
            turn_id="turn-race", state="running"
        )
        with LoopbackServer(
            [
                Response(value=before),
                self._captured_dispatch_at(commands, 11),
                Response(value=metadata),
                Response(value=second_race),
            ]
        ) as server:
            second = invoke(
                server,
                tools.t3_thread_send,
                {
                    "thread_id": "thread-1",
                    "message": "Race two",
                    "instance_id": "codex",
                    "model": "gpt-5.6",
                    "busy_policy": "queue",
                },
            )
        self.assertEqual(second["error_code"], "concurrent_state_change")
        self.assertEqual([item["method"] for item in server.requests].count("POST"), 1)
        self.assertEqual(
            second["details"]["completed_phase"], "model_selection_metadata"
        )

    def _start_failure(self, error_text: str) -> tuple[dict, list[dict], list[dict]]:
        def failed(detail: dict, command: dict) -> None:
            current_session = detail["thread"]["session"]
            current_session["status"] = "error"
            current_session["activeTurnId"] = None
            current_session["lastError"] = error_text
            detail["thread"]["latestTurn"] = latest_turn(
                turn_id="turn-new", state="error"
            )
            detail["thread"]["activities"] = [
                activity(
                    activity_id="provider-start-failed",
                    kind="provider.turn.start.failed",
                    payload={"detail": error_text},
                    sequence=12,
                    created_at=command["createdAt"],
                    turn_id=None,
                )
            ]

        return self._successful_switch(final_hook=failed)

    def test_command_correlated_unrestorable_start_error_rehomes_without_raw_text(self) -> None:
        for error_text in (
            "Requested provider instance 'missing' is not configured in this build. SENTINEL-RAW",
            "model change requires a new thread SENTINEL-RAW",
            "incompatible resume driver SENTINEL-RAW",
        ):
            with self.subTest(error_text=error_text):
                result, _commands, requests = self._start_failure(error_text)
                encoded = json.dumps(result)
                self.assertEqual(result["error_code"], "unsupported_model_switch")
                self.assertNotIn("SENTINEL-RAW", encoded)
                self.assertNotIn(error_text, encoded)
                self.assertEqual(
                    result["details"]["completed_phase"],
                    "model_selection_metadata",
                )
                self.assertEqual(
                    result["details"]["recovery"]["tool"], "t3_thread_create"
                )
                self.assertEqual(
                    [item["method"] for item in requests].count("POST"), 2
                )

    def test_command_correlated_quota_error_is_typed_and_redacted(self) -> None:
        error_text = "provider quota exceeded SENTINEL-PROVIDER-SECRET"
        result, _commands, requests = self._start_failure(error_text)
        encoded = json.dumps(result)
        self.assertEqual(result["error_code"], "provider_limit_exhausted")
        self.assertNotIn(error_text, encoded)
        self.assertNotIn("SENTINEL-PROVIDER-SECRET", encoded)
        self.assertEqual([item["method"] for item in requests].count("POST"), 2)

    def test_unknown_or_stale_start_failure_is_not_mislabeled_or_replayed(self) -> None:
        unknown_text = "temporary upstream transport failure SENTINEL-UNKNOWN"
        unknown, _commands, unknown_requests = self._start_failure(unknown_text)
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["verification"], "accepted_pending_projection")
        self.assertNotIn("SENTINEL-UNKNOWN", json.dumps(unknown))
        self.assertNotIn("recovery", unknown)
        self.assertEqual(
            [item["method"] for item in unknown_requests].count("POST"), 2
        )

        def stale_failure(detail: dict, command: dict) -> None:
            current_session = detail["thread"]["session"]
            current_session["status"] = "error"
            current_session["activeTurnId"] = None
            current_session["lastError"] = "quota exceeded SENTINEL-STALE"
            current_session["updatedAt"] = "2026-08-21T12:00:00Z"
            detail["thread"]["latestTurn"] = latest_turn(
                turn_id="turn-new", state="error"
            )
            detail["thread"]["activities"] = [
                activity(
                    activity_id="stale-provider-failure",
                    kind="provider.turn.start.failed",
                    payload={"detail": "quota exceeded SENTINEL-STALE"},
                    sequence=12,
                    created_at="2026-08-21T12:00:00Z",
                    turn_id=None,
                )
            ]

        stale, _commands, stale_requests = self._successful_switch(
            final_hook=stale_failure
        )
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["verification"], "accepted_pending_projection")
        self.assertNotEqual(stale.get("error_code"), "provider_limit_exhausted")
        self.assertNotIn("SENTINEL-STALE", json.dumps(stale))
        self.assertEqual([item["method"] for item in stale_requests].count("POST"), 2)

    def test_override_http_error_details_are_compactly_redacted(self) -> None:
        raw_error = {
            "code": "SENTINEL-SERVER-CODE",
            "reason": "SENTINEL-SERVER-REASON",
            "traceId": "SENTINEL-SERVER-TRACE",
        }
        cases = (
            ("initial_read", [Response(status=400, value=raw_error)], 0),
            (
                "metadata_dispatch",
                [Response(value=self._before()), Response(status=400, value=raw_error)],
                1,
            ),
        )
        for name, responses, post_count in cases:
            with self.subTest(name=name), LoopbackServer(responses) as server:
                result = invoke(
                    server,
                    tools.t3_thread_send,
                    {
                        "thread_id": "thread-1",
                        "message": "Do not reflect server details",
                        "instance_id": "codex",
                        "model": "gpt-5.6",
                        "busy_policy": "queue",
                    },
                )
            encoded = json.dumps(result)
            self.assertEqual(result["error_code"], "invalid_request")
            self.assertNotIn("SENTINEL-SERVER", encoded)
            self.assertEqual(
                [item["method"] for item in server.requests].count("POST"),
                post_count,
            )

    def test_accepted_pending_projection_is_compact_and_stops_each_phase(self) -> None:
        before = self._before()
        with LoopbackServer(
            [
                Response(value=before),
                Response(value={"sequence": 11}),
                Response(value={"malformed": "SENTINEL-RAW"}),
            ]
        ) as server:
            metadata_pending = invoke(
                server,
                tools.t3_thread_send,
                {
                    "thread_id": "thread-1",
                    "message": "Pending metadata",
                    "instance_id": "codex",
                    "model": "gpt-5.6",
                    "busy_policy": "queue",
                },
            )
        self.assertFalse(metadata_pending["ok"])
        self.assertTrue(metadata_pending["accepted"])
        self.assertEqual(metadata_pending["phase"], "metadata")
        self.assertEqual(
            metadata_pending["verification"], "accepted_pending_projection"
        )
        self.assertNotIn("detail", metadata_pending)
        self.assertNotIn("SENTINEL-RAW", json.dumps(metadata_pending))
        self.assertEqual([item["method"] for item in server.requests].count("POST"), 1)

        commands: list[dict] = []
        metadata = copy.deepcopy(before)
        metadata["snapshotSequence"] = 11
        metadata["page"]["snapshotSequence"] = 11
        metadata["page"]["threadSequence"] = 11
        metadata["thread"]["modelSelection"] = copy.deepcopy(self.TARGET_PAIR)
        with LoopbackServer(
            [
                Response(value=before),
                self._captured_dispatch_at(commands, 11),
                Response(value=metadata),
                Response(value=metadata),
                self._captured_dispatch_at(commands, 12),
                Response(value={"malformed": "SENTINEL-RAW"}),
            ]
        ) as server:
            turn_pending = invoke(
                server,
                tools.t3_thread_send,
                {
                    "thread_id": "thread-1",
                    "message": "Pending turn",
                    "instance_id": "codex",
                    "model": "gpt-5.6",
                    "busy_policy": "queue",
                },
            )
        self.assertFalse(turn_pending["ok"])
        self.assertTrue(turn_pending["accepted"])
        self.assertEqual(turn_pending["phase"], "turn")
        self.assertNotIn("detail", turn_pending)
        self.assertNotIn("SENTINEL-RAW", json.dumps(turn_pending))
        self.assertEqual([item["method"] for item in server.requests].count("POST"), 2)
        self.assertIn("metadata_command_id", turn_pending)
        self.assertIn("turn_command_id", turn_pending)

    def test_success_requires_monotonic_sequence_target_session_and_unique_linked_message(self) -> None:
        nonmonotonic_metadata, _commands, metadata_requests = self._successful_switch(
            metadata_sequence=10, turn_sequence=12
        )
        self.assertEqual(
            nonmonotonic_metadata["error_code"], "concurrent_state_change"
        )
        self.assertEqual(
            [item["method"] for item in metadata_requests].count("POST"), 1
        )

        regressed, _commands, _requests = self._successful_switch(
            metadata_sequence=11, turn_sequence=10
        )
        self.assertEqual(regressed["error_code"], "concurrent_state_change")

        def missing_instance(detail: dict, _command: dict) -> None:
            detail["thread"]["session"].pop("providerInstanceId")

        missing, _commands, _requests = self._successful_switch(
            final_hook=missing_instance
        )
        self.assertEqual(missing["verification"], "accepted_pending_projection")
        self.assertNotEqual(missing.get("action"), "model_switched_and_turn_started")

        def duplicate(detail: dict, _command: dict) -> None:
            detail["thread"]["messages"].append(
                copy.deepcopy(detail["thread"]["messages"][0])
            )

        duplicated, _commands, _requests = self._successful_switch(final_hook=duplicate)
        self.assertEqual(duplicated["verification"], "accepted_pending_projection")
        self.assertNotEqual(
            duplicated.get("action"), "model_switched_and_turn_started"
        )

        def late_old_instance(detail: dict, command: dict) -> None:
            detail["thread"]["activities"] = [
                activity(
                    activity_id="late-old-instance",
                    kind="provider.session.updated",
                    payload={"providerInstanceId": "codex-main"},
                    sequence=12,
                    created_at=command["createdAt"],
                    turn_id="turn-new",
                )
            ]

        late, _commands, _requests = self._successful_switch(
            final_hook=late_old_instance
        )
        self.assertEqual(late["error_code"], "concurrent_state_change")
        self.assertEqual(
            [item["method"] for item in _requests].count("POST"), 2
        )

    def test_final_projection_requires_consistent_nonterminal_target_turn(self) -> None:
        def completed(detail: dict, _command: dict) -> None:
            detail["thread"]["session"]["status"] = "ready"
            detail["thread"]["session"]["activeTurnId"] = None
            detail["thread"]["latestTurn"] = latest_turn(
                turn_id="turn-new", state="completed"
            )

        completed_result, _commands, _requests = self._successful_switch(
            final_hook=completed
        )
        self.assertTrue(completed_result["ok"])
        self.assertEqual(completed_result["turn_state"], "completed")
        self.assertEqual(completed_result["session_state"], "ready")

        def stopped(detail: dict, _command: dict) -> None:
            detail["thread"]["session"]["status"] = "stopped"
            detail["thread"]["session"]["activeTurnId"] = None

        def interrupted(detail: dict, _command: dict) -> None:
            detail["thread"]["session"]["status"] = "interrupted"
            detail["thread"]["session"]["activeTurnId"] = None
            detail["thread"]["latestTurn"] = latest_turn(
                turn_id="turn-new", state="interrupted"
            )

        for name, hook in (("stopped", stopped), ("interrupted", interrupted)):
            with self.subTest(name=name):
                result, _commands, requests = self._successful_switch(
                    final_hook=hook
                )
                self.assertEqual(result["error_code"], "unsupported_model_switch")
                self.assertFalse(result["retryable"])
                self.assertNotEqual(
                    result.get("action"), "model_switched_and_turn_started"
                )
                self.assertEqual(
                    [item["method"] for item in requests].count("POST"), 2
                )

        def generic_post_start_error(detail: dict, command: dict) -> None:
            current_session = detail["thread"]["session"]
            current_session["status"] = "error"
            current_session["activeTurnId"] = None
            current_session["lastError"] = "runtime failed SENTINEL-RUNTIME"
            current_session["updatedAt"] = command["createdAt"]
            detail["thread"]["latestTurn"] = latest_turn(
                turn_id="turn-new", state="error"
            )

        errored, _commands, error_requests = self._successful_switch(
            final_hook=generic_post_start_error
        )
        self.assertFalse(errored["ok"])
        self.assertEqual(errored["verification"], "accepted_pending_projection")
        self.assertNotIn("SENTINEL-RUNTIME", json.dumps(errored))
        self.assertEqual([item["method"] for item in error_requests].count("POST"), 2)

        def mismatched_active_turn(detail: dict, _command: dict) -> None:
            detail["thread"]["session"]["activeTurnId"] = "turn-concurrent"

        mismatched, _commands, mismatch_requests = self._successful_switch(
            final_hook=mismatched_active_turn
        )
        self.assertEqual(mismatched["error_code"], "concurrent_state_change")
        self.assertEqual(
            [item["method"] for item in mismatch_requests].count("POST"), 2
        )

    def test_post_turn_race_and_provenance_change_never_succeed_or_rollback(self) -> None:
        def concurrent_message(detail: dict, command: dict) -> None:
            detail["thread"]["messages"].append(
                projected_message(
                    message_id="message-concurrent",
                    role="user",
                    text="Concurrent user work",
                    turn_id="turn-concurrent",
                    created_at=command["createdAt"],
                )
            )

        raced, commands, requests = self._successful_switch(
            final_hook=concurrent_message
        )
        self.assertEqual(raced["error_code"], "concurrent_state_change")
        self.assertEqual([command["type"] for command in commands], [
            "thread.meta.update",
            "thread.turn.start",
        ])
        self.assertEqual([item["method"] for item in requests].count("POST"), 2)

        before = self._before()
        before["thread"]["branch"] = "feature/original"
        before["thread"]["worktreePath"] = "/work/original"
        preserved, _commands, _requests = self._successful_switch(before=before)
        self.assertTrue(preserved["ok"])

        def changed_branch(detail: dict, _command: dict) -> None:
            detail["thread"]["branch"] = "feature/concurrent"

        changed, _commands, changed_requests = self._successful_switch(
            before=before, final_hook=changed_branch
        )
        self.assertEqual(changed["error_code"], "concurrent_state_change")
        self.assertEqual(
            [item["method"] for item in changed_requests].count("POST"), 2
        )

    def test_quota_error_session_must_still_be_observed_ready_or_idle(self) -> None:
        exhausted = session(status="error")
        exhausted["lastError"] = "Usage limit reached for current provider"
        before = self._before(current_session=exhausted)
        with LoopbackServer([Response(value=before)]) as server:
            result = invoke(
                server,
                tools.t3_thread_send,
                {
                    "thread_id": "thread-1",
                    "message": "Switch only from ready or idle",
                    "instance_id": "codex",
                    "model": "gpt-5.6",
                    "busy_policy": "queue",
                },
            )
        self.assertEqual(result["error_code"], "unsupported_model_switch")
        self.assertFalse(result["retryable"])
        self.assertEqual([item["method"] for item in server.requests], ["GET"])

        historical = self._before()
        historical["thread"]["latestTurn"] = latest_turn(
            turn_id="turn-old", state="error"
        )
        historical["thread"]["session"]["lastError"] = (
            "Historical provider failure must not override current readiness"
        )
        continued, _commands, _requests = self._successful_switch(before=historical)
        self.assertTrue(continued["ok"])
        self.assertEqual(continued["action"], "model_switched_and_turn_started")


class AgentFacingWaitToolTests(unittest.TestCase):
    def _invoke_wait(self, detail: dict, args: dict) -> tuple[dict, LoopbackServer]:
        self.assertIn("t3_thread_wait", tools.OPERATIONS)
        shell = shell_snapshot(sequence=detail["snapshotSequence"])
        responder = projection_responder(shell, detail)
        server = LoopbackServer([responder] * 12)
        server.__enter__()
        try:
            result = invoke(server, tools.OPERATIONS["t3_thread_wait"], args)
        finally:
            server.__exit__(None, None, None)
        return result, server

    def test_wait_rejects_unbounded_timeout_before_http(self) -> None:
        self.assertIn("t3_thread_wait", tools.OPERATIONS)
        with LoopbackServer([]) as server:
            result = invoke(
                server,
                tools.OPERATIONS["t3_thread_wait"],
                {"thread_id": "thread-1", "timeout_seconds": 31},
            )
        self.assertEqual(result["error_code"], "invalid_input")
        self.assertEqual(server.requests, [])

    def test_wait_cumulative_response_budget_failure_is_sanitized(self) -> None:
        shell = shell_snapshot(sequence=4)
        detail = detail_snapshot(sequence=4)
        shell_size = len(json.dumps(shell).encode("utf-8"))
        detail_size = len(json.dumps(detail).encode("utf-8"))
        with LoopbackServer(
            [Response(value=shell), Response(value=detail)]
        ) as server:
            transport = tools.T3Client(
                server.base_url,
                server.token,
                cumulative_response_limit=shell_size + detail_size - 1,
            )
            with mock.patch.object(tools, "_make_client", return_value=transport):
                result = invoke(
                    server,
                    tools.OPERATIONS["t3_thread_wait"],
                    {"thread_id": "thread-1", "timeout_seconds": 0},
                )

        self.assertEqual(result["error_code"], "response_budget_exhausted")
        self.assertFalse(result["outcome_ambiguous"])
        self.assertNotIn(server.token, json.dumps(result))
        self.assertEqual([item["method"] for item in server.requests], ["GET", "GET"])

    def test_wait_reports_per_thread_progress_and_latest_assistant_delta(self) -> None:
        detail = with_projection_fields(
            detail_snapshot(
                sequence=20,
                messages=[
                    projected_message(
                        message_id="assistant-progress",
                        role="assistant",
                        text="Implemented the first step",
                        turn_id="turn-1",
                        created_at="2026-08-21T12:02:00Z",
                    )
                ],
                turn=latest_turn(turn_id="turn-1", state="running"),
                current_session=session(status="running", active_turn_id="turn-1"),
            ),
            thread_sequence=8,
        )
        result, _server = self._invoke_wait(
            detail,
            {
                "thread_id": "thread-1",
                "after_thread_sequence": 7,
                "until": "change",
                "timeout_seconds": 0,
            },
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["wait_outcome"], "progressed")
        self.assertEqual(result["thread_sequence"], 8)
        self.assertEqual(result["liveness"], "working")
        self.assertTrue(result["progress"])
        self.assertEqual(result["latest_assistant_update"]["id"], "assistant-progress")

    def test_wait_returns_a_compact_delta_without_snapshot_or_duplicate_update(self) -> None:
        messages = [
            projected_message(
                message_id=f"assistant-{index}",
                role="assistant",
                text=f"historical-material-{index}",
                turn_id=f"turn-{index}",
                created_at=f"2026-08-21T12:{index // 60:02d}:{index % 60:02d}Z",
            )
            for index in range(149)
        ]
        messages.append(
            projected_message(
                message_id="assistant-latest",
                role="assistant",
                text="Only this latest material update belongs in the wait receipt.",
                turn_id="turn-current",
                created_at="2026-08-21T15:00:00Z",
            )
        )
        detail = with_projection_fields(
            detail_snapshot(
                sequence=150,
                messages=messages,
                turn=latest_turn(turn_id="turn-current", state="running"),
                current_session=session(
                    status="running", active_turn_id="turn-current"
                ),
            ),
            thread_sequence=150,
        )

        result, _server = self._invoke_wait(
            detail,
            {
                "thread_id": "thread-1",
                "after_thread_sequence": 149,
                "until": "change",
                "timeout_seconds": 0,
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["latest_assistant_update"]["id"], "assistant-latest")
        self.assertEqual(
            set(result["material_delta"]),
            {
                "pending_requests",
                "pending_requests_truncated",
                "last_error",
                "last_error_truncated",
                "last_error_original_utf16_units",
                "latest_user_update",
                "actionable_plan",
                "plan_progress",
                "plan_progress_truncated",
                "updated_at",
                "settled_at",
            },
        )
        encoded = json.dumps(result)
        self.assertNotIn("latest_assistant_update", result["material_delta"])
        self.assertNotIn("historical-material-0", encoded)
        self.assertNotIn("model_selection", result["material_delta"])
        self.assertNotIn("worktree_path", result["material_delta"])
        self.assertLess(len(encoded), 12_000)

    def test_wait_distinguishes_action_required_settled_and_timeout(self) -> None:
        blocked = with_projection_fields(
            detail_snapshot(
                sequence=10,
                turn=latest_turn(turn_id="turn-1", state="running"),
                current_session=session(status="running", active_turn_id="turn-1"),
            ),
            thread_sequence=10,
        )
        blocked["thread"]["activities"] = [
            activity(
                activity_id="input-open",
                kind="user-input.requested",
                payload={"requestId": "input-open", "questions": []},
                sequence=10,
                created_at="2026-08-21T12:00:00Z",
            )
        ]
        settled = with_projection_fields(
            detail_snapshot(
                sequence=11,
                turn=latest_turn(turn_id="turn-1", state="completed"),
                current_session=session(status="ready"),
            ),
            thread_sequence=11,
        )
        unchanged = with_projection_fields(
            detail_snapshot(
                sequence=12,
                turn=latest_turn(turn_id="turn-1", state="running"),
                current_session=session(status="running", active_turn_id="turn-1"),
            ),
            thread_sequence=12,
        )
        cases = (
            (
                blocked,
                {"thread_id": "thread-1", "until": "blocked", "timeout_seconds": 0},
                "action_required",
                "action_required",
            ),
            (
                settled,
                {"thread_id": "thread-1", "until": "terminal", "timeout_seconds": 0},
                "settled",
                "settled",
            ),
            (
                settled,
                {
                    "thread_id": "thread-1",
                    "after_thread_sequence": 11,
                    "until": "terminal",
                    "timeout_seconds": 0,
                },
                "timeout",
                "settled",
            ),
            (
                unchanged,
                {
                    "thread_id": "thread-1",
                    "after_thread_sequence": 12,
                    "until": "change",
                    "timeout_seconds": 0,
                },
                "timeout",
                "working",
            ),
        )
        for detail, args, outcome, liveness in cases:
            with self.subTest(outcome=outcome):
                result, _server = self._invoke_wait(detail, args)
                self.assertTrue(result["ok"])
                self.assertEqual(result["wait_outcome"], outcome)
                self.assertEqual(result["liveness"], liveness)

    def test_wait_never_settles_while_canonical_background_work_is_live(self) -> None:
        for background_liveness in ("working", "monitoring"):
            with self.subTest(background_liveness=background_liveness):
                detail = with_projection_fields(
                    detail_snapshot(
                        sequence=15,
                        turn=latest_turn(turn_id="turn-1", state="completed"),
                        current_session=session(status="ready"),
                    ),
                    thread_sequence=15,
                )
                detail["thread"]["backgroundLiveness"] = background_liveness
                detail["thread"]["planProgress"] = {
                    "step": "Background work remains",
                    "completedSteps": 1,
                    "totalSteps": 3,
                }
                result, _server = self._invoke_wait(
                    detail,
                    {
                        "thread_id": "thread-1",
                        "until": "terminal",
                        "timeout_seconds": 0,
                    },
                )

                self.assertTrue(result["ok"])
                self.assertEqual(result["wait_outcome"], "timeout")
                self.assertEqual(result["lifecycle"], "running")
                self.assertEqual(result["liveness"], background_liveness)
                self.assertEqual(
                    result["material_delta"]["plan_progress"],
                    {
                        "step": "Background work remains",
                        "completedSteps": 1,
                        "totalSteps": 3,
                    },
                )

    def test_wait_never_settles_terminal_state_with_live_background_work(self) -> None:
        for status in ("error", "stopped"):
            with self.subTest(status=status):
                detail = with_projection_fields(
                    detail_snapshot(
                        sequence=17,
                        turn=latest_turn(turn_id="turn-1", state="error"),
                        current_session=session(status=status),
                    ),
                    thread_sequence=17,
                )
                detail["thread"]["backgroundLiveness"] = "monitoring"
                result, _server = self._invoke_wait(
                    detail,
                    {
                        "thread_id": "thread-1",
                        "until": "terminal",
                        "timeout_seconds": 0,
                    },
                )

                self.assertEqual(result["wait_outcome"], "timeout")
                self.assertEqual(result["lifecycle"], "running")
                self.assertEqual(result["liveness"], "monitoring")

    def test_wait_receipt_has_one_cumulative_projection_budget(self) -> None:
        detail = with_projection_fields(
            detail_snapshot(
                sequence=16,
                messages=[
                    projected_message(
                        message_id="assistant-large",
                        role="assistant",
                        text="a" * 120_000,
                        turn_id="turn-1",
                    )
                ],
                turn=latest_turn(turn_id="turn-1", state="running"),
                current_session=session(status="running", active_turn_id="turn-1"),
            ),
            thread_sequence=16,
        )
        detail["thread"]["planProgress"] = {
            "step": "p" * 120_000,
            "completedSteps": 1,
            "totalSteps": 32,
        }
        detail["thread"]["activities"] = [
            activity(
                activity_id=f"wait-approval-{index}",
                kind="approval.requested",
                payload={
                    "requestId": f"wait-request-{index}",
                    "requestKind": "command",
                    "requestType": "exec_command_approval",
                    "detail": "d" * 20_000,
                },
                sequence=index + 1,
                created_at=f"2026-08-21T12:00:{index:02d}Z",
                turn_id="turn-1",
            )
            for index in range(32)
        ]
        result, _server = self._invoke_wait(
            detail,
            {"thread_id": "thread-1", "until": "change", "timeout_seconds": 0},
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["projection_truncated"])
        self.assertLessEqual(
            len(json.dumps(result, ensure_ascii=False).encode("utf-8")),
            tools.MAX_MODEL_PROJECTION_UTF8_BYTES,
        )


class AgentFacingRespondToolTests(unittest.TestCase):
    def _pending_detail(self, *activities: dict, sequence: int = 4) -> dict:
        detail = with_projection_fields(
            detail_snapshot(
                sequence=sequence,
                turn=latest_turn(turn_id="turn-1", state="running"),
                current_session=session(status="running", active_turn_id="turn-1"),
            ),
            thread_sequence=sequence,
        )
        detail["thread"]["activities"] = list(activities)
        return detail

    def _invoke_with_short_projection_window(
        self,
        before: dict,
        after: dict,
        response_args: dict,
    ) -> tuple[dict, list[dict]]:
        commands: list[dict] = []

        def dispatch(request: dict) -> Response:
            commands.append(json.loads(request["body"]))
            return Response(value={"sequence": 5})

        with LoopbackServer(
            [Response(value=before), Response(value=before), dispatch]
            + [Response(value=after)] * 12
        ) as server:
            transport = tools.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.1,
                mutation_timeout=0.08,
                mutation_poll_timeout=0.04,
                poll_interval=0.01,
                retry_backoff=(),
            )
            with mock.patch.object(tools, "_make_client", return_value=transport):
                result = invoke(
                    server,
                    tools.OPERATIONS["t3_thread_respond"],
                    {
                        "thread_id": "thread-1",
                        "turn_id": "turn-1",
                        **response_args,
                    },
                )
        return result, commands

    def test_respond_requires_explicit_expected_turn_before_http(self) -> None:
        with LoopbackServer([]) as server:
            result = invoke(
                server,
                tools.OPERATIONS["t3_thread_respond"],
                {
                    "thread_id": "thread-1",
                    "request_id": "approval-open",
                    "decision": "accept",
                },
            )

        self.assertEqual(result["error_code"], "invalid_input")
        self.assertEqual(server.requests, [])

    def test_respond_revalidates_current_turn_before_non_atomic_dispatch(self) -> None:
        old_request = activity(
            activity_id="approval-old",
            kind="approval.requested",
            payload={
                "requestId": "reused-request",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
            turn_id="turn-1",
        )
        newer_request = copy.deepcopy(old_request)
        newer_request.update(
            {
                "id": "approval-new",
                "sequence": 5,
                "createdAt": "2026-08-21T12:00:01Z",
                "turnId": "turn-2",
            }
        )
        before = self._pending_detail(old_request, sequence=4)
        changed = with_projection_fields(
            detail_snapshot(
                sequence=5,
                turn=latest_turn(turn_id="turn-2", state="running"),
                current_session=session(status="running", active_turn_id="turn-2"),
            ),
            thread_sequence=5,
        )
        changed["thread"]["activities"] = [newer_request]

        with LoopbackServer(
            [Response(value=before), Response(value=changed)]
        ) as server:
            result = invoke(
                server,
                tools.OPERATIONS["t3_thread_respond"],
                {
                    "thread_id": "thread-1",
                    "request_id": "reused-request",
                    "turn_id": "turn-1",
                    "decision": "accept",
                },
            )

        self.assertEqual(result["error_code"], "conflict")
        self.assertEqual(
            [request["method"] for request in server.requests],
            ["GET", "GET"],
        )

    def test_respond_dispatches_exact_typed_approval_or_user_input_command(self) -> None:
        approval = activity(
            activity_id="approval-open",
            kind="approval.requested",
            payload={
                "requestId": "approval-open",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        user_input = activity(
            activity_id="input-open",
            kind="user-input.requested",
            payload={
                "requestId": "input-open",
                "questions": [
                    {
                        "id": "scope",
                        "header": "Scope",
                        "question": "Choose scope",
                        "options": [],
                        "multiSelect": False,
                    }
                ],
            },
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        cases = (
            (
                approval,
                {
                    "request_id": "approval-open",
                    "turn_id": "turn-1",
                    "decision": "acceptForSession",
                },
                "thread.approval.respond",
                {"decision": "acceptForSession"},
                "approval.resolved",
            ),
            (
                user_input,
                {
                    "request_id": "input-open",
                    "turn_id": "turn-1",
                    "answers": {"scope": "Focused"},
                },
                "thread.user-input.respond",
                {"answers": {"scope": "Focused"}},
                "user-input.resolved",
            ),
        )
        self.assertIn("t3_thread_respond", tools.OPERATIONS)
        for requested, response_args, command_type, command_payload, resolved_kind in cases:
            with self.subTest(command_type=command_type):
                commands: list[dict] = []
                before = self._pending_detail(requested, sequence=4)

                def dispatch(request: dict) -> Response:
                    commands.append(json.loads(request["body"]))
                    return Response(value={"sequence": 5})

                def readback(_request: dict) -> Response:
                    command_time = datetime.fromisoformat(
                        commands[0]["createdAt"].replace("Z", "+00:00")
                    ) + timedelta(milliseconds=1)
                    resolved = activity(
                        activity_id="resolved",
                        kind=resolved_kind,
                        payload={
                            "requestId": response_args["request_id"],
                            **command_payload,
                        },
                        sequence=5,
                        created_at=command_time.isoformat(timespec="milliseconds").replace(
                            "+00:00", "Z"
                        ),
                    )
                    if resolved_kind == "user-input.resolved":
                        resolved.pop("sequence")
                    return Response(
                        value=self._pending_detail(requested, resolved, sequence=5)
                    )

                with LoopbackServer(
                    [Response(value=before), Response(value=before), dispatch, readback]
                ) as server:
                    result = invoke(
                        server,
                        tools.OPERATIONS["t3_thread_respond"],
                        {
                            "thread_id": "thread-1",
                            "turn_id": "turn-1",
                            **response_args,
                        },
                    )
                self.assertTrue(result["ok"])
                self.assertTrue(result["accepted"])
                self.assertTrue(result["completed"])
                self.assertEqual(result["verification"], "verified")
                self.assertEqual(result["action"], "pending_request_responded")
                self.assertEqual(result["request_id"], response_args["request_id"])
                self.assertEqual(commands[0]["type"], command_type)
                self.assertEqual(commands[0]["threadId"], "thread-1")
                self.assertEqual(commands[0]["requestId"], response_args["request_id"])
                for key, value in command_payload.items():
                    self.assertEqual(commands[0][key], value)

                self.assertNotIn("detail", result)
                self.assertEqual(
                    {
                        "request_id": result["request_id"],
                        "request_kind": result["request_kind"],
                        "thread_id": result["thread_id"],
                        "command_id": result["command_id"],
                        "dispatch_sequence": result["dispatch_sequence"],
                    },
                    {
                        "request_id": response_args["request_id"],
                        "request_kind": (
                            "approval"
                            if resolved_kind == "approval.resolved"
                            else "user_input"
                        ),
                        "thread_id": "thread-1",
                        "command_id": commands[0]["commandId"],
                        "dispatch_sequence": 5,
                    },
                )

    def test_respond_pending_projection_is_compact_and_keeps_reconciliation_ids(self) -> None:
        requested = activity(
            activity_id="approval-open",
            kind="approval.requested",
            payload={
                "requestId": "approval-open",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        before = self._pending_detail(requested, sequence=4)
        delayed = self._pending_detail(requested, sequence=4)

        result, commands = self._invoke_with_short_projection_window(
            before,
            delayed,
            {"request_id": "approval-open", "decision": "decline"},
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(result["request_id"], "approval-open")
        self.assertEqual(result["thread_id"], "thread-1")
        self.assertEqual(result["command_id"], commands[0]["commandId"])
        self.assertEqual(result["dispatch_sequence"], 5)
        self.assertEqual(result["reconciliation"]["required_snapshot_sequence"], 5)
        self.assertEqual(
            result["reconciliation"]["arguments"]["thread_id"], "thread-1"
        )
        self.assertNotIn("detail", result)
        self.assertLess(len(json.dumps(result)), 4_000)

    def test_respond_preserves_javascript_safe_integer_boundaries(self) -> None:
        safe = 2**53 - 1
        requested = activity(
            activity_id="input-open",
            kind="user-input.requested",
            payload={"requestId": "input-open", "questions": []},
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        for answer in (-safe, safe):
            with self.subTest(answer_sign=1 if answer > 0 else -1):
                resolved = activity(
                    activity_id=(
                        "resolved-positive" if answer > 0 else "resolved-negative"
                    ),
                    kind="user-input.resolved",
                    payload={
                        "requestId": "input-open",
                        "answers": {"count": answer},
                    },
                    sequence=5,
                    created_at="2026-08-21T12:00:01Z",
                )
                with mock.patch.object(
                    tools,
                    "_now_rfc3339",
                    return_value="2026-08-21T12:00:00.500Z",
                ):
                    result, commands = self._invoke_with_short_projection_window(
                        self._pending_detail(requested, sequence=4),
                        self._pending_detail(requested, resolved, sequence=5),
                        {"request_id": "input-open", "answers": {"count": answer}},
                    )

                self.assertTrue(result["ok"])
                self.assertTrue(result["completed"])
                self.assertEqual(commands[0]["answers"], {"count": answer})

    def test_respond_verifies_t3_canonical_single_and_multi_choice_answers(self) -> None:
        requested = activity(
            activity_id="input-open",
            kind="user-input.requested",
            payload={"requestId": "input-open", "questions": []},
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        cases = (
            ({"scope": ["Focused"]}, {"scope": "Focused"}),
            ({"scope": ["Focused", "Broad"]}, {"scope": ["Focused", "Broad"]}),
        )
        for requested_answers, projected_answers in cases:
            with self.subTest(requested_answers=requested_answers):
                resolved = activity(
                    activity_id="resolved",
                    kind="user-input.resolved",
                    payload={
                        "requestId": "input-open",
                        "answers": projected_answers,
                    },
                    sequence=5,
                    created_at="2026-08-21T12:00:01Z",
                )
                with mock.patch.object(
                    tools,
                    "_now_rfc3339",
                    return_value="2026-08-21T12:00:00.500Z",
                ):
                    result, commands = self._invoke_with_short_projection_window(
                        self._pending_detail(requested, sequence=4),
                        self._pending_detail(requested, resolved, sequence=5),
                        {
                            "request_id": "input-open",
                            "answers": requested_answers,
                        },
                    )

                self.assertTrue(result["ok"])
                self.assertTrue(result["completed"])
                self.assertEqual(result["verification"], "verified")
                self.assertEqual(commands[0]["answers"], requested_answers)

    def test_respond_rejects_matching_provider_failure_activity(self) -> None:
        cases = (
            (
                "approval.requested",
                {
                    "requestId": "request-1",
                    "requestKind": "command",
                    "requestType": "exec_command_approval",
                },
                {"request_id": "request-1", "decision": "accept"},
                "provider.approval.respond.failed",
            ),
            (
                "user-input.requested",
                {"requestId": "request-1", "questions": []},
                {"request_id": "request-1", "answers": {"scope": "Focused"}},
                "provider.user-input.respond.failed",
            ),
        )
        for requested_kind, requested_payload, response_args, failure_kind in cases:
            with self.subTest(failure_kind=failure_kind):
                requested = activity(
                    activity_id="requested",
                    kind=requested_kind,
                    payload=requested_payload,
                    sequence=4,
                    created_at="2026-08-21T12:00:00Z",
                )
                failed = activity(
                    activity_id="failed",
                    kind=failure_kind,
                    payload={"requestId": "request-1", "reason": "stale pending request"},
                    sequence=5,
                    created_at="2026-08-21T12:00:01Z",
                )
                with mock.patch.object(
                    tools,
                    "_now_rfc3339",
                    return_value="2026-08-21T12:00:00.500Z",
                ):
                    result, commands = self._invoke_with_short_projection_window(
                        self._pending_detail(requested, sequence=4),
                        self._pending_detail(requested, failed, sequence=5),
                        response_args,
                    )

                self.assertEqual(result["error_code"], "concurrent_state_change")
                self.assertTrue(result["outcome_ambiguous"])
                self.assertEqual(len(commands), 1)

    def test_respond_rejects_real_null_turn_equal_time_provider_failure(self) -> None:
        command_time = "2026-08-21T12:00:01Z"
        cases = (
            (
                "approval.requested",
                {
                    "requestId": "request-1",
                    "requestKind": "command",
                    "requestType": "exec_command_approval",
                },
                {"request_id": "request-1", "decision": "accept"},
                "provider.approval.respond.failed",
            ),
            (
                "user-input.requested",
                {"requestId": "request-1", "questions": []},
                {"request_id": "request-1", "answers": {"scope": "Focused"}},
                "provider.user-input.respond.failed",
            ),
        )
        for requested_kind, requested_payload, response_args, failure_kind in cases:
            with self.subTest(failure_kind=failure_kind):
                requested = activity(
                    activity_id="requested",
                    kind=requested_kind,
                    payload=requested_payload,
                    sequence=4,
                    created_at="2026-08-21T12:00:00Z",
                    turn_id="turn-1",
                )
                failed = activity(
                    activity_id="failed",
                    kind=failure_kind,
                    payload={"requestId": "request-1", "reason": "provider failure"},
                    sequence=5,
                    created_at=command_time,
                    turn_id=None,
                )
                with mock.patch.object(
                    tools,
                    "_now_rfc3339",
                    return_value=command_time,
                ):
                    result, commands = self._invoke_with_short_projection_window(
                        self._pending_detail(requested, sequence=4),
                        self._pending_detail(requested, failed, sequence=5),
                        response_args,
                    )

                self.assertEqual(result["error_code"], "concurrent_state_change")
                self.assertTrue(result["outcome_ambiguous"])
                self.assertEqual(len(commands), 1)

    def test_respond_rejects_concurrent_different_resolution(self) -> None:
        cases = (
            (
                "approval.requested",
                {
                    "requestId": "request-1",
                    "requestKind": "command",
                    "requestType": "exec_command_approval",
                },
                {"request_id": "request-1", "decision": "accept"},
                "approval.resolved",
                {"requestId": "request-1", "decision": "decline"},
            ),
            (
                "user-input.requested",
                {"requestId": "request-1", "questions": []},
                {"request_id": "request-1", "answers": {"scope": "Focused"}},
                "user-input.resolved",
                {"requestId": "request-1", "answers": {"scope": "All"}},
            ),
            (
                "user-input.requested",
                {"requestId": "request-1", "questions": []},
                {
                    "request_id": "request-1",
                    "answers": {"confirmed": True},
                },
                "user-input.resolved",
                {
                    "requestId": "request-1",
                    "answers": {"confirmed": 1},
                },
            ),
        )
        for (
            requested_kind,
            requested_payload,
            response_args,
            resolved_kind,
            resolved_payload,
        ) in cases:
            with self.subTest(resolved_kind=resolved_kind):
                requested = activity(
                    activity_id="requested",
                    kind=requested_kind,
                    payload=requested_payload,
                    sequence=4,
                    created_at="2026-08-21T12:00:00Z",
                )
                resolved = activity(
                    activity_id="resolved",
                    kind=resolved_kind,
                    payload=resolved_payload,
                    sequence=5,
                    created_at="2026-08-21T12:00:01Z",
                )
                with mock.patch.object(
                    tools,
                    "_now_rfc3339",
                    return_value="2026-08-21T12:00:00.500Z",
                ):
                    result, commands = self._invoke_with_short_projection_window(
                        self._pending_detail(requested, sequence=4),
                        self._pending_detail(requested, resolved, sequence=5),
                        response_args,
                    )

                self.assertEqual(result["error_code"], "concurrent_state_change")
                self.assertTrue(result["outcome_ambiguous"])
                self.assertEqual(len(commands), 1)

    def test_respond_ignores_pre_dispatch_terminal_activity_and_remains_pending(self) -> None:
        stale_resolution = activity(
            activity_id="old-resolution",
            kind="approval.resolved",
            payload={"requestId": "request-1", "decision": "accept"},
            sequence=2,
            created_at="2026-08-21T11:59:58Z",
        )
        stale_resolution.pop("sequence")
        requested = activity(
            activity_id="requested",
            kind="approval.requested",
            payload={
                "requestId": "request-1",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        before = self._pending_detail(stale_resolution, requested, sequence=4)
        after = self._pending_detail(stale_resolution, sequence=5)

        result, commands = self._invoke_with_short_projection_window(
            before,
            after,
            {"request_id": "request-1", "decision": "accept"},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "pending_request_response_accepted")
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(result["dispatch_sequence"], 5)
        self.assertEqual(result["reconciliation"]["required_snapshot_sequence"], 5)
        self.assertEqual(len(commands), 1)

    def test_respond_ignores_newly_projected_unsequenced_terminal_older_than_request(self) -> None:
        requested = activity(
            activity_id="approval-requested",
            kind="approval.requested",
            payload={
                "requestId": "request-1",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        stale_resolution = activity(
            activity_id="hidden-old-resolution",
            kind="approval.resolved",
            payload={"requestId": "request-1", "decision": "accept"},
            sequence=2,
            created_at="2026-08-21T11:59:00Z",
        )
        stale_resolution.pop("sequence")
        result, commands = self._invoke_with_short_projection_window(
            self._pending_detail(requested, sequence=4),
            self._pending_detail(requested, stale_resolution, sequence=5),
            {"request_id": "request-1", "decision": "accept"},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "pending_request_response_accepted")
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(result["dispatch_sequence"], 5)
        self.assertEqual(len(commands), 1)

    def test_respond_ignores_unsequenced_terminal_at_exact_dispatch_timestamp(self) -> None:
        command_time = "2026-08-21T12:00:01.000Z"
        requested = activity(
            activity_id="approval-requested",
            kind="approval.requested",
            payload={
                "requestId": "request-1",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        ambiguous_resolution = activity(
            activity_id="equal-time-resolution",
            kind="approval.resolved",
            payload={"requestId": "request-1", "decision": "accept"},
            sequence=5,
            created_at=command_time,
        )
        ambiguous_resolution.pop("sequence")
        with mock.patch.object(tools, "_now_rfc3339", return_value=command_time):
            result, commands = self._invoke_with_short_projection_window(
                self._pending_detail(requested, sequence=4),
                self._pending_detail(requested, ambiguous_resolution, sequence=5),
                {"request_id": "request-1", "decision": "accept"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "pending_request_response_accepted")
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(len(commands), 1)

    def test_respond_ignores_newly_projected_sequenced_terminal_before_dispatch(
        self,
    ) -> None:
        command_time = "2026-08-21T12:00:02.000Z"
        requested = activity(
            activity_id="approval-requested",
            kind="approval.requested",
            payload={
                "requestId": "request-1",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        hidden_resolution = activity(
            activity_id="hidden-resolution",
            kind="approval.resolved",
            payload={"requestId": "request-1", "decision": "accept"},
            sequence=5,
            created_at="2026-08-21T12:00:01Z",
        )
        with mock.patch.object(tools, "_now_rfc3339", return_value=command_time):
            result, commands = self._invoke_with_short_projection_window(
                self._pending_detail(requested, sequence=4),
                self._pending_detail(requested, hidden_resolution, sequence=5),
                {"request_id": "request-1", "decision": "accept"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "pending_request_response_accepted")
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(len(commands), 1)

    def test_respond_accepts_low_session_sequence_terminal_after_dispatch(self) -> None:
        requested = activity(
            activity_id="approval-requested",
            kind="approval.requested",
            payload={
                "requestId": "request-1",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=1,
            created_at="2026-08-21T12:00:00Z",
        )
        resolved = activity(
            activity_id="approval-resolved",
            kind="approval.resolved",
            payload={"requestId": "request-1", "decision": "accept"},
            sequence=2,
            created_at="2026-08-21T12:00:02Z",
        )
        with mock.patch.object(
            tools,
            "_now_rfc3339",
            return_value="2026-08-21T12:00:01Z",
        ):
            result, commands = self._invoke_with_short_projection_window(
                self._pending_detail(requested, sequence=100),
                self._pending_detail(requested, resolved, sequence=101),
                {"request_id": "request-1", "decision": "accept"},
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["completed"])
        self.assertEqual(result["verification"], "verified")
        self.assertEqual(len(commands), 1)

    def test_respond_ignores_late_terminal_for_reused_id_on_other_turn(self) -> None:
        old_requested = activity(
            activity_id="old-requested",
            kind="approval.requested",
            payload={
                "requestId": "reused-id",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=1,
            created_at="2026-08-21T12:00:00Z",
            turn_id="turn-old",
        )
        old_resolved = activity(
            activity_id="old-resolved",
            kind="approval.resolved",
            payload={"requestId": "reused-id", "decision": "decline"},
            sequence=2,
            created_at="2026-08-21T12:00:00.500Z",
            turn_id="turn-old",
        )
        new_requested = activity(
            activity_id="new-requested",
            kind="approval.requested",
            payload={
                "requestId": "reused-id",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=3,
            created_at="2026-08-21T12:00:01Z",
            turn_id="turn-new",
        )
        late_old_resolution = activity(
            activity_id="late-old-resolution",
            kind="approval.resolved",
            payload={"requestId": "reused-id", "decision": "accept"},
            sequence=4,
            created_at="2026-08-21T12:00:02Z",
            turn_id="turn-old",
        )
        before = self._pending_detail(
            old_requested,
            old_resolved,
            new_requested,
            sequence=3,
        )
        after = self._pending_detail(
            old_requested,
            old_resolved,
            new_requested,
            late_old_resolution,
            sequence=5,
        )
        for detail in (before, after):
            detail["thread"]["latestTurn"] = latest_turn(
                turn_id="turn-new", state="running"
            )
            detail["thread"]["session"] = session(
                status="running", active_turn_id="turn-new"
            )
        with mock.patch.object(
            tools,
            "_now_rfc3339",
            return_value="2026-08-21T12:00:01.500Z",
        ):
            result, commands = self._invoke_with_short_projection_window(
                before,
                after,
                {
                    "request_id": "reused-id",
                    "turn_id": "turn-new",
                    "decision": "accept",
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "pending_request_response_accepted")
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(len(commands), 1)

    def test_respond_rejects_equal_timestamp_unsequenced_terminal_before_post(self) -> None:
        created_at = "2026-08-21T12:00:00Z"
        requested = activity(
            activity_id="z-requested",
            kind="approval.requested",
            payload={
                "requestId": "request-1",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=4,
            created_at=created_at,
        )
        ambiguous_resolution = activity(
            activity_id="a-resolved",
            kind="approval.resolved",
            payload={"requestId": "request-1", "decision": "decline"},
            sequence=5,
            created_at=created_at,
        )
        ambiguous_resolution.pop("sequence")
        detail = self._pending_detail(
            requested,
            ambiguous_resolution,
            sequence=4,
        )

        result, commands = self._invoke_with_short_projection_window(
            detail,
            detail,
            {"request_id": "request-1", "decision": "accept"},
        )

        self.assertEqual(result["error_code"], "conflict")
        self.assertFalse(result["outcome_ambiguous"])
        self.assertEqual(commands, [])

    def test_respond_rejects_equal_sequence_terminal_before_post(self) -> None:
        requested = activity(
            activity_id="approval-requested",
            kind="approval.requested",
            payload={
                "requestId": "request-1",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=4,
            created_at="2026-08-21T12:00:01Z",
        )
        ambiguous_resolution = activity(
            activity_id="approval-resolved",
            kind="approval.resolved",
            payload={"requestId": "request-1", "decision": "decline"},
            sequence=4,
            created_at="2026-08-21T12:00:00Z",
        )
        detail = self._pending_detail(
            requested,
            ambiguous_resolution,
            sequence=4,
        )

        result, commands = self._invoke_with_short_projection_window(
            detail,
            detail,
            {"request_id": "request-1", "decision": "accept"},
        )

        self.assertEqual(result["error_code"], "conflict")
        self.assertFalse(result["outcome_ambiguous"])
        self.assertEqual(commands, [])

    def test_respond_rejects_duplicate_same_kind_request_id_before_post(self) -> None:
        first = activity(
            activity_id="approval-requested-first",
            kind="approval.requested",
            payload={
                "requestId": "same-id",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=3,
            created_at="2026-08-21T12:00:00Z",
            turn_id="turn-1",
        )
        second = activity(
            activity_id="approval-requested-second",
            kind="approval.requested",
            payload={
                "requestId": "same-id",
                "requestKind": "file-read",
                "requestType": "file_read_approval",
            },
            sequence=4,
            created_at="2026-08-21T12:00:01Z",
            turn_id="turn-2",
        )
        detail = self._pending_detail(first, second, sequence=4)

        result, commands = self._invoke_with_short_projection_window(
            detail,
            detail,
            {"request_id": "same-id", "decision": "accept"},
        )

        self.assertEqual(result["error_code"], "conflict")
        self.assertFalse(result["outcome_ambiguous"])
        self.assertEqual(commands, [])

    def test_respond_stale_mismatched_or_ambiguous_request_never_posts(self) -> None:
        requested_approval = activity(
            activity_id="approval-requested",
            kind="approval.requested",
            payload={
                "requestId": "same-id",
                "requestKind": "command",
                "requestType": "exec_command_approval",
            },
            sequence=1,
            created_at="2026-08-21T12:00:00Z",
        )
        requested_input = activity(
            activity_id="input-requested",
            kind="user-input.requested",
            payload={"requestId": "same-id", "questions": []},
            sequence=2,
            created_at="2026-08-21T12:00:01Z",
        )
        resolved_approval = activity(
            activity_id="approval-resolved",
            kind="approval.resolved",
            payload={"requestId": "same-id"},
            sequence=3,
            created_at="2026-08-21T12:00:02Z",
        )
        cases = (
            (
                self._pending_detail(requested_approval, resolved_approval),
                {"request_id": "same-id", "decision": "accept"},
            ),
            (
                self._pending_detail(requested_input),
                {"request_id": "same-id", "decision": "accept"},
            ),
            (
                self._pending_detail(requested_approval),
                {"request_id": "same-id", "answers": {"scope": "Focused"}},
            ),
            (
                self._pending_detail(requested_approval, requested_input),
                {"request_id": "same-id", "decision": "accept"},
            ),
        )
        self.assertIn("t3_thread_respond", tools.OPERATIONS)
        for detail, response_args in cases:
            with self.subTest(response_args=response_args, activity_count=len(detail["thread"]["activities"])):
                with LoopbackServer([Response(value=detail)]) as server:
                    result = invoke(
                        server,
                        tools.OPERATIONS["t3_thread_respond"],
                        {
                            "thread_id": "thread-1",
                            "turn_id": "turn-1",
                            **response_args,
                        },
                    )
                self.assertEqual(result["error_code"], "conflict")
                self.assertEqual([request["method"] for request in server.requests], ["GET"])


class AgentFacingSettleToolTests(unittest.TestCase):
    @staticmethod
    def _settlement_detail(
        *,
        sequence: int = 4,
        settled_override: str | None = None,
        settled_at: str | None = None,
        pinned_at: str | None = None,
        pin_order_key: str | None = None,
        snoozed_until: str | None = None,
        snoozed_at: str | None = None,
        current_session: dict | None = None,
        messages: list[dict] | None = None,
        activities: list[dict] | None = None,
    ) -> dict:
        detail = with_projection_fields(
            detail_snapshot(
                sequence=sequence,
                turn=latest_turn(),
                current_session=(
                    session(status="ready")
                    if current_session is None
                    else current_session
                ),
                messages=messages,
            ),
            thread_sequence=sequence,
        )
        detail["thread"].update(
            {
                "settledOverride": settled_override,
                "settledAt": settled_at,
                "pinnedAt": pinned_at,
                "pinOrderKey": pin_order_key,
                "snoozedUntil": snoozed_until,
                "snoozedAt": snoozed_at,
            }
        )
        detail["thread"]["activities"] = list(activities or [])
        return detail

    def _invoke_settle(
        self,
        responses: list[Response],
        *,
        command_id: str | None = None,
        transport_kwargs: dict | None = None,
    ) -> tuple[dict, LoopbackServer]:
        server = LoopbackServer(responses)  # type: ignore[arg-type]
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        selected_id = command_id or str(uuid.uuid4())
        if transport_kwargs is None:
            patcher = mock.patch.object(
                tools.T3Client,
                "new_uuid4",
                return_value=selected_id,
            )
        else:
            transport = tools.T3Client(
                server.base_url,
                server.token,
                **transport_kwargs,
            )
            patcher = mock.patch.object(tools, "_make_client", return_value=transport)
        with patcher:
            if transport_kwargs is None:
                result = invoke(
                    server,
                    tools.OPERATIONS["t3_thread_settle"],
                    {"thread_id": "thread-1"},
                )
            else:
                with mock.patch.object(
                    tools.T3Client,
                    "new_uuid4",
                    return_value=selected_id,
                ):
                    result = invoke(
                        server,
                        tools.OPERATIONS["t3_thread_settle"],
                        {"thread_id": "thread-1"},
                    )
        return result, server

    def test_settle_rejects_missing_or_unknown_public_arguments_without_http(self) -> None:
        with LoopbackServer([]) as server:
            for args in ({}, {"thread_id": "thread-1", "unexpected": True}):
                with self.subTest(fields=tuple(sorted(args))):
                    result = invoke(
                        server,
                        tools.OPERATIONS["t3_thread_settle"],
                        args,
                    )
                    self.assertEqual(result["error_code"], "invalid_input")
                    self.assertEqual(server.requests, [])

    def test_settle_dispatches_exact_native_command_and_waits_for_companion_clear(self) -> None:
        command_id = str(uuid.uuid4())
        settled_at = "2026-08-23T12:00:00Z"
        before = self._settlement_detail(
            settled_override="settled",
            settled_at=settled_at,
            pinned_at="2026-08-23T11:00:00Z",
            pin_order_key="0001",
            snoozed_until="2026-08-24T12:00:00Z",
            snoozed_at="2026-08-23T11:30:00Z",
        )
        still_pinned = self._settlement_detail(
            sequence=5,
            settled_override="settled",
            settled_at=settled_at,
            pinned_at="2026-08-23T11:00:00Z",
            pin_order_key="0001",
            snoozed_until="2026-08-24T12:00:00Z",
            snoozed_at="2026-08-23T11:30:00Z",
        )
        cleared = self._settlement_detail(
            sequence=5,
            settled_override="settled",
            settled_at=settled_at,
        )
        result, server = self._invoke_settle(
            [
                Response(value=settlement_descriptor()),
                Response(value=before),
                Response(value={"sequence": 5}),
                Response(value=still_pinned),
                Response(value=cleared),
            ],
            command_id=command_id,
        )

        self.assertEqual(
            [(item["method"], item["path"]) for item in server.requests],
            [
                ("GET", "/.well-known/t3/environment"),
                ("GET", "/api/orchestration/threads/thread-1?turnLimit=150"),
                ("POST", "/api/orchestration/dispatch"),
                ("GET", "/api/orchestration/threads/thread-1?turnLimit=150"),
                ("GET", "/api/orchestration/threads/thread-1?turnLimit=150"),
            ],
        )
        command = json.loads(server.requests[2]["body"])
        self.assertEqual(
            command,
            {
                "type": "thread.settle",
                "commandId": command_id,
                "threadId": "thread-1",
            },
        )
        self.assertTrue(uuid.UUID(command["commandId"]).version == 4)
        encoded_command = json.dumps(command)
        for forbidden_type in {
                "thread.session.stop",
                "thread.delete",
                "thread.archive",
                "thread.unpin",
                "thread.unsnooze",
        }:
            self.assertNotIn(forbidden_type, encoded_command)
        self.assertEqual(
            set(result),
            {
                "ok",
                "action",
                "command_id",
                "thread_id",
                "dispatch_sequence",
                "dispatch_attempts",
                "recovered_after_ambiguous_dispatch",
                "accepted",
                "verification",
                "completed",
                "settled_override",
                "settled_at",
            },
        )
        self.assertEqual(result["action"], "thread_settled")
        self.assertEqual(result["command_id"], command_id)
        self.assertEqual(result["thread_id"], "thread-1")
        self.assertEqual(result["settled_override"], "settled")
        self.assertEqual(result["settled_at"], settled_at)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["completed"])
        self.assertEqual(result["verification"], "verified")
        self.assertNotIn("detail", result)
        self.assertNotIn("messages", result)
        self.assertNotIn("activities", result)

    def test_fully_settled_thread_is_a_compact_noop_without_post(self) -> None:
        settled_at = "2026-08-23T12:00:00Z"
        settled = self._settlement_detail(
            settled_override="settled",
            settled_at=settled_at,
        )
        result, server = self._invoke_settle(
            [
                Response(value=settlement_descriptor()),
                Response(value=settled),
            ]
        )

        self.assertEqual(
            result,
            {
                "ok": True,
                "action": "thread_already_settled",
                "command_id": None,
                "thread_id": "thread-1",
                "settled_override": "settled",
                "settled_at": settled_at,
            },
        )
        self.assertEqual(
            [item["method"] for item in server.requests],
            ["GET", "GET"],
        )

    def test_settle_capability_preflight_fails_closed_before_detail_or_post(self) -> None:
        for enabled in (None, False):
            with self.subTest(enabled=enabled):
                result, server = self._invoke_settle(
                    [Response(value=settlement_descriptor(enabled=enabled))]
                )
                self.assertEqual(result["error_code"], "conflict")
                self.assertEqual(
                    [(item["method"], item["path"]) for item in server.requests],
                    [("GET", "/.well-known/t3/environment")],
                )

    def test_settle_rejects_unsafe_thread_states_without_post(self) -> None:
        now = datetime.now(timezone.utc)
        fresh_user_at = (now - timedelta(seconds=30)).isoformat().replace(
            "+00:00", "Z"
        )
        pending_approval = activity(
            activity_id="approval-open",
            kind="approval.requested",
            payload={"requestId": "request-approval"},
            sequence=1,
            created_at="2026-08-23T11:00:00Z",
        )
        pending_input = activity(
            activity_id="input-open",
            kind="user-input.requested",
            payload={"requestId": "request-input", "questions": []},
            sequence=1,
            created_at="2026-08-23T11:00:00Z",
        )
        deleted = self._settlement_detail()
        deleted["thread"]["deletedAt"] = "2026-08-23T11:00:00Z"
        archived = self._settlement_detail()
        archived["thread"]["archivedAt"] = "2026-08-23T11:00:00Z"
        cases = {
            "deleted": deleted,
            "archived": archived,
            "starting_session": self._settlement_detail(
                current_session=session(status="starting")
            ),
            "running_session": self._settlement_detail(
                current_session=session(status="running", active_turn_id="turn-1")
            ),
            "pending_approval": self._settlement_detail(
                activities=[pending_approval]
            ),
            "pending_input": self._settlement_detail(activities=[pending_input]),
            "queued_turn": self._settlement_detail(
                messages=[
                    projected_message(
                        message_id="queued-user",
                        role="user",
                        text="queued work",
                        turn_id=None,
                        created_at=fresh_user_at,
                    )
                ]
            ),
        }
        for name, detail in cases.items():
            with self.subTest(state=name):
                result, server = self._invoke_settle(
                    [
                        Response(value=settlement_descriptor()),
                        Response(value=detail),
                    ]
                )
                self.assertEqual(result["error_code"], "conflict")
                self.assertEqual(
                    [item["method"] for item in server.requests],
                    ["GET", "GET"],
                )
                self.assertEqual(
                    sum(item["method"] == "POST" for item in server.requests),
                    0,
                )

    def test_settle_queued_turn_abs_age_includes_exact_boundaries_only(self) -> None:
        fixed_now = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

        cases = (
            ("past_exact_boundary", -120_000, True),
            ("future_exact_boundary", 120_000, True),
            ("past_just_outside", -120_001, False),
            ("future_just_outside", 120_001, False),
        )
        for name, offset_milliseconds, queued in cases:
            with self.subTest(case=name):
                user_at = (
                    fixed_now + timedelta(milliseconds=offset_milliseconds)
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                before = self._settlement_detail(
                    messages=[
                        projected_message(
                            message_id=f"user-{name}",
                            role="user",
                            text="queued boundary work",
                            turn_id=None,
                            created_at=user_at,
                        )
                    ]
                )
                responses = [
                    Response(value=settlement_descriptor()),
                    Response(value=before),
                ]
                if not queued:
                    responses.extend(
                        [
                            Response(value={"sequence": 5}),
                            Response(
                                value=self._settlement_detail(
                                    sequence=5,
                                    settled_override="settled",
                                    settled_at="2026-08-23T12:00:00Z",
                                    messages=before["thread"]["messages"],
                                )
                            ),
                        ]
                    )
                with mock.patch.object(tools, "datetime", FixedDateTime):
                    result, server = self._invoke_settle(responses)

                self.assertEqual(
                    result["error_code"] if queued else result["action"],
                    "conflict" if queued else "thread_settled",
                )
                self.assertEqual(
                    sum(item["method"] == "POST" for item in server.requests),
                    0 if queued else 1,
                )

    def test_settle_post_dispatch_archive_or_delete_is_concurrent_state_change(self) -> None:
        for field in ("archivedAt", "deletedAt"):
            with self.subTest(field=field):
                command_id = str(uuid.uuid4())
                raced = self._settlement_detail(
                    sequence=5,
                    settled_override="settled",
                    settled_at="2026-08-23T12:00:00Z",
                )
                raced["thread"][field] = "2026-08-23T12:00:01Z"
                result, server = self._invoke_settle(
                    [
                        Response(value=settlement_descriptor()),
                        Response(value=self._settlement_detail()),
                        Response(value={"sequence": 5}),
                        Response(value=raced),
                    ],
                    command_id=command_id,
                )

                posts = [
                    item for item in server.requests if item["method"] == "POST"
                ]
                self.assertEqual(len(posts), 1)
                self.assertEqual(
                    json.loads(posts[0]["body"]),
                    {
                        "type": "thread.settle",
                        "commandId": command_id,
                        "threadId": "thread-1",
                    },
                )
                self.assertEqual(result["error_code"], "concurrent_state_change")
                self.assertTrue(result["outcome_ambiguous"])
                self.assertEqual(result["command_id"], command_id)
                self.assertEqual(result["thread_id"], "thread-1")
                self.assertFalse(result["ok"])

    def test_settle_opaque_500_retries_identical_command_until_accepted_sequence(self) -> None:
        command_id = str(uuid.uuid4())
        ambiguous_readback = self._settlement_detail(
            sequence=5,
            settled_override="settled",
            settled_at="2026-08-23T12:00:00Z",
        )
        accepted_readback = self._settlement_detail(
            sequence=6,
            settled_override="settled",
            settled_at="2026-08-23T12:00:00Z",
        )
        result, server = self._invoke_settle(
            [
                Response(value=settlement_descriptor()),
                Response(value=self._settlement_detail()),
                Response(status=500, value={"opaque": True}),
                Response(value=ambiguous_readback),
                Response(value={"sequence": 6}),
                Response(value=accepted_readback),
            ],
            command_id=command_id,
            transport_kwargs={"retry_backoff": ()},
        )

        self.assertEqual(
            [item["method"] for item in server.requests],
            ["GET", "GET", "POST", "GET", "POST", "GET"],
        )
        post_bodies = [
            item["body"] for item in server.requests if item["method"] == "POST"
        ]
        self.assertEqual(len(post_bodies), 2)
        self.assertEqual(post_bodies[0], post_bodies[1])
        self.assertEqual(
            json.loads(post_bodies[0]),
            {
                "type": "thread.settle",
                "commandId": command_id,
                "threadId": "thread-1",
            },
        )
        self.assertEqual(result["action"], "thread_settled")
        self.assertEqual(result["command_id"], command_id)
        self.assertEqual(result["thread_id"], "thread-1")
        self.assertEqual(result["dispatch_sequence"], 6)
        self.assertEqual(result["dispatch_attempts"], 2)
        self.assertTrue(result["recovered_after_ambiguous_dispatch"])
        self.assertTrue(result["accepted"])
        self.assertTrue(result["completed"])
        self.assertEqual(result["verification"], "verified")

    def test_settle_request_reducer_clears_resolved_and_stale_requests_by_id(self) -> None:
        now = datetime.now(timezone.utc)
        stale_user_at = (now - timedelta(seconds=121)).isoformat().replace(
            "+00:00", "Z"
        )
        activities = [
            activity(
                activity_id="approval-requested",
                kind="approval.requested",
                payload={"requestId": "approval-shared"},
                sequence=1,
                created_at="2026-08-23T11:00:00Z",
                turn_id="turn-old",
            ),
            activity(
                activity_id="approval-resolved",
                kind="approval.resolved",
                payload={"requestId": "approval-shared"},
                sequence=2,
                created_at="2026-08-23T11:00:01Z",
                turn_id="turn-other",
            ),
            activity(
                activity_id="input-requested",
                kind="user-input.requested",
                payload={"requestId": "input-shared", "questions": []},
                sequence=3,
                created_at="2026-08-23T11:00:02Z",
                turn_id="turn-old",
            ),
            activity(
                activity_id="input-stale-clear",
                kind="provider.user-input.respond.failed",
                payload={
                    "requestId": "input-shared",
                    "detail": "stale pending user-input request",
                },
                sequence=4,
                created_at="2026-08-23T11:00:03Z",
                turn_id=None,
            ),
        ]
        before = self._settlement_detail(
            messages=[
                projected_message(
                    message_id="stale-unlinked-user",
                    role="user",
                    text="old queued work",
                    turn_id=None,
                    created_at=stale_user_at,
                )
            ],
            activities=activities,
        )
        after = self._settlement_detail(
            sequence=5,
            settled_override="settled",
            settled_at="2026-08-23T12:00:00Z",
            messages=before["thread"]["messages"],
            activities=activities,
        )
        result, server = self._invoke_settle(
            [
                Response(value=settlement_descriptor()),
                Response(value=before),
                Response(value={"sequence": 5}),
                Response(value=after),
            ]
        )

        self.assertEqual(result["action"], "thread_settled")
        self.assertEqual(
            [item["method"] for item in server.requests],
            ["GET", "GET", "POST", "GET"],
        )

    def test_settle_recent_unlinked_user_is_not_queued_for_error_session(self) -> None:
        fresh_user_at = (
            datetime.now(timezone.utc) - timedelta(seconds=15)
        ).isoformat().replace("+00:00", "Z")
        before = self._settlement_detail(
            current_session=session(status="error"),
            messages=[
                projected_message(
                    message_id="error-session-user",
                    role="user",
                    text="failed work",
                    turn_id=None,
                    created_at=fresh_user_at,
                )
            ],
        )
        after = self._settlement_detail(
            sequence=5,
            current_session=session(status="error"),
            settled_override="settled",
            settled_at="2026-08-23T12:00:00Z",
            messages=before["thread"]["messages"],
        )
        result, server = self._invoke_settle(
            [
                Response(value=settlement_descriptor()),
                Response(value=before),
                Response(value={"sequence": 5}),
                Response(value=after),
            ]
        )
        self.assertEqual(result["action"], "thread_settled")
        self.assertEqual(sum(item["method"] == "POST" for item in server.requests), 1)

    def test_settle_accepted_pending_projection_is_compact_and_never_redispatches(self) -> None:
        before = self._settlement_detail(sequence=4)
        delayed = self._settlement_detail(sequence=5)
        command_id = str(uuid.uuid4())
        result, server = self._invoke_settle(
            [
                Response(value=settlement_descriptor()),
                Response(value=before),
                Response(value={"sequence": 5}),
            ]
            + [Response(value=delayed)] * 12,
            command_id=command_id,
            transport_kwargs={
                "request_timeout": 0.1,
                "mutation_timeout": 0.08,
                "mutation_poll_timeout": 0.04,
                "poll_interval": 0.01,
                "retry_backoff": (),
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["action"], "thread_settle_accepted_pending_projection"
        )
        self.assertEqual(result["command_id"], command_id)
        self.assertEqual(result["thread_id"], "thread-1")
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(result["dispatch_sequence"], 5)
        self.assertEqual(
            result["reconciliation"],
            {
                "tool": "t3_thread_read",
                "arguments": {
                    "thread_id": "thread-1",
                    "view": "raw",
                    "turn_limit": 150,
                },
                "required_snapshot_sequence": 5,
            },
        )
        self.assertNotIn("detail", result)
        self.assertNotIn("settled_override", result)
        self.assertNotIn("settled_at", result)
        self.assertNotIn("messages", result)
        self.assertNotIn("activities", result)
        self.assertEqual(
            sum(item["method"] == "POST" for item in server.requests),
            1,
        )
        self.assertLess(len(json.dumps(result)), 4_000)


class MutationToolTests(unittest.TestCase):
    @staticmethod
    def configured_create_context(base_url: str, **overrides: object) -> FakeContext:
        values: dict[str, object] = {
            "default_instance_id": "codex-secondary",
            "default_model": "test-model-large",
            "default_reasoning_effort": "ultra",
            "default_model_aliases": ["large"],
        }
        values.update(overrides)
        return FakeContext(base_url, **values)

    def test_create_uses_project_default_and_exact_native_payload(self) -> None:
        commands: list[dict] = []

        def created_readback(request: dict) -> Response:
            command = commands[0]
            detail = detail_snapshot(sequence=1, thread_id=command["threadId"])
            detail["thread"].update(
                {
                    "projectId": command["projectId"],
                    "title": command["title"],
                    "modelSelection": command["modelSelection"],
                    "runtimeMode": command["runtimeMode"],
                    "interactionMode": command["interactionMode"],
                }
            )
            return Response(value=detail)

        with LoopbackServer(
            [Response(value=shell_snapshot()), captured_dispatch(commands), created_readback]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_create,
                {"project_id": "project-1", "title": " New thread "},
            )
        self.assertTrue(result["ok"])
        command = commands[0]
        self.assertEqual(command["type"], "thread.create")
        self.assertEqual(command["title"], "New thread")
        self.assertEqual(
            command["modelSelection"]["options"],
            [
                {"id": "reasoning_effort", "value": "high"},
                {"id": "web_search", "value": True},
            ],
        )
        self.assertEqual(command["runtimeMode"], "approval-required")
        self.assertEqual(command["interactionMode"], "default")
        self.assertIsNone(command["branch"])
        self.assertIsNone(command["worktreePath"])
        self.assertNotIn("bootstrap", command)
        self.assertEqual(uuid.UUID(command["commandId"]).version, 4)
        self.assertEqual(uuid.UUID(command["threadId"]).version, 4)

    def test_create_configured_default_reaches_create_and_initial_turn(self) -> None:
        commands: list[dict] = []

        def created_readback(request: dict) -> Response:
            command = commands[0]
            detail = detail_snapshot(sequence=1, thread_id=command["threadId"])
            detail["thread"].update(
                {
                    "projectId": command["projectId"],
                    "title": command["title"],
                    "modelSelection": copy.deepcopy(command["modelSelection"]),
                    "runtimeMode": command["runtimeMode"],
                    "interactionMode": command["interactionMode"],
                    "branch": command["branch"],
                    "worktreePath": command["worktreePath"],
                }
            )
            return Response(value=detail)

        def turn_readback(request: dict) -> Response:
            create_command, turn_command = commands
            detail = detail_snapshot(
                sequence=2,
                thread_id=create_command["threadId"],
                messages=[
                    message(
                        message_id=turn_command["message"]["messageId"],
                        text=turn_command["message"]["text"],
                    )
                ],
            )
            detail["thread"].update(
                {
                    "projectId": create_command["projectId"],
                    "title": create_command["title"],
                    "modelSelection": copy.deepcopy(create_command["modelSelection"]),
                    "runtimeMode": create_command["runtimeMode"],
                    "interactionMode": create_command["interactionMode"],
                    "branch": create_command["branch"],
                    "worktreePath": create_command["worktreePath"],
                }
            )
            return Response(value=detail)

        with LoopbackServer(
            [
                Response(value=shell_snapshot()),
                captured_dispatch(commands),
                created_readback,
                captured_dispatch(commands),
                turn_readback,
            ]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_create,
                {
                    "project_id": "project-1",
                    "title": "Configured model",
                    "initial_message": "Start",
                },
                context=self.configured_create_context(server.base_url),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            [command["type"] for command in commands],
            ["thread.create", "thread.turn.start"],
        )
        expected_selection = {
            "instanceId": "codex-secondary",
            "model": "test-model-large",
            "options": [{"id": "reasoningEffort", "value": "ultra"}],
        }
        self.assertEqual(commands[0]["modelSelection"], expected_selection)
        self.assertEqual(commands[1]["modelSelection"], expected_selection)

        commands = []
        with LoopbackServer(
            [
                Response(value=shell_snapshot()),
                captured_dispatch(commands),
                created_readback,
                captured_dispatch(commands),
                turn_readback,
            ]
        ) as server:
            alias_result = invoke(
                server,
                tools.t3_thread_create,
                {
                    "project_id": "project-1",
                    "title": "Configured model alias",
                    "instance_id": "codex-secondary",
                    "model": "  LaRgE  ",
                    "initial_message": "Start alias",
                },
                context=self.configured_create_context(server.base_url),
            )

        self.assertTrue(alias_result["ok"])
        self.assertEqual(
            [command["type"] for command in commands],
            ["thread.create", "thread.turn.start"],
        )
        self.assertEqual(commands[0]["modelSelection"], expected_selection)
        self.assertEqual(commands[1]["modelSelection"], expected_selection)

    def test_create_model_only_canonical_and_alias_resolve_exact_configured_pair(self) -> None:
        for supplied_model in ("test-model-large", "  LaRgE  "):
            with self.subTest(model=supplied_model):
                commands: list[dict] = []

                def readback(request: dict) -> Response:
                    command = commands[0]
                    detail = detail_snapshot(sequence=1, thread_id=command["threadId"])
                    detail["thread"].update(
                        {
                            "projectId": command["projectId"],
                            "title": command["title"],
                            "modelSelection": copy.deepcopy(command["modelSelection"]),
                            "runtimeMode": command["runtimeMode"],
                            "interactionMode": command["interactionMode"],
                        }
                    )
                    return Response(value=detail)

                with LoopbackServer(
                    [
                        Response(value=shell_snapshot()),
                        captured_dispatch(commands),
                        readback,
                    ]
                ) as server:
                    result = invoke(
                        server,
                        tools.t3_thread_create,
                        {
                            "project_id": "project-1",
                            "title": "Configured shorthand",
                            "model": supplied_model,
                        },
                        context=self.configured_create_context(server.base_url),
                    )

                self.assertTrue(result["ok"])
                self.assertEqual(
                    commands[0]["modelSelection"],
                    {
                        "instanceId": "codex-secondary",
                        "model": "test-model-large",
                        "options": [
                            {"id": "reasoningEffort", "value": "ultra"}
                        ],
                    },
                )

    def test_create_explicit_options_override_config_and_other_pair_stays_literal(self) -> None:
        cases = (
            (
                {
                    "project_id": "project-1",
                    "title": "Explicit effort",
                    "instance_id": "codex-secondary",
                    "model": " LaRgE ",
                    "model_options": [
                        {"id": "reasoningEffort", "value": "xhigh"},
                        {"id": "web_search", "value": False},
                    ],
                },
                {
                    "instanceId": "codex-secondary",
                    "model": "test-model-large",
                    "options": [
                        {"id": "reasoningEffort", "value": "xhigh"},
                        {"id": "web_search", "value": False},
                    ],
                },
            ),
            (
                {
                    "project_id": "project-1",
                    "title": "Explicit empty options",
                    "instance_id": "codex-secondary",
                    "model": "LARGE",
                    "model_options": [],
                },
                {
                    "instanceId": "codex-secondary",
                    "model": "test-model-large",
                    "options": [],
                },
            ),
            (
                {
                    "project_id": "project-1",
                    "title": "Literal other pair",
                    "instance_id": "other",
                    "model": "large",
                },
                {"instanceId": "other", "model": "large"},
            ),
            (
                {
                    "project_id": "project-1",
                    "title": "Literal unrelated model",
                    "instance_id": "codex-secondary",
                    "model": "gpt-other",
                },
                {"instanceId": "codex-secondary", "model": "gpt-other"},
            ),
        )
        for args, expected_selection in cases:
            with self.subTest(title=args["title"]):
                commands: list[dict] = []

                def readback(request: dict) -> Response:
                    command = commands[0]
                    detail = detail_snapshot(sequence=1, thread_id=command["threadId"])
                    detail["thread"].update(
                        {
                            "projectId": command["projectId"],
                            "title": command["title"],
                            "modelSelection": copy.deepcopy(command["modelSelection"]),
                            "runtimeMode": command["runtimeMode"],
                            "interactionMode": command["interactionMode"],
                        }
                    )
                    return Response(value=detail)

                with LoopbackServer(
                    [
                        Response(value=shell_snapshot()),
                        captured_dispatch(commands),
                        readback,
                    ]
                ) as server:
                    result = invoke(
                        server,
                        tools.t3_thread_create,
                        args,
                        context=self.configured_create_context(server.base_url),
                    )

                self.assertTrue(result["ok"])
                self.assertEqual(commands[0]["modelSelection"], expected_selection)

    def test_create_invalid_or_incomplete_model_defaults_fail_before_http(self) -> None:
        invalid_overrides = (
            {"default_model": None},
            {
                "default_instance_id": None,
                "default_model": None,
                "default_reasoning_effort": "ultra",
                "default_model_aliases": None,
            },
            {"default_model_aliases": ["large", " LARGE "]},
            {"default_model_aliases": "large"},
        )
        with LoopbackServer([]) as server:
            for overrides in invalid_overrides:
                with self.subTest(overrides=overrides):
                    result = invoke(
                        server,
                        tools.t3_thread_create,
                        {"project_id": "project-1", "title": "Invalid config"},
                        context=self.configured_create_context(
                            server.base_url, **overrides
                        ),
                    )
                    self.assertEqual(result["error_code"], "configuration_error")
            self.assertEqual(server.requests, [])

    def test_create_incomplete_or_unknown_shorthand_stays_invalid_before_http(self) -> None:
        invalid_args = (
            {
                "project_id": "project-1",
                "title": "Instance only",
                "instance_id": "codex-secondary",
            },
            {
                "project_id": "project-1",
                "title": "Unknown model",
                "model": "gpt-other",
            },
        )
        with LoopbackServer([]) as server:
            for args in invalid_args:
                with self.subTest(title=args["title"]):
                    result = invoke(
                        server,
                        tools.t3_thread_create,
                        args,
                        context=self.configured_create_context(server.base_url),
                    )
                    self.assertEqual(result["error_code"], "invalid_input")
            self.assertEqual(server.requests, [])

    def test_create_explicit_model_pair_and_missing_default_conflict(self) -> None:
        commands: list[dict] = []

        def explicit_detail(request: dict) -> Response:
            command = commands[0]
            detail = detail_snapshot(sequence=1, thread_id=command["threadId"])
            detail["thread"].update(
                {
                    "projectId": command["projectId"],
                    "title": command["title"],
                    "modelSelection": command["modelSelection"],
                    "runtimeMode": command["runtimeMode"],
                    "interactionMode": command["interactionMode"],
                }
            )
            return Response(value=detail)

        shell = shell_snapshot()
        with LoopbackServer([Response(value=shell), captured_dispatch(commands), explicit_detail]) as server:
            result = invoke(
                server,
                tools.t3_thread_create,
                {
                    "project_id": "project-1",
                    "title": "Explicit",
                    "instance_id": "other",
                    "model": "model-x",
                    "runtime_mode": "full-access",
                    "interaction_mode": "plan",
                },
            )
        self.assertTrue(result["ok"])
        self.assertEqual(commands[0]["modelSelection"], {"instanceId": "other", "model": "model-x"})

        commands = []
        with LoopbackServer(
            [Response(value=shell_snapshot()), captured_dispatch(commands), explicit_detail]
        ) as server:
            inherited = invoke(
                server,
                tools.t3_thread_create,
                {
                    "project_id": "project-1",
                    "title": "Exact default pair",
                    "instance_id": "codex-main",
                    "model": "gpt-current",
                },
            )
        self.assertTrue(inherited["ok"])
        self.assertEqual(
            commands[0]["modelSelection"]["options"],
            [
                {"id": "reasoning_effort", "value": "high"},
                {"id": "web_search", "value": True},
            ],
        )

        no_default = shell_snapshot()
        no_default["projects"][0]["defaultModelSelection"] = None
        with LoopbackServer([Response(value=no_default)]) as server:
            conflict = invoke(
                server,
                tools.t3_thread_create,
                {"project_id": "project-1", "title": "No default"},
            )
            self.assertEqual(conflict["error_code"], "conflict")
            self.assertEqual([item["method"] for item in server.requests], ["GET"])

    def test_create_runtime_precedence_and_invalid_config_fail_before_http(self) -> None:
        commands: list[dict] = []

        def readback(request: dict) -> Response:
            command = commands[-1]
            detail = detail_snapshot(sequence=len(commands), thread_id=command["threadId"])
            detail["thread"].update(
                {
                    "projectId": command["projectId"],
                    "title": command["title"],
                    "modelSelection": command["modelSelection"],
                    "runtimeMode": command["runtimeMode"],
                    "interactionMode": command["interactionMode"],
                    "branch": command["branch"],
                    "worktreePath": command["worktreePath"],
                }
            )
            return Response(value=detail)

        with LoopbackServer(
            [Response(value=shell_snapshot()), captured_dispatch(commands), readback]
        ) as server:
            configured = invoke(
                server,
                tools.t3_thread_create,
                {"project_id": "project-1", "title": "Configured"},
                context=FakeContext(server.base_url, default_runtime_mode="auto"),
            )
        self.assertTrue(configured["ok"])
        self.assertEqual(commands[0]["runtimeMode"], "auto")

        commands = []
        with LoopbackServer(
            [Response(value=shell_snapshot()), captured_dispatch(commands), readback]
        ) as server:
            explicit = invoke(
                server,
                tools.t3_thread_create,
                {
                    "project_id": "project-1",
                    "title": "Explicit override",
                    "runtime_mode": "full-access",
                },
                context=FakeContext(
                    server.base_url, default_runtime_mode="approval-required"
                ),
            )
        self.assertTrue(explicit["ok"])
        self.assertEqual(commands[0]["runtimeMode"], "full-access")
        self.assertIn("modify or delete", explicit["warning"])

        with LoopbackServer([]) as server:
            invalid = invoke(
                server,
                tools.t3_thread_create,
                {
                    "project_id": "project-1",
                    "title": "Invalid config",
                    "runtime_mode": "auto",
                },
                context=FakeContext(server.base_url, default_runtime_mode="unsupported"),
            )
            self.assertEqual(invalid["error_code"], "configuration_error")
            self.assertEqual(server.requests, [])

    def test_ambiguous_full_access_create_preserves_warning_and_error_semantics(self) -> None:
        commands: list[dict] = []

        def malformed_dispatch(request: dict) -> Response:
            commands.append(json.loads(request["body"]))
            return Response(value={})

        with LoopbackServer(
            [
                Response(value=shell_snapshot()),
                malformed_dispatch,
                Response(status=500, value={"code": "temporary"}),
            ]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_create,
                {
                    "project_id": "project-1",
                    "title": "Ambiguous full access",
                    "runtime_mode": "full-access",
                },
            )
        self.assertEqual(result["error_code"], "mutation_ambiguous")
        self.assertTrue(result["outcome_ambiguous"])
        self.assertFalse(result["retryable"])
        self.assertEqual(result["command_id"], commands[0]["commandId"])
        self.assertIn("modify or delete", result["details"]["warning"])

    def test_create_initial_turn_preserves_resolved_settings_metadata_and_options(self) -> None:
        commands: list[dict] = []

        def created_readback(request: dict) -> Response:
            command = commands[0]
            detail = detail_snapshot(sequence=1, thread_id=command["threadId"])
            detail["thread"].update(
                {
                    "projectId": command["projectId"],
                    "title": command["title"],
                    "modelSelection": command["modelSelection"],
                    "runtimeMode": command["runtimeMode"],
                    "interactionMode": command["interactionMode"],
                    "branch": command["branch"],
                    "worktreePath": command["worktreePath"],
                }
            )
            return Response(value=detail)

        def turn_readback(request: dict) -> Response:
            create_command, turn_command = commands
            detail = detail_snapshot(
                sequence=2,
                thread_id=create_command["threadId"],
                messages=[
                    message(
                        message_id=turn_command["message"]["messageId"],
                        text=turn_command["message"]["text"],
                    )
                ],
                turn=latest_turn(turn_id="turn-1", state="running"),
            )
            detail["thread"].update(
                {
                    "projectId": create_command["projectId"],
                    "title": create_command["title"],
                    "modelSelection": create_command["modelSelection"],
                    "runtimeMode": create_command["runtimeMode"],
                    "interactionMode": create_command["interactionMode"],
                    "branch": create_command["branch"],
                    "worktreePath": create_command["worktreePath"],
                }
            )
            return Response(value=detail)

        with LoopbackServer(
            [
                Response(value=shell_snapshot()),
                captured_dispatch(commands),
                created_readback,
                captured_dispatch(commands),
                turn_readback,
            ]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_create,
                {
                    "project_id": "project-1",
                    "title": "Planned work",
                    "instance_id": "codex-main",
                    "model": "gpt-current",
                    "model_options": [
                        {"id": "reasoning_effort", "value": "xhigh"},
                        {"id": "web_search", "value": False},
                    ],
                    "runtime_mode": "auto-accept-edits",
                    "interaction_mode": "plan",
                    "initial_message": " Draft a plan ",
                    "branch": " feature/native-plan ",
                    "worktree_path": " /work/project-plan ",
                },
                context=FakeContext(
                    server.base_url, default_runtime_mode="full-access"
                ),
            )
        self.assertTrue(result["ok"])
        self.assertEqual([item["type"] for item in commands], ["thread.create", "thread.turn.start"])
        create_command, turn_command = commands
        self.assertEqual(create_command["runtimeMode"], "auto-accept-edits")
        self.assertEqual(create_command["branch"], "feature/native-plan")
        self.assertEqual(create_command["worktreePath"], "/work/project-plan")
        self.assertEqual(
            create_command["modelSelection"]["options"],
            [
                {"id": "reasoning_effort", "value": "xhigh"},
                {"id": "web_search", "value": False},
            ],
        )
        for field in ("modelSelection", "runtimeMode", "interactionMode"):
            self.assertEqual(turn_command[field], create_command[field])
        self.assertEqual(turn_command["message"]["text"], "Draft a plan")
        self.assertNotIn("sourceProposedPlan", turn_command)
        self.assertNotEqual(create_command["commandId"], turn_command["commandId"])
        self.assertEqual(result["create_command_id"], create_command["commandId"])
        self.assertEqual(result["turn_command_id"], turn_command["commandId"])
        self.assertIn("atomic expected-mode", result["race_semantics"])

    def test_create_initial_turn_failure_reports_completed_create_phase(self) -> None:
        commands: list[dict] = []

        def created_readback(request: dict) -> Response:
            command = commands[0]
            detail = detail_snapshot(sequence=1, thread_id=command["threadId"])
            detail["thread"].update(
                {
                    "projectId": command["projectId"],
                    "title": command["title"],
                    "modelSelection": command["modelSelection"],
                    "runtimeMode": command["runtimeMode"],
                    "interactionMode": command["interactionMode"],
                    "branch": command["branch"],
                    "worktreePath": command["worktreePath"],
                }
            )
            return Response(value=detail)

        def failed_turn_dispatch(request: dict) -> Response:
            commands.append(json.loads(request["body"]))
            return Response(status=400, value={"code": "invalid"})

        with LoopbackServer(
            [
                Response(value=shell_snapshot()),
                captured_dispatch(commands),
                created_readback,
                failed_turn_dispatch,
            ]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_create,
                {
                    "project_id": "project-1",
                    "title": "Composite",
                    "initial_message": "Start",
                    "runtime_mode": "full-access",
                },
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["details"]["workflow_phase"], "initial_turn")
        self.assertEqual(
            result["details"]["verified_create_command_id"], commands[0]["commandId"]
        )
        self.assertEqual(result["details"]["created_thread_id"], commands[0]["threadId"])
        self.assertIn("modify or delete", result["details"]["warning"])
        self.assertEqual([item["method"] for item in server.requests].count("POST"), 2)

    def test_initial_turn_uuid_failure_reports_verified_create_and_warning(self) -> None:
        commands: list[dict] = []
        create_command_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())

        def created_readback(request: dict) -> Response:
            command = commands[0]
            detail = detail_snapshot(sequence=1, thread_id=command["threadId"])
            detail["thread"].update(
                {
                    "projectId": command["projectId"],
                    "title": command["title"],
                    "modelSelection": command["modelSelection"],
                    "runtimeMode": command["runtimeMode"],
                    "interactionMode": command["interactionMode"],
                    "branch": command["branch"],
                    "worktreePath": command["worktreePath"],
                }
            )
            return Response(value=detail)

        with LoopbackServer(
            [Response(value=shell_snapshot()), captured_dispatch(commands), created_readback]
        ) as server:
            with mock.patch.object(
                tools.T3Client,
                "new_uuid4",
                side_effect=[create_command_id, thread_id, tools.T3ClientError()],
            ):
                result = invoke(
                    server,
                    tools.t3_thread_create,
                    {
                        "project_id": "project-1",
                        "title": "UUID boundary",
                        "runtime_mode": "full-access",
                        "initial_message": "Start",
                    },
                )
        self.assertEqual(result["error_code"], "internal_error")
        self.assertEqual(result["details"]["workflow_phase"], "initial_turn")
        self.assertEqual(
            result["details"]["verified_create_command_id"], create_command_id
        )
        self.assertEqual(result["details"]["created_thread_id"], thread_id)
        self.assertIn("modify or delete", result["details"]["warning"])
        self.assertEqual([item["method"] for item in server.requests].count("POST"), 1)

    def test_create_rejects_invalid_model_options_before_http(self) -> None:
        invalid_options = (
            [{"id": "same", "value": "one"}, {"id": "same", "value": True}],
            [{"id": "x", "value": 1}],
            [{"id": "x", "value": " "}],
            [{"id": "x", "value": True, "extra": False}],
            [{"id": str(index), "value": True} for index in range(65)],
        )
        with LoopbackServer([]) as server:
            for options in invalid_options:
                with self.subTest(option_count=len(options)):
                    result = invoke(
                        server,
                        tools.t3_thread_create,
                        {
                            "project_id": "project-1",
                            "title": "Invalid options",
                            "instance_id": "codex-main",
                            "model": "gpt-current",
                            "model_options": options,
                        },
                    )
                    self.assertEqual(result["error_code"], "invalid_input")
            self.assertEqual(server.requests, [])

    def test_every_send_is_a_fresh_same_thread_turn_with_stored_settings(self) -> None:
        commands: list[dict] = []
        stopped = session(status="stopped", active_turn_id=None)
        before = detail_snapshot(sequence=1, current_session=stopped)

        def observed(index: int):
            def responder(request: dict) -> Response:
                command = commands[index]
                detail = detail_snapshot(
                    sequence=index + 1,
                    messages=[message(message_id=command["message"]["messageId"], text=command["message"]["text"])],
                    turn=latest_turn(turn_id="turn-1", state="running"),
                    current_session=stopped,
                )
                return Response(value=detail)

            return responder

        with LoopbackServer(
            [
                Response(value=before),
                captured_dispatch(commands),
                observed(0),
                Response(value=before),
                captured_dispatch(commands),
                observed(1),
            ]
        ) as server:
            first = invoke(
                server,
                tools.t3_thread_send,
                {"thread_id": "thread-1", "message": " one ", "busy_policy": "queue"},
            )
            second = invoke(
                server,
                tools.t3_thread_send,
                {"thread_id": "thread-1", "message": "two", "busy_policy": "queue"},
            )
        self.assertTrue(first["ok"] and second["ok"])
        self.assertEqual([item["type"] for item in commands], ["thread.turn.start"] * 2)
        self.assertNotEqual(commands[0]["commandId"], commands[1]["commandId"])
        self.assertNotEqual(commands[0]["message"]["messageId"], commands[1]["message"]["messageId"])
        for command in commands:
            self.assertEqual(command["threadId"], "thread-1")
            self.assertEqual(command["runtimeMode"], before["thread"]["runtimeMode"])
            self.assertEqual(command["interactionMode"], before["thread"]["interactionMode"])
            self.assertEqual(command["modelSelection"], before["thread"]["modelSelection"])
            self.assertNotIn("bootstrap", command)
            self.assertNotIn("sourceProposedPlan", command)
        self.assertIn("atomic expected-mode", first["race_semantics"])

    def test_ambiguous_send_preserves_mode_race_context(self) -> None:
        before = detail_snapshot(sequence=1)
        with LoopbackServer(
            [
                Response(value=before),
                Response(value={}),
                Response(status=500, value={"code": "temporary"}),
            ]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_send,
                {
                    "thread_id": "thread-1",
                    "message": "Continue",
                    "busy_policy": "queue",
                },
            )
        self.assertEqual(result["error_code"], "mutation_ambiguous")
        self.assertTrue(result["outcome_ambiguous"])
        self.assertIn("atomic expected-mode", result["details"]["race_semantics"])

    def test_mode_set_uses_dedicated_commands_noop_and_pending_approval_warning(self) -> None:
        commands: list[dict] = []
        active = session(status="running", active_turn_id="turn-1")
        before = detail_snapshot(
            sequence=1,
            turn=latest_turn(turn_id="turn-1", state="running"),
            current_session=active,
        )
        after = detail_snapshot(
            sequence=1,
            turn=latest_turn(turn_id="turn-1", state="running"),
            current_session=active,
        )
        after["thread"]["runtimeMode"] = "full-access"
        with LoopbackServer(
            [Response(value=before), captured_dispatch(commands), Response(value=after)]
        ) as server:
            changed = invoke(
                server,
                tools.t3_thread_set_mode,
                {"thread_id": "thread-1", "runtime_mode": "full-access"},
            )
        self.assertTrue(changed["ok"])
        self.assertEqual(commands[0]["type"], "thread.runtime-mode.set")
        self.assertEqual(commands[0]["runtimeMode"], "full-access")
        self.assertNotIn("interactionMode", commands[0])
        self.assertEqual(changed["active_turn_unchanged"], "turn-1")
        self.assertIn("approval already pending", changed["warning"])
        self.assertIn("full-access permits", changed["warning"])
        self.assertEqual(
            changed["warnings"],
            [tools.FULL_ACCESS_WARNING, tools.PENDING_APPROVAL_WARNING],
        )

        idle_after = detail_snapshot(sequence=2)
        idle_after["thread"]["runtimeMode"] = "full-access"
        with LoopbackServer(
            [
                Response(value=detail_snapshot(sequence=1)),
                Response(value={"sequence": 2}),
                Response(value=idle_after),
            ]
        ) as server:
            idle_full_access = invoke(
                server,
                tools.t3_thread_set_mode,
                {"thread_id": "thread-1", "runtime_mode": "full-access"},
            )
        self.assertEqual(
            idle_full_access["warnings"], [tools.FULL_ACCESS_WARNING]
        )
        self.assertEqual(idle_full_access["warning"], tools.FULL_ACCESS_WARNING)

        commands = []
        interaction_after = detail_snapshot(sequence=1)
        interaction_after["thread"]["interactionMode"] = "plan"
        with LoopbackServer(
            [
                Response(value=detail_snapshot(sequence=1)),
                captured_dispatch(commands),
                Response(value=interaction_after),
            ]
        ) as server:
            interaction = invoke(
                server,
                tools.t3_thread_set_mode,
                {"thread_id": "thread-1", "interaction_mode": "plan"},
            )
        self.assertTrue(interaction["ok"])
        self.assertEqual(commands[0]["type"], "thread.interaction-mode.set")
        self.assertEqual(commands[0]["interactionMode"], "plan")
        self.assertNotIn("runtimeMode", commands[0])

        with LoopbackServer([Response(value=interaction_after)]) as server:
            noop = invoke(
                server,
                tools.t3_thread_set_mode,
                {"thread_id": "thread-1", "interaction_mode": "plan"},
            )
            self.assertEqual(noop["action"], "mode_already_set")
            self.assertIsNone(noop["command_id"])
            self.assertEqual([item["method"] for item in server.requests], ["GET"])

    def test_mode_validation_and_failure_preserve_pending_approval_disclosure(self) -> None:
        with LoopbackServer([]) as server:
            for payload in (
                {"thread_id": "thread-1"},
                {
                    "thread_id": "thread-1",
                    "runtime_mode": "auto",
                    "interaction_mode": "plan",
                },
                {"thread_id": "thread-1", "runtime_mode": "unsupported"},
            ):
                with self.subTest(payload=payload):
                    result = invoke(server, tools.t3_thread_set_mode, payload)
                    self.assertEqual(result["error_code"], "invalid_input")
            self.assertEqual(server.requests, [])

        active = detail_snapshot(
            sequence=1,
            turn=latest_turn(turn_id="turn-1", state="running"),
            current_session=session(status="running", active_turn_id="turn-1"),
        )
        with LoopbackServer(
            [Response(value=active), Response(status=400, value={"code": "invalid"})]
        ) as server:
            failed = invoke(
                server,
                tools.t3_thread_set_mode,
                {"thread_id": "thread-1", "runtime_mode": "full-access"},
            )
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["details"]["active_turn_unchanged"], "turn-1")
        self.assertIn("approval already pending", failed["details"]["warning"])
        self.assertIn("full-access permits", failed["details"]["warning"])
        self.assertEqual(
            failed["details"]["warnings"],
            [tools.FULL_ACCESS_WARNING, tools.PENDING_APPROVAL_WARNING],
        )

    def test_plan_to_build_orders_native_mode_and_provenance_turn(self) -> None:
        commands: list[dict] = []
        plan = proposed_plan(plan_markdown="  ## Exact plan\n\nDo the work.  ")
        before = detail_snapshot(sequence=1, proposed_plans=[plan])
        before["thread"]["interactionMode"] = "default"
        before["thread"]["runtimeMode"] = "full-access"

        def mode_readback(request: dict) -> Response:
            detail = detail_snapshot(sequence=1, proposed_plans=[plan])
            detail["thread"]["interactionMode"] = "default"
            detail["thread"]["runtimeMode"] = "full-access"
            return Response(value=detail)

        def implementation_readback(request: dict) -> Response:
            turn_command = commands[1]
            implemented = proposed_plan(
                plan_markdown=plan["planMarkdown"],
                implemented_at="2026-08-21T12:01:00Z",
                implementation_thread_id="thread-1",
            )
            detail = detail_snapshot(
                sequence=2,
                messages=[
                    message(
                        message_id=turn_command["message"]["messageId"],
                        text=turn_command["message"]["text"],
                    )
                ],
                turn=latest_turn(
                    turn_id="turn-1",
                    state="running",
                    source_plan=source_proposed_plan(),
                ),
                proposed_plans=[implemented],
            )
            detail["thread"]["interactionMode"] = "default"
            detail["thread"]["runtimeMode"] = "full-access"
            return Response(value=detail)

        with LoopbackServer(
            [
                Response(value=before),
                captured_dispatch(commands),
                mode_readback,
                captured_dispatch(commands),
                implementation_readback,
            ]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_implement_plan,
                {"thread_id": "thread-1", "plan_id": "plan-1"},
            )
        self.assertTrue(result["ok"])
        self.assertEqual(
            [item["type"] for item in commands],
            ["thread.interaction-mode.set", "thread.turn.start"],
        )
        self.assertEqual(commands[0]["interactionMode"], "default")
        self.assertNotEqual(commands[0]["commandId"], commands[1]["commandId"])
        self.assertEqual(
            commands[1]["message"]["text"],
            "PLEASE IMPLEMENT THIS PLAN:\n## Exact plan\n\nDo the work.",
        )
        self.assertEqual(
            commands[1]["sourceProposedPlan"],
            {"threadId": "thread-1", "planId": "plan-1"},
        )
        self.assertEqual(commands[1]["interactionMode"], "default")
        self.assertEqual(result["mode_command_id"], commands[0]["commandId"])
        self.assertEqual(result["turn_command_id"], commands[1]["commandId"])
        self.assertEqual(
            result["latest_turn"]["sourceProposedPlan"],
            commands[1]["sourceProposedPlan"],
        )
        self.assertIn("at-most-once", result["race_semantics"])
        self.assertIn("modify or delete files without approval", result["warning"])

    def test_plan_halts_when_mode_is_accepted_pending_projection(self) -> None:
        commands: list[dict] = []
        plan = proposed_plan()
        before = with_projection_fields(
            detail_snapshot(sequence=3, proposed_plans=[plan]),
            thread_sequence=3,
        )
        before["thread"]["interactionMode"] = "plan"
        stale_projection = with_projection_fields(
            detail_snapshot(sequence=4, proposed_plans=[plan]),
            thread_sequence=4,
        )
        stale_projection["thread"]["interactionMode"] = "default"

        def dispatch(request: dict) -> Response:
            commands.append(json.loads(request["body"]))
            return Response(value={"sequence": 5})

        with LoopbackServer(
            [Response(value=before), dispatch] + [Response(value=stale_projection)] * 12
        ) as server:
            transport = tools.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.1,
                mutation_timeout=0.08,
                mutation_poll_timeout=0.04,
                poll_interval=0.01,
                retry_backoff=(),
            )
            with mock.patch.object(tools, "_make_client", return_value=transport):
                result = invoke(
                    server,
                    tools.t3_thread_implement_plan,
                    {"thread_id": "thread-1", "plan_id": "plan-1"},
                )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["action"],
            "plan_mode_transition_accepted_pending_projection",
        )
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(
            result["reconciliation"],
            {
                "tool": "t3_thread_read",
                "arguments": {
                    "thread_id": "thread-1",
                    "view": "raw",
                    "turn_limit": 150,
                },
                "required_snapshot_sequence": 5,
            },
        )
        self.assertEqual([command["type"] for command in commands], ["thread.interaction-mode.set"])
        self.assertEqual(
            [request["method"] for request in server.requests].count("POST"),
            1,
        )

    def test_plan_mode_boundary_requires_accepted_sequence_after_ambiguity(self) -> None:
        for initial_mode in ("default", "plan"):
            with self.subTest(initial_mode=initial_mode):
                commands: list[dict] = []
                plan = proposed_plan()
                before = detail_snapshot(sequence=1, proposed_plans=[plan])
                before["thread"]["interactionMode"] = initial_mode
                after_ambiguous = detail_snapshot(sequence=1, proposed_plans=[plan])
                after_ambiguous["thread"]["interactionMode"] = "default"
                after_accepted = detail_snapshot(sequence=2, proposed_plans=[plan])
                after_accepted["thread"]["interactionMode"] = "default"

                def capture_with(response: Response):
                    def responder(request: dict) -> Response:
                        commands.append(json.loads(request["body"]))
                        return response

                    return responder

                def implementation_readback(request: dict) -> Response:
                    turn_command = commands[2]
                    implemented = proposed_plan(
                        implemented_at="2026-08-21T12:01:00Z",
                        implementation_thread_id="thread-1",
                    )
                    detail = detail_snapshot(
                        sequence=3,
                        messages=[
                            message(
                                message_id=turn_command["message"]["messageId"],
                                text=turn_command["message"]["text"],
                            )
                        ],
                        turn=latest_turn(
                            turn_id="turn-1",
                            state="running",
                            source_plan=source_proposed_plan(),
                        ),
                        proposed_plans=[implemented],
                    )
                    detail["thread"]["interactionMode"] = "default"
                    return Response(value=detail)

                with LoopbackServer(
                    [
                        Response(value=before),
                        capture_with(
                            Response(value={"sequence": 1}, delay_before_body=0.08)
                        ),
                        Response(value=after_ambiguous),
                        capture_with(Response(value={"sequence": 2})),
                        Response(value=after_accepted),
                        capture_with(Response(value={"sequence": 3})),
                        implementation_readback,
                    ]
                ) as server:
                    transport = tools.T3Client(
                        server.base_url,
                        server.token,
                        request_timeout=0.03,
                        mutation_timeout=0.5,
                        mutation_poll_timeout=0.1,
                        retry_backoff=(),
                    )
                    with mock.patch.object(tools, "_make_client", return_value=transport):
                        result = invoke(
                            server,
                            tools.t3_thread_implement_plan,
                            {"thread_id": "thread-1", "plan_id": "plan-1"},
                        )

                self.assertTrue(result["ok"])
                self.assertEqual(
                    [command["type"] for command in commands],
                    [
                        "thread.interaction-mode.set",
                        "thread.interaction-mode.set",
                        "thread.turn.start",
                    ],
                )
                self.assertEqual(commands[0], commands[1])
                self.assertEqual(result["mode_dispatch_sequence"], 2)
                self.assertEqual(result["mode_dispatch_attempts"], 2)
                self.assertTrue(result["mode_recovered_after_ambiguous_dispatch"])
                self.assertEqual(
                    [request["method"] for request in server.requests],
                    ["GET", "POST", "GET", "POST", "GET", "POST", "GET"],
                )

    def test_plan_mode_boundary_without_accepted_sequence_never_starts_turn(self) -> None:
        commands: list[dict] = []
        plan = proposed_plan()
        before = detail_snapshot(sequence=1, proposed_plans=[plan])
        before["thread"]["interactionMode"] = "default"
        ambiguous_readback = detail_snapshot(sequence=1, proposed_plans=[plan])
        ambiguous_readback["thread"]["interactionMode"] = "default"

        def ambiguous_dispatch(request: dict) -> Response:
            commands.append(json.loads(request["body"]))
            return Response(value={"sequence": 1}, delay_before_body=0.08)

        with LoopbackServer(
            [
                Response(value=before),
                ambiguous_dispatch,
                Response(value=ambiguous_readback),
            ]
        ) as server:
            transport = tools.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.03,
                mutation_timeout=0.2,
                mutation_poll_timeout=0.1,
                dispatch_attempts=1,
                retry_backoff=(),
            )
            with mock.patch.object(tools, "_make_client", return_value=transport):
                result = invoke(
                    server,
                    tools.t3_thread_implement_plan,
                    {"thread_id": "thread-1", "plan_id": "plan-1"},
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "mutation_ambiguous")
        self.assertEqual(
            result["details"]["workflow_phase"], "interaction_mode_transition"
        )
        self.assertEqual(
            [command["type"] for command in commands],
            ["thread.interaction-mode.set"],
        )
        self.assertEqual(
            [request["method"] for request in server.requests],
            ["GET", "POST", "GET"],
        )

    def test_plan_to_build_preconditions_fail_before_dispatch(self) -> None:
        already = proposed_plan(
            implemented_at="2026-08-21T12:01:00Z",
            implementation_thread_id="thread-1",
        )
        running = detail_snapshot(
            sequence=1,
            turn=latest_turn(turn_id="turn-1", state="running"),
            current_session=session(status="running", active_turn_id="turn-1"),
            proposed_plans=[proposed_plan()],
        )
        cases = (
            detail_snapshot(sequence=1, proposed_plans=[]),
            detail_snapshot(sequence=1, proposed_plans=[already]),
            running,
        )
        background = detail_snapshot(
            sequence=1,
            proposed_plans=[proposed_plan()],
        )
        background["thread"]["backgroundLiveness"] = "working"
        cases += (background,)
        for detail in cases:
            with self.subTest(plan_count=len(detail["thread"]["proposedPlans"])):
                with LoopbackServer([Response(value=detail)]) as server:
                    result = invoke(
                        server,
                        tools.t3_thread_implement_plan,
                        {"thread_id": "thread-1", "plan_id": "plan-1"},
                    )
                self.assertEqual(result["error_code"], "conflict")
                self.assertEqual([item["method"] for item in server.requests], ["GET"])

    def test_plan_rechecks_background_liveness_after_mode_transition(self) -> None:
        commands: list[dict] = []
        plan = proposed_plan()
        before = detail_snapshot(sequence=1, proposed_plans=[plan])
        before["thread"]["interactionMode"] = "plan"
        after_mode = detail_snapshot(sequence=2, proposed_plans=[plan])
        after_mode["thread"]["interactionMode"] = "default"
        after_mode["thread"]["backgroundLiveness"] = "monitoring"

        with LoopbackServer(
            [
                Response(value=before),
                captured_dispatch(commands),
                Response(value=after_mode),
            ]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_implement_plan,
                {"thread_id": "thread-1", "plan_id": "plan-1"},
            )

        self.assertEqual(result["error_code"], "conflict")
        self.assertEqual(
            [command["type"] for command in commands],
            ["thread.interaction-mode.set"],
        )
        self.assertEqual(
            [request["method"] for request in server.requests],
            ["GET", "POST", "GET"],
        )

    def test_plan_second_phase_accepted_pending_reports_completed_mode_without_rollback(self) -> None:
        commands: list[dict] = []
        plan = proposed_plan()
        before = detail_snapshot(sequence=1, proposed_plans=[plan])
        after_mode = detail_snapshot(sequence=1, proposed_plans=[plan])
        after_mode["thread"]["interactionMode"] = "default"
        with LoopbackServer(
            [
                Response(value=before),
                captured_dispatch(commands),
                Response(value=after_mode),
                captured_dispatch(commands),
                Response(status=400, value={"code": "invalid"}),
            ]
        ) as server:
            result = invoke(
                server,
                tools.t3_thread_implement_plan,
                {"thread_id": "thread-1", "plan_id": "plan-1"},
            )
        self.assertTrue(result["ok"])
        self.assertEqual(len(commands), 2)
        self.assertEqual(result["action"], "plan_implementation_accepted_pending_projection")
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(result["mode_command_id"], commands[0]["commandId"])
        self.assertEqual([item["method"] for item in server.requests].count("POST"), 2)

    def test_plan_turn_uuid_failure_reports_verified_mode_phase(self) -> None:
        commands: list[dict] = []
        mode_command_id = str(uuid.uuid4())
        plan = proposed_plan()
        before = detail_snapshot(sequence=1, proposed_plans=[plan])
        after_mode = detail_snapshot(sequence=1, proposed_plans=[plan])
        after_mode["thread"]["interactionMode"] = "default"
        with LoopbackServer(
            [
                Response(value=before),
                captured_dispatch(commands),
                Response(value=after_mode),
            ]
        ) as server:
            with mock.patch.object(
                tools.T3Client,
                "new_uuid4",
                side_effect=[mode_command_id, tools.T3ClientError()],
            ):
                result = invoke(
                    server,
                    tools.t3_thread_implement_plan,
                    {"thread_id": "thread-1", "plan_id": "plan-1"},
                )
        self.assertEqual(result["error_code"], "internal_error")
        self.assertEqual(result["details"]["workflow_phase"], "implementation_turn")
        self.assertEqual(result["details"]["completed_phase"], "interaction_mode_set")
        self.assertEqual(
            result["details"]["verified_mode_command_id"], mode_command_id
        )
        self.assertIn("at-most-once", result["details"]["race_semantics"])
        self.assertEqual([item["method"] for item in server.requests].count("POST"), 1)

    def test_interrupt_success_and_newer_turn_race(self) -> None:
        commands: list[dict] = []
        running = session(status="running", active_turn_id="turn-1")
        before = detail_snapshot(
            sequence=1,
            turn=latest_turn(turn_id="turn-1", state="running"),
            current_session=running,
        )
        after = detail_snapshot(
            sequence=1,
            turn=latest_turn(turn_id="turn-1", state="interrupted"),
            current_session=session(status="interrupted", active_turn_id=None),
        )
        with LoopbackServer([Response(value=before), captured_dispatch(commands), Response(value=after)]) as server:
            success = invoke(server, tools.t3_turn_interrupt, {"thread_id": "thread-1"})
        self.assertTrue(success["ok"])
        self.assertEqual(commands[0]["turnId"], "turn-1")
        self.assertEqual(commands[0]["type"], "thread.turn.interrupt")

        newer = detail_snapshot(
            sequence=1,
            turn=latest_turn(turn_id="turn-2", state="running"),
            current_session=session(status="running", active_turn_id="turn-2"),
        )
        with LoopbackServer([Response(value=before), captured_dispatch([]), Response(value=newer)]) as server:
            race = invoke(server, tools.t3_turn_interrupt, {"thread_id": "thread-1"})
        self.assertEqual(race["error_code"], "concurrent_state_change")
        self.assertTrue(race["outcome_ambiguous"])
        self.assertIn("command_id", race)

    def test_interrupt_conflict_and_stop_state_machine(self) -> None:
        inactive = detail_snapshot(sequence=1, current_session=session(status="ready"))
        already = detail_snapshot(sequence=1, current_session=session(status="stopped"))
        with LoopbackServer([Response(value=inactive), Response(value=already)]) as server:
            conflict = invoke(server, tools.t3_turn_interrupt, {"thread_id": "thread-1"})
            stopped = invoke(server, tools.t3_session_stop, {"thread_id": "thread-1"})
            self.assertEqual(conflict["error_code"], "conflict")
            self.assertEqual(stopped["action"], "already_stopped")
            self.assertIsNone(stopped["command_id"])
            self.assertEqual([item["method"] for item in server.requests], ["GET", "GET"])

        commands: list[dict] = []
        running = detail_snapshot(
            sequence=1,
            current_session=session(status="running", active_turn_id="turn-1"),
        )
        stopped_detail = detail_snapshot(
            sequence=1,
            current_session=session(status="stopped", active_turn_id=None),
        )
        with LoopbackServer(
            [Response(value=running), captured_dispatch(commands), Response(value=stopped_detail)]
        ) as server:
            result = invoke(server, tools.t3_session_stop, {"thread_id": "thread-1"})
        self.assertTrue(result["ok"])
        self.assertEqual(commands[0]["type"], "thread.session.stop")
        self.assertNotIn("onlyIfSettled", commands[0])


if __name__ == "__main__":
    unittest.main()
