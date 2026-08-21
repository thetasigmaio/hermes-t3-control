from __future__ import annotations

import copy
import json
import unittest
import uuid
from unittest import mock

import schemas
import tools
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
        self, base_url: str, *, default_runtime_mode: object = None
    ) -> None:
        self.base_url = base_url
        self.default_runtime_mode = default_runtime_mode

    def get_config(self, key: str, default: object = None) -> object:
        if key == "base_url":
            return self.base_url
        if key == "default_runtime_mode":
            return self.default_runtime_mode
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
    with mock.patch.object(
        tools,
        "_profile_secret",
        return_value=server.token if credential is None else credential,
    ):
        return json.loads(handler(args, **kwargs))


def captured_dispatch(store: list[dict]):
    def responder(request: dict) -> Response:
        command = json.loads(request["body"])
        store.append(command)
        return Response(value={"sequence": len(store)})

    return responder


class ReadToolTests(unittest.TestCase):
    def test_threads_and_detail_reads(self) -> None:
        shell = shell_snapshot(sequence=3)
        detail = detail_snapshot(sequence=4)
        with LoopbackServer([Response(value=shell), Response(value=detail)]) as server:
            listed = invoke(server, tools.t3_threads, {}, ignored_context=True)
            read = invoke(
                server,
                tools.t3_thread_read,
                {"thread_id": "thread-1", "turn_limit": 7, "before_cursor": "older"},
            )
            self.assertTrue(listed["ok"])
            self.assertEqual(listed["shell"]["snapshotSequence"], 3)
            self.assertTrue(read["ok"])
            self.assertEqual(
                server.requests[1]["path"],
                "/api/orchestration/threads/thread-1?turnLimit=7&beforeCursor=older",
            )

    def test_validation_and_secret_fail_closed_before_http(self) -> None:
        with LoopbackServer([]) as server:
            invalid = invoke(server, tools.t3_thread_read, {"thread_id": "x", "unknown": True})
            incomplete = invoke(
                server,
                tools.t3_thread_create,
                {"project_id": "project-1", "title": "Thread", "instance_id": "codex"},
            )
            handler = tools.bind_handler(FakeContext(server.base_url), tools.t3_threads)
            with mock.patch.object(tools, "_profile_secret", side_effect=RuntimeError("hidden")):
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
                    with mock.patch.object(tools, "_profile_secret", return_value=invalid_secret):
                        self.assertFalse(tools.check_t3_available())
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


class PublicArgumentPreflightTests(unittest.TestCase):
    def test_active_credential_in_every_public_string_path_makes_no_http_request(self) -> None:
        base_args = {
            "t3_thread_read": {
                "thread_id": "thread-1",
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
            }
        )
        mode_credentials = {
            ("t3_thread_create", ("runtime_mode",)): "auto",
            ("t3_thread_create", ("interaction_mode",)): "plan",
            ("t3_thread_set_mode", ("runtime_mode",)): "auto",
            ("t3_thread_set_mode", ("interaction_mode",)): "plan",
        }
        self.assertEqual(len(public_string_paths), 22)

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
                if (
                    tool_name == "t3_thread_set_mode"
                    and field_name == "interaction_mode"
                ):
                    payload.pop("runtime_mode")
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
                {"thread_id": "thread-1"},
            )
            self.assertTrue(omitted["ok"])
            self.assertEqual(
                server.requests[0]["path"],
                "/api/orchestration/threads/thread-1?turnLimit=20",
            )


class MutationToolTests(unittest.TestCase):
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
            first = invoke(server, tools.t3_thread_send, {"thread_id": "thread-1", "message": " one "})
            second = invoke(server, tools.t3_thread_send, {"thread_id": "thread-1", "message": "two"})
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
                {"thread_id": "thread-1", "message": "Continue"},
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

    def test_plan_to_build_orders_native_mode_and_provenance_turn(self) -> None:
        commands: list[dict] = []
        plan = proposed_plan(plan_markdown="  ## Exact plan\n\nDo the work.  ")
        before = detail_snapshot(sequence=1, proposed_plans=[plan])
        before["thread"]["interactionMode"] = "default"

        def mode_readback(request: dict) -> Response:
            detail = detail_snapshot(sequence=1, proposed_plans=[plan])
            detail["thread"]["interactionMode"] = "default"
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

    def test_plan_second_phase_failure_reports_completed_mode_without_rollback(self) -> None:
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
        self.assertFalse(result["ok"])
        self.assertEqual(len(commands), 2)
        self.assertEqual(result["details"]["workflow_phase"], "implementation_turn")
        self.assertEqual(result["details"]["completed_phase"], "interaction_mode_set")
        self.assertEqual(
            result["details"]["verified_mode_command_id"], commands[0]["commandId"]
        )
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
