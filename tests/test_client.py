from __future__ import annotations

import copy
import json
import time
import unittest
import uuid
from types import MappingProxyType
from unittest import mock

import client
from tests.support import (
    NOW,
    LoopbackServer,
    Response,
    detail_snapshot,
    latest_turn,
    message,
    proposed_plan,
    shell_snapshot,
    source_proposed_plan,
)


class OriginAndInputTests(unittest.TestCase):
    def test_numeric_loopback_origins_only(self) -> None:
        token = uuid.uuid4().hex
        accepted = client.T3Client("http://127.0.0.1:9876/", token)
        self.assertEqual(accepted.origin, "http://127.0.0.1:9876")
        ipv6 = client.T3Client("http://[::1]:9876", token)
        self.assertEqual(ipv6.origin, "http://[::1]:9876")
        for value in (
            "http://localhost:9876",
            "http://192.0.2.1:9876",
            "ftp://127.0.0.1:9876",
            "http://user@127.0.0.1:9876",
            "http://127.0.0.1:9876/path",
            "http://127.0.0.1:9876/?query=1",
            "http://127.0.0.1:9876/#fragment",
            "http://127.0.0.1:0",
        ):
            with self.subTest(value=value), self.assertRaises(client.ConfigurationError):
                client.T3Client(value, token)

    def test_constructor_overrides_cannot_exceed_production_maxima(self) -> None:
        token = uuid.uuid4().hex
        self.assertEqual(client.MUTATION_POLL_SECONDS, 15.0)
        self.assertEqual(
            client.T3Client("http://127.0.0.1", token).mutation_poll_timeout,
            15.0,
        )
        with self.assertRaises(client.ConfigurationError):
            client.T3Client("http://127.0.0.1", token, request_timeout=10.01)
        with self.assertRaises(client.ConfigurationError):
            client.T3Client("http://127.0.0.1", token, response_limit=client.MAX_RESPONSE_BYTES + 1)
        with self.assertRaises(client.ConfigurationError):
            client.T3Client("http://127.0.0.1", token, dispatch_attempts=True)
        with self.assertRaises(client.ConfigurationError):
            client.T3Client(
                "http://127.0.0.1",
                token,
                cumulative_response_limit=client.MAX_CUMULATIVE_RESPONSE_BYTES + 1,
            )

    def test_credentials_with_outer_whitespace_fail_before_http(self) -> None:
        with LoopbackServer([]) as server:
            for token in ("   ", f" {server.token}", f"{server.token}\n"):
                with self.subTest(token_shape=(len(token), token == token.strip())):
                    with self.assertRaises(client.ConfigurationError):
                        client.T3Client(server.base_url, token)
            self.assertEqual(server.requests, [])

    def test_credentials_reject_internal_whitespace_controls_and_non_ascii_before_http(self) -> None:
        with LoopbackServer([]) as server:
            for token in (
                f"{server.token}\r\nInjected: yes",
                f"{server.token} internal",
                f"{server.token}\tinternal",
                f"{server.token}\N{LATIN SMALL LETTER E WITH ACUTE}",
            ):
                with self.subTest(token_shape=(len(token), token.isascii())):
                    self.assertFalse(client.is_valid_bearer_credential(token))
                    with self.assertRaises(client.ConfigurationError):
                        client.T3Client(server.base_url, token)
            self.assertEqual(server.requests, [])

    def test_message_utf16_boundary_and_boolean_integer_rejection(self) -> None:
        astral = "\U0001f642"
        self.assertEqual(client.normalize_message(astral * 60_000), astral * 60_000)
        with self.assertRaises(client.InvalidInputError):
            client.normalize_message(astral * 60_001)
        with self.assertRaises(client.InvalidInputError):
            client.validate_turn_limit(True)

    def test_invalid_thread_input_makes_no_request(self) -> None:
        with LoopbackServer([]) as server:
            transport = client.T3Client(server.base_url, server.token)
            with self.assertRaises(client.InvalidInputError):
                transport.get_thread(" " * 3)
            with self.assertRaises(client.InvalidInputError):
                transport.get_thread("thread", turn_limit=151)
            self.assertEqual(server.requests, [])

    def test_active_token_in_normalized_thread_or_cursor_fails_before_http(self) -> None:
        with LoopbackServer([]) as server:
            transport = client.T3Client(server.base_url, server.token)
            operations = (
                (
                    lambda: transport.get_thread(f"  thread-{server.token}  "),
                    "thread_id contains the active credential.",
                ),
                (
                    lambda: transport.get_thread(
                        "thread-1", before_cursor=f"  cursor-{server.token}  "
                    ),
                    "before_cursor contains the active credential.",
                ),
            )
            for operation, expected_message in operations:
                with self.assertRaises(client.InvalidInputError) as caught:
                    operation()
                self.assertEqual(caught.exception.safe_message, expected_message)
                encoded = json.dumps(caught.exception.to_dict())
                self.assertFalse(
                    server.token in encoded,
                    "The sanitized input error reflected the active credential.",
                )
            self.assertEqual(len(server.requests), 0)

    def test_thread_id_is_one_encoded_segment_and_pagination_is_bounded(self) -> None:
        with LoopbackServer([Response(value=detail_snapshot(thread_id="a/b ?"))]) as server:
            transport = client.T3Client(server.base_url, server.token)
            detail = transport.get_thread(" a/b ? ", turn_limit=7, before_cursor="old / cursor")
            self.assertEqual(detail["thread"]["id"], "a/b ?")
            self.assertEqual(
                server.requests[0]["path"],
                "/api/orchestration/threads/a%2Fb%20%3F?turnLimit=7&beforeCursor=old+%2F+cursor",
            )

    def test_endpoint_allowlist_rejects_every_other_route(self) -> None:
        token = uuid.uuid4().hex
        transport = client.T3Client("http://127.0.0.1:9", token)
        for method, path in (
            ("GET", "/api/orchestration/snapshot"),
            ("POST", "/api/orchestration/shell"),
            ("GET", "/api/orchestration/threads/a/b?turnLimit=20"),
            ("GET", "/api/orchestration/threads/a?beforeCursor=x"),
            ("GET", "http://127.0.0.1/api/orchestration/shell"),
        ):
            with self.subTest(method=method, path=path), self.assertRaises(client.InvalidInputError):
                transport._request_json(method, path)


class ReadAndSchemaTests(unittest.TestCase):
    def test_environment_descriptor_is_allowlisted_bounded_and_additive(self) -> None:
        descriptor = {
            "environmentId": "environment-test",
            "serverVersion": "0.0.34-nightly.20260820.1141",
            "futureField": {"preserved": True},
        }
        with LoopbackServer([Response(value=descriptor)]) as server:
            observed = client.T3Client(
                server.base_url, server.token
            ).get_environment_descriptor()

        self.assertEqual(observed, descriptor)
        self.assertEqual(
            server.requests[0]["path"], "/.well-known/t3/environment"
        )
        self.assertEqual(
            server.requests[0]["authorization"], f"Bearer {server.token}"
        )

        for field, value in (
            ("environmentId", ""),
            ("environmentId", " environment-test"),
            ("environmentId", "x" * (client.MAX_IDENTIFIER_CHARS + 1)),
            ("serverVersion", "bad\nversion"),
            ("serverVersion", 1),
        ):
            invalid = dict(descriptor, **{field: value})
            with self.subTest(field=field, value_type=type(value).__name__), self.assertRaises(
                client.ResponseSchemaError
            ):
                client.validate_environment_descriptor(invalid)

    def test_environment_descriptor_probe_is_strictly_unauthenticated(self) -> None:
        descriptor = {
            "environmentId": "environment-test",
            "serverVersion": "0.0.34-nightly.20260820.1141",
        }
        with LoopbackServer([Response(value=descriptor)]) as server:
            observed = client.probe_environment_descriptor(server.base_url)

        self.assertEqual(observed, descriptor)
        self.assertEqual(
            server.requests,
            [
                {
                    "method": "GET",
                    "path": "/.well-known/t3/environment",
                    "authorization": None,
                    "body": b"",
                }
            ],
        )

    def test_environment_descriptor_uses_its_one_mib_byte_limit(self) -> None:
        oversized = {
            "environmentId": "environment-test",
            "serverVersion": "0.0.34-nightly.20260820.1141",
            "padding": "x" * client.MAX_ENVIRONMENT_RESPONSE_BYTES,
        }
        with LoopbackServer([Response(value=oversized)]) as server:
            transport = client.T3Client(server.base_url, server.token)
            with self.assertRaises(client.ResponseTooLargeError):
                transport.get_environment_descriptor()

    def test_shell_and_detail_preserve_additive_fields_and_authenticate(self) -> None:
        shell = shell_snapshot(extra=True)
        detail = detail_snapshot(sequence=2)
        detail["futureDetailField"] = {"value": 1}
        with LoopbackServer([Response(value=shell), Response(value=detail)]) as server:
            transport = client.T3Client(server.base_url, server.token)
            self.assertEqual(transport.get_shell()["futureTopLevelField"], [1, 2, 3])
            self.assertEqual(transport.get_thread("thread-1")["futureDetailField"], {"value": 1})
            self.assertEqual(
                [request["authorization"] for request in server.requests],
                [f"Bearer {server.token}", f"Bearer {server.token}"],
            )

    def test_required_schema_fields_and_types_are_enforced(self) -> None:
        bad_shell = shell_snapshot()
        bad_shell["snapshotSequence"] = True
        bad_detail = detail_snapshot()
        del bad_detail["thread"]["messages"]
        bad_dispatch = {"sequence": -1}
        for validator, value in (
            (client.validate_shell_snapshot, bad_shell),
            (client.validate_thread_detail, bad_detail),
            (client.validate_dispatch_response, bad_dispatch),
        ):
            with self.subTest(validator=validator.__name__), self.assertRaises(
                client.ResponseSchemaError
            ):
                validator(value)

    def test_detail_validates_plans_and_latest_turn_provenance(self) -> None:
        unimplemented = proposed_plan(plan_id="plan-unimplemented", turn_id=None)
        implemented = proposed_plan(
            plan_id="plan-implemented",
            implemented_at=NOW,
            implementation_thread_id="thread-1",
        )
        source = source_proposed_plan(plan_id="plan-implemented")
        detail = detail_snapshot(
            turn=latest_turn(source_plan=source),
            proposed_plans=[unimplemented, implemented],
        )

        with LoopbackServer([Response(value=detail)]) as server:
            validated = client.T3Client(server.base_url, server.token).get_thread(
                "thread-1"
            )

        self.assertEqual(validated, detail)
        self.assertEqual(
            validated["thread"]["proposedPlans"], [unimplemented, implemented]
        )
        self.assertEqual(
            validated["thread"]["latestTurn"]["sourceProposedPlan"], source
        )

    def test_plan_and_provenance_additive_fields_are_preserved(self) -> None:
        plan = proposed_plan()
        plan["futurePlanField"] = {"kept": True}
        source = {
            "threadId": "thread-1",
            "planId": "plan-1",
            "futureSourceField": [1, 2, 3],
        }
        turn = latest_turn(source_plan=source)
        turn["futureTurnField"] = "kept"
        detail = detail_snapshot(turn=turn, proposed_plans=[plan])

        validated = client.validate_thread_detail(detail)

        self.assertIs(validated, detail)
        self.assertEqual(
            validated["thread"]["proposedPlans"][0]["futurePlanField"],
            {"kept": True},
        )
        self.assertEqual(
            validated["thread"]["latestTurn"]["sourceProposedPlan"][
                "futureSourceField"
            ],
            [1, 2, 3],
        )
        self.assertEqual(validated["thread"]["latestTurn"]["futureTurnField"], "kept")

    def test_optional_agent_projection_fields_are_validated_when_present(self) -> None:
        shell = shell_snapshot()
        compact = shell["threads"][0]
        compact.update(
            {
                "settledAt": None,
                "latestUserMessageAt": NOW,
                "hasPendingApprovals": False,
                "hasPendingUserInput": True,
                "hasActionableProposedPlan": False,
                "backgroundLiveness": "working",
                "planProgress": None,
            }
        )
        self.assertIs(client.validate_shell_snapshot(shell), shell)

        detail = detail_snapshot()
        detail["thread"]["activities"] = [
            {
                "id": "activity-1",
                "kind": "approval.requested",
                "summary": "Approval requested",
                "payload": {"requestId": "request-1", "future": True},
                "turnId": None,
                "sequence": 1,
                "createdAt": NOW,
                "futureActivityField": [1, 2, 3],
            }
        ]
        self.assertIs(client.validate_thread_detail(detail), detail)

        invalid_shell_fields = (
            ("settledAt", "not-a-timestamp"),
            ("hasPendingApprovals", 1),
            ("backgroundLiveness", []),
            ("planProgress", "working"),
        )
        for field, value in invalid_shell_fields:
            invalid = copy.deepcopy(shell)
            invalid["threads"][0][field] = value
            with self.subTest(field=field), self.assertRaises(
                client.ResponseSchemaError
            ):
                client.validate_shell_snapshot(invalid)

        for field, value in (
            ("id", ""),
            ("kind", None),
            ("summary", "  "),
            ("payload", []),
            ("turnId", 1),
            ("sequence", True),
            ("createdAt", "not-a-timestamp"),
        ):
            invalid = copy.deepcopy(detail)
            invalid["thread"]["activities"][0][field] = value
            with self.subTest(activity_field=field), self.assertRaises(
                client.ResponseSchemaError
            ):
                client.validate_thread_detail(invalid)

    def test_background_liveness_matches_live_nullable_enum_projection(self) -> None:
        for value in (None, "working", "monitoring"):
            with self.subTest(value=value):
                shell = shell_snapshot()
                shell["threads"][0]["backgroundLiveness"] = value
                self.assertIs(client.validate_shell_snapshot(shell), shell)

        for value in ({"state": "working"}, "idle", False):
            with self.subTest(invalid=value):
                shell = shell_snapshot()
                shell["threads"][0]["backgroundLiveness"] = value
                with self.assertRaises(client.ResponseSchemaError):
                    client.validate_shell_snapshot(shell)

    def test_activity_sequence_matches_live_optional_projection(self) -> None:
        detail = detail_snapshot()
        detail["thread"]["activities"] = [
            {
                "id": "activity-live",
                "tone": "info",
                "kind": "checkpoint.captured",
                "summary": "Checkpoint captured",
                "payload": {},
                "turnId": "turn-1",
                "createdAt": NOW,
            }
        ]
        self.assertIs(client.validate_thread_detail(detail), detail)

        detail["thread"]["activities"][0]["sequence"] = True
        with self.assertRaises(client.ResponseSchemaError):
            client.validate_thread_detail(detail)

    def test_detail_requires_proposed_plans_and_exact_plan_fields(self) -> None:
        missing_plans = detail_snapshot()
        del missing_plans["thread"]["proposedPlans"]
        invalid_plans: list[object] = [
            None,
            {},
            ["not-an-object"],
        ]
        malformed_fields = (
            ("id", ""),
            ("turnId", 1),
            ("planMarkdown", "  "),
            ("implementedAt", "not-a-timestamp"),
            ("implementationThreadId", ""),
            ("createdAt", "2026-08-21T12:00:00"),
            ("updatedAt", None),
        )
        required_fields = (
            "id",
            "turnId",
            "planMarkdown",
            "implementedAt",
            "implementationThreadId",
            "createdAt",
            "updatedAt",
        )

        with self.assertRaises(client.ResponseSchemaError):
            client.validate_thread_detail(missing_plans)
        for plans in invalid_plans:
            invalid = detail_snapshot()
            invalid["thread"]["proposedPlans"] = plans
            with self.subTest(plans=plans), self.assertRaises(client.ResponseSchemaError):
                client.validate_thread_detail(invalid)
        for field in required_fields:
            invalid = detail_snapshot(proposed_plans=[proposed_plan()])
            del invalid["thread"]["proposedPlans"][0][field]
            with self.subTest(missing_field=field), self.assertRaises(
                client.ResponseSchemaError
            ):
                client.validate_thread_detail(invalid)
        for field, value in malformed_fields:
            invalid = detail_snapshot(proposed_plans=[proposed_plan()])
            invalid["thread"]["proposedPlans"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(
                client.ResponseSchemaError
            ):
                client.validate_thread_detail(invalid)

    def test_plan_implementation_fields_must_be_paired(self) -> None:
        cases = (
            proposed_plan(implemented_at=NOW, implementation_thread_id=None),
            proposed_plan(implemented_at=None, implementation_thread_id="thread-1"),
        )
        for plan in cases:
            invalid = detail_snapshot(proposed_plans=[plan])
            with self.subTest(plan=plan), self.assertRaises(client.ResponseSchemaError):
                client.validate_thread_detail(invalid)

    def test_latest_turn_source_plan_requires_exact_nonempty_identities(self) -> None:
        malformed_sources: tuple[object, ...] = (
            None,
            "not-an-object",
            {},
            {"threadId": "thread-1"},
            {"planId": "plan-1"},
            {"threadId": "", "planId": "plan-1"},
            {"threadId": "thread-1", "planId": "  "},
            {"threadId": 1, "planId": "plan-1"},
            {"threadId": "thread-1", "planId": False},
        )
        for source in malformed_sources:
            invalid = detail_snapshot(turn=latest_turn())
            invalid["thread"]["latestTurn"]["sourceProposedPlan"] = source
            with self.subTest(source=source), self.assertRaises(
                client.ResponseSchemaError
            ):
                client.validate_thread_detail(invalid)

    def test_thread_response_and_session_identity_must_match_exactly(self) -> None:
        wrong_target = detail_snapshot(thread_id="thread-other")
        with LoopbackServer([Response(value=wrong_target)]) as server:
            transport = client.T3Client(server.base_url, server.token)
            with self.assertRaises(client.ResponseSchemaError):
                transport.get_thread(" thread-1 ")

        mismatched_session = detail_snapshot()
        mismatched_session["thread"]["session"] = copy.deepcopy(
            shell_snapshot()["threads"][0]["session"]
        )
        mismatched_session["thread"]["session"]["threadId"] = "thread-other"
        with self.assertRaises(client.ResponseSchemaError):
            client.validate_thread_detail(mismatched_session)

    def test_success_responses_reject_reflected_active_token_recursively(self) -> None:
        token = f"active_{uuid.uuid4().hex}"
        reflected_key = shell_snapshot(extra=True)
        reflected_key["futureTopLevelField"] = {token: "safe"}
        reflected_value = shell_snapshot(extra=True)
        reflected_value["futureTopLevelField"] = [{"nested": token}]
        reflected_history = detail_snapshot(
            messages=[message(text=f"server reflected {token} into history")]
        )
        with LoopbackServer(
            [
                Response(value=reflected_key),
                Response(value=reflected_value),
                Response(value=reflected_history),
            ]
        ) as server:
            transport = client.T3Client(server.base_url, token)
            for operation in (
                transport.get_shell,
                transport.get_shell,
                lambda: transport.get_thread("thread-1"),
            ):
                with self.subTest(operation=operation), self.assertRaises(
                    client.ResponseSchemaError
                ) as caught:
                    operation()
                self.assertNotIn(token, json.dumps(caught.exception.to_dict()))

    def test_plan_and_provenance_responses_reject_reflected_active_token(self) -> None:
        token = f"runtime_{uuid.uuid4().hex}"
        reflected_plan = proposed_plan(plan_markdown=f"plan contains {token}")
        reflected_source = source_proposed_plan(plan_id=token)
        with LoopbackServer(
            [
                Response(value=detail_snapshot(proposed_plans=[reflected_plan])),
                Response(
                    value=detail_snapshot(
                        turn=latest_turn(source_plan=reflected_source)
                    )
                ),
            ]
        ) as server:
            transport = client.T3Client(server.base_url, token)
            for _ in range(2):
                with self.assertRaises(client.ResponseSchemaError) as caught:
                    transport.get_thread("thread-1")
                self.assertNotIn(token, json.dumps(caught.exception.to_dict()))

    def test_model_selection_options_require_canonical_array_items(self) -> None:
        valid = shell_snapshot()
        valid_options = valid["projects"][0]["defaultModelSelection"]["options"]
        self.assertEqual(
            valid_options,
            [
                {"id": "reasoning_effort", "value": "high"},
                {"id": "web_search", "value": True},
            ],
        )
        self.assertIs(client.validate_shell_snapshot(valid), valid)

        invalid_options = (
            {},
            ["not-an-object"],
            [{"id": "", "value": "high"}],
            [{"id": "reasoning_effort", "value": ""}],
            [{"id": "reasoning_effort", "value": 1}],
        )
        for options in invalid_options:
            invalid = copy.deepcopy(valid)
            invalid["projects"][0]["defaultModelSelection"]["options"] = options
            with self.subTest(options=options), self.assertRaises(client.ResponseSchemaError):
                client.validate_shell_snapshot(invalid)

    def test_rfc3339_timestamp_validation(self) -> None:
        for timestamp in (
            "2026-02-30T12:00:00Z",
            "2026-08-21T12:00:00",
            "20260821T120000Z",
            "2026-W34-5T12:00:00Z",
        ):
            invalid = shell_snapshot()
            invalid["updatedAt"] = timestamp
            with self.subTest(timestamp=timestamp), self.assertRaises(
                client.ResponseSchemaError
            ):
                client.validate_shell_snapshot(invalid)
        for timestamp in (
            "2026-08-21T12:00:00Z",
            "2026-08-21T14:00:00.123456789+02:00",
        ):
            valid = shell_snapshot()
            valid["updatedAt"] = timestamp
            with self.subTest(timestamp=timestamp):
                self.assertIs(client.validate_shell_snapshot(valid), valid)

    def test_malformed_success_and_response_size_have_distinct_errors(self) -> None:
        with LoopbackServer(
            [Response(body=b"not-json"), Response(body=b"x" * 65)]
        ) as server:
            transport = client.T3Client(server.base_url, server.token, response_limit=64)
            with self.assertRaises(client.ResponseSchemaError) as malformed:
                transport.get_shell()
            self.assertEqual(malformed.exception.error_code, "response_schema_error")
            with self.assertRaises(client.ResponseTooLargeError) as oversized:
                transport.get_shell()
            self.assertEqual(oversized.exception.error_code, "response_too_large")

    def test_standards_valid_nonfinite_float_is_rejected_during_json_parse(self) -> None:
        raw = json.dumps(shell_snapshot(extra=True)).replace(
            '"futureTopLevelField": [1, 2, 3]',
            '"futureTopLevelField": 1e999',
        ).encode("utf-8")
        self.assertIn(b"1e999", raw)
        with LoopbackServer([Response(body=raw)]) as server:
            transport = client.T3Client(server.base_url, server.token)
            with self.assertRaises(client.ResponseSchemaError) as caught:
                transport.get_shell()

        self.assertEqual(caught.exception.error_code, "response_schema_error")
        self.assertNotIn(server.token, json.dumps(caught.exception.to_dict()))

    def test_cumulative_response_budget_is_enforced_across_valid_reads(self) -> None:
        shell = shell_snapshot()
        encoded = json.dumps(shell).encode("utf-8")
        cumulative_limit = len(encoded) * 2 - 1
        with LoopbackServer(
            [Response(value=shell), Response(value=shell)]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                cumulative_response_limit=cumulative_limit,
            )
            self.assertEqual(transport.get_shell()["snapshotSequence"], 1)
            with self.assertRaises(client.ResponseBudgetExceededError) as caught:
                transport.get_shell()

        self.assertEqual(caught.exception.error_code, "response_budget_exhausted")
        self.assertNotIn(server.token, json.dumps(caught.exception.to_dict()))
        self.assertEqual([item["method"] for item in server.requests], ["GET", "GET"])

    def test_slow_or_stalled_body_obeys_absolute_chunk_deadline(self) -> None:
        payload = json.dumps(shell_snapshot()).encode("utf-8")
        midpoint = len(payload) // 2
        with LoopbackServer(
            [
                Response(
                    chunks=(payload[:midpoint], payload[midpoint:]),
                    delay_between_chunks=0.15,
                )
            ]
        ) as server:
            transport = client.T3Client(server.base_url, server.token, request_timeout=0.05)
            started = time.monotonic()
            with self.assertRaises(client.NetworkError):
                transport.get_shell()
            self.assertLess(time.monotonic() - started, 0.5)

    def test_cumulative_slow_drip_exceeds_absolute_deadline(self) -> None:
        payload = json.dumps(shell_snapshot()).encode("utf-8")
        width = max(1, len(payload) // 8)
        chunks = tuple(payload[index : index + width] for index in range(0, len(payload), width))
        with LoopbackServer(
            [Response(chunks=chunks, delay_between_chunks=0.02)]
        ) as server:
            transport = client.T3Client(server.base_url, server.token, request_timeout=0.07)
            started = time.monotonic()
            with self.assertRaises(client.NetworkError):
                transport.get_shell()
            self.assertLess(time.monotonic() - started, 0.4)

    def test_bytes_read_at_deadline_are_still_charged_to_cumulative_budget(self) -> None:
        now = [0.0]

        class DeadlineResponse:
            length = None

            def read1(self, maximum: int) -> bytes:
                now[0] = 2.0
                return b"data"[:maximum]

        class Connection:
            sock = None
            timeout = None

        transport = client.T3Client(
            "http://127.0.0.1:9",
            uuid.uuid4().hex,
            cumulative_response_limit=8,
            clock=lambda: now[0],
        )
        with self.assertRaises(client.NetworkError):
            transport._read_bounded(
                DeadlineResponse(),
                Connection(),
                deadline=1.0,
                response_limit=8,
            )

        self.assertEqual(transport._response_bytes_remaining, 4)

    def test_http_taxonomy_and_known_field_sanitization(self) -> None:
        cases = (
            (400, "invalid_request", False),
            (401, "authentication_error", True),
            (403, "authorization_error", False),
            (404, "not_found", False),
            (302, "http_error", False),
            (409, "http_error", False),
            (408, "server_error", True),
            (429, "server_error", True),
            (503, "server_error", True),
        )
        responses = [
            Response(
                status=status,
                value={
                    "error": {
                        "code": "known\ncode",
                        "requiredScope": "scope",
                        "ignored": "private",
                    }
                },
            )
            for status, _, _ in cases
        ]
        with LoopbackServer(responses) as server:
            transport = client.T3Client(server.base_url, server.token)
            for status, error_code, retryable in cases:
                with self.subTest(status=status), self.assertRaises(client.HTTPResponseError) as caught:
                    transport.get_shell()
                self.assertEqual(caught.exception.error_code, error_code)
                self.assertEqual(caught.exception.retryable, retryable)
                self.assertEqual(caught.exception.details["code"], "known code")
                self.assertNotIn("ignored", caught.exception.details)

    def test_redirect_is_terminal_and_never_followed(self) -> None:
        with LoopbackServer(
            [Response(status=302, value={}, headers=(("Location", "/somewhere"),))]
        ) as server:
            transport = client.T3Client(server.base_url, server.token)
            with self.assertRaises(client.HTTPResponseError) as caught:
                transport.get_shell()
            self.assertEqual(caught.exception.error_code, "http_error")
            self.assertEqual(len(server.requests), 1)


class MutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.command_id = str(uuid.uuid4())
        self.command = {
            "type": "thread.session.stop",
            "commandId": self.command_id,
            "threadId": "thread-1",
        }

    @staticmethod
    def predicate(detail: dict[str, object]) -> bool:
        return bool(detail["thread"].get("verified"))  # type: ignore[index,union-attr]

    @staticmethod
    def verified_detail(sequence: int) -> dict[str, object]:
        detail = detail_snapshot(sequence=sequence)
        detail["thread"]["verified"] = True
        return detail

    def test_accepted_dispatch_polls_at_or_beyond_sequence(self) -> None:
        before = detail_snapshot(sequence=8)
        after = self.verified_detail(9)
        with LoopbackServer(
            [Response(value={"sequence": 9}), Response(value=before), Response(value=after)]
        ) as server:
            transport = client.T3Client(server.base_url, server.token, poll_interval=0.001)
            result = transport.mutate("thread-1", self.command, self.predicate)
            self.assertTrue(result["accepted"])
            self.assertEqual(result["verification"], "verified")
            self.assertTrue(result["completed"])
            self.assertEqual(result["dispatch_sequence"], 9)
            self.assertEqual(result["detail"]["snapshotSequence"], 9)
            self.assertEqual(
                [request["method"] for request in server.requests], ["POST", "GET", "GET"]
            )

    def test_accepted_mutation_budget_exhaustion_is_pending_without_redispatch(self) -> None:
        dispatch = {"sequence": 5}
        unchanged = detail_snapshot(sequence=5)
        dispatch_size = len(json.dumps(dispatch).encode("utf-8"))
        detail_size = len(json.dumps(unchanged).encode("utf-8"))
        cumulative_limit = dispatch_size + detail_size * 2 - 1
        with LoopbackServer(
            [
                Response(value=dispatch),
                Response(value=unchanged),
                Response(value=unchanged),
            ]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                cumulative_response_limit=cumulative_limit,
                mutation_poll_timeout=0.2,
                poll_interval=0.001,
            )
            result = transport.mutate("thread-1", self.command, self.predicate)

        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(
            result["projection_cause_code"], "response_budget_exhausted"
        )
        self.assertEqual(result["observed_snapshot_sequence"], 5)
        self.assertNotIn(server.token, json.dumps(result))
        self.assertEqual(
            [item["method"] for item in server.requests], ["POST", "GET", "GET"]
        )
        self.assertEqual(
            sum(item["method"] == "POST" for item in server.requests), 1
        )

    def test_accepted_mutation_nonfinite_projection_is_pending_without_redispatch(self) -> None:
        projected = json.dumps(detail_snapshot(sequence=5))
        projected = (projected[:-1] + ', "futureMetric": 1e999}').encode("utf-8")
        with LoopbackServer(
            [Response(value={"sequence": 5}), Response(body=projected)]
        ) as server:
            transport = client.T3Client(server.base_url, server.token)
            result = transport.mutate("thread-1", self.command, self.predicate)

        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(result["projection_cause_code"], "response_schema_error")
        self.assertEqual(result["command_id"], self.command_id)
        self.assertEqual(result["thread_id"], "thread-1")
        self.assertEqual(result["dispatch_sequence"], 5)
        self.assertEqual(result["reconciliation"]["required_snapshot_sequence"], 5)
        self.assertNotIn(server.token, json.dumps(result))
        self.assertEqual([item["method"] for item in server.requests], ["POST", "GET"])

    def test_ambiguous_dispatch_reads_back_before_byte_identical_retry(self) -> None:
        dispatch_body = client.canonical_command_bytes(self.command)
        with LoopbackServer(
            [
                Response(value={}, delay_before_body=0.08),
                Response(value=detail_snapshot(sequence=3)),
                Response(value={"sequence": 4}),
                Response(value=self.verified_detail(4)),
            ]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.03,
                mutation_timeout=0.3,
                mutation_poll_timeout=0.1,
                poll_interval=0.001,
                retry_backoff=(),
            )
            result = transport.mutate("thread-1", self.command, self.predicate)
            self.assertEqual(result["dispatch_sequence"], 4)
            self.assertEqual(result["dispatch_attempts"], 2)
            self.assertTrue(result["recovered_after_ambiguous_dispatch"])
            self.assertEqual(
                [request["method"] for request in server.requests], ["POST", "GET", "POST", "GET"]
            )
            posts = [request["body"] for request in server.requests if request["method"] == "POST"]
            self.assertEqual(posts, [dispatch_body, dispatch_body])
            self.assertEqual(json.loads(posts[0])["commandId"], self.command_id)

    def test_ambiguous_dispatch_can_be_verified_by_readback_without_retry(self) -> None:
        with LoopbackServer(
            [
                Response(value={}, delay_before_body=0.08),
                Response(value=self.verified_detail(2)),
            ]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.03,
                mutation_timeout=0.2,
                mutation_poll_timeout=0.1,
            )
            result = transport.mutate("thread-1", self.command, self.predicate)
            self.assertTrue(result["recovered_after_ambiguous_dispatch"])
            self.assertIsNone(result["dispatch_sequence"])
            self.assertEqual([r["method"] for r in server.requests], ["POST", "GET"])

    def test_accepted_sequence_required_retries_predicate_true_readback(self) -> None:
        dispatch_body = client.canonical_command_bytes(self.command)
        with LoopbackServer(
            [
                Response(value={}, delay_before_body=0.08),
                Response(value=self.verified_detail(3)),
                Response(value={"sequence": 4}),
                Response(value=self.verified_detail(4)),
            ]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.03,
                mutation_timeout=0.3,
                mutation_poll_timeout=0.1,
                poll_interval=0.001,
                retry_backoff=(),
            )
            result = transport.mutate(
                "thread-1",
                self.command,
                self.predicate,
                require_accepted_sequence=True,
            )
            self.assertEqual(result["dispatch_sequence"], 4)
            self.assertEqual(result["dispatch_attempts"], 2)
            self.assertTrue(result["recovered_after_ambiguous_dispatch"])
            self.assertEqual(
                [request["method"] for request in server.requests],
                ["POST", "GET", "POST", "GET"],
            )
            posts = [
                request["body"]
                for request in server.requests
                if request["method"] == "POST"
            ]
            self.assertEqual(posts, [dispatch_body, dispatch_body])

    def test_accepted_sequence_required_exhaustion_stays_ambiguous(self) -> None:
        dispatch_body = client.canonical_command_bytes(self.command)
        with LoopbackServer(
            [
                Response(value={}, delay_before_body=0.08),
                Response(value=self.verified_detail(3)),
                Response(value={}, delay_before_body=0.08),
                Response(value=self.verified_detail(4)),
            ]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.03,
                mutation_timeout=0.3,
                mutation_poll_timeout=0.1,
                dispatch_attempts=2,
                retry_backoff=(),
            )
            with self.assertRaises(client.MutationAmbiguousError) as caught:
                transport.mutate(
                    "thread-1",
                    self.command,
                    self.predicate,
                    require_accepted_sequence=True,
                )
            result = caught.exception.to_dict()
            self.assertEqual(result["command_id"], self.command_id)
            self.assertTrue(result["outcome_ambiguous"])
            self.assertEqual(
                [request["method"] for request in server.requests],
                ["POST", "GET", "POST", "GET"],
            )
            posts = [
                request["body"]
                for request in server.requests
                if request["method"] == "POST"
            ]
            self.assertEqual(posts, [dispatch_body, dispatch_body])

    def test_secondary_readback_failure_has_mutation_ambiguity_precedence(self) -> None:
        with LoopbackServer(
            [
                Response(value={}, delay_before_body=0.08),
                Response(value={}, delay_before_body=0.08),
            ]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.03,
                mutation_timeout=0.2,
                mutation_poll_timeout=0.1,
            )
            with self.assertRaises(client.MutationAmbiguousError) as caught:
                transport.mutate("thread-1", self.command, self.predicate)
            result = caught.exception.to_dict()
            self.assertEqual(result["error_code"], "mutation_ambiguous")
            self.assertEqual(result["command_id"], self.command_id)
            self.assertTrue(result["outcome_ambiguous"])
            self.assertFalse(result["retryable"])

    def test_ambiguous_attempt_then_terminal_retry_remains_mutation_ambiguous(self) -> None:
        with LoopbackServer(
            [
                Response(value={}, delay_before_body=0.08),
                Response(value=detail_snapshot(sequence=3)),
                Response(status=401, value={"code": "expired"}),
            ]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.03,
                mutation_timeout=0.3,
                mutation_poll_timeout=0.1,
                retry_backoff=(),
            )
            with self.assertRaises(client.MutationAmbiguousError) as caught:
                transport.mutate("thread-1", self.command, self.predicate)
            result = caught.exception.to_dict()
            self.assertEqual(result["command_id"], self.command_id)
            self.assertEqual(result["thread_id"], "thread-1")
            self.assertEqual(result["cause_code"], "authentication_error")
            self.assertEqual(
                [request["method"] for request in server.requests], ["POST", "GET", "POST"]
            )

    def test_mutation_cannot_verify_against_wrong_readback_target(self) -> None:
        wrong_target = detail_snapshot(sequence=2, thread_id="thread-other")
        wrong_target["thread"]["verified"] = True
        with LoopbackServer(
            [
                Response(value={}, delay_before_body=0.08),
                Response(value=wrong_target),
            ]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.03,
                mutation_timeout=0.2,
                mutation_poll_timeout=0.1,
                retry_backoff=(),
            )
            with self.assertRaises(client.MutationAmbiguousError) as caught:
                transport.mutate("thread-1", self.command, self.predicate)
            result = caught.exception.to_dict()
            self.assertEqual(result["command_id"], self.command_id)
            self.assertEqual(result["thread_id"], "thread-1")
            self.assertEqual(result["cause_code"], "response_schema_error")
            self.assertEqual(
                [request["method"] for request in server.requests], ["POST", "GET"]
            )

    def test_terminal_dispatch_http_failure_is_not_redispatched(self) -> None:
        with LoopbackServer([Response(status=400, value={"code": "invalid"})]) as server:
            transport = client.T3Client(server.base_url, server.token)
            with self.assertRaises(client.HTTPResponseError) as caught:
                transport.mutate("thread-1", self.command, self.predicate)
            self.assertEqual(caught.exception.error_code, "invalid_request")
            self.assertEqual(len(server.requests), 1)

    def test_accepted_turn_with_delayed_projection_is_pending_without_redispatch(self) -> None:
        message_id = str(uuid.uuid4())
        command = {
            "type": "thread.turn.start",
            "commandId": self.command_id,
            "threadId": "thread-1",
            "message": {
                "messageId": message_id,
                "role": "user",
                "text": "Continue with the accepted work.",
                "attachments": [],
            },
            "modelSelection": shell_snapshot()["threads"][0]["modelSelection"],
            "titleSeed": "A thread",
            "runtimeMode": "approval-required",
            "interactionMode": "default",
            "createdAt": NOW,
        }
        dispatch_body = client.canonical_command_bytes(command)

        def exact_message_observed(detail: dict[str, object]) -> bool:
            return any(
                item["id"] == message_id
                and item["role"] == "user"
                and item["text"] == command["message"]["text"]
                for item in detail["thread"]["messages"]  # type: ignore[index,union-attr]
            )

        responses = [Response(value={"sequence": 5})] + [
            Response(value=detail_snapshot(sequence=5)) for _ in range(20)
        ]
        with LoopbackServer(responses) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.1,
                mutation_timeout=0.2,
                mutation_poll_timeout=0.02,
                poll_interval=0.005,
            )
            result = transport.mutate(
                "thread-1", command, exact_message_observed
            )

            self.assertEqual(result["command_id"], self.command_id)
            self.assertEqual(result["message_id"], message_id)
            self.assertEqual(result["thread_id"], "thread-1")
            self.assertEqual(result["dispatch_sequence"], 5)
            self.assertEqual(result["dispatch_attempts"], 1)
            self.assertFalse(result["recovered_after_ambiguous_dispatch"])
            self.assertTrue(result["accepted"])
            self.assertEqual(result["verification"], "accepted_pending_projection")
            self.assertFalse(result["completed"])
            self.assertEqual(result["detail"]["snapshotSequence"], 5)
            self.assertEqual(result["observed_snapshot_sequence"], 5)
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
                    "expected_message_id": message_id,
                },
            )
            posts = [
                request for request in server.requests if request["method"] == "POST"
            ]
            self.assertEqual(len(posts), 1)
            self.assertEqual(posts[0]["body"], dispatch_body)
            self.assertTrue(
                all(request["method"] == "GET" for request in server.requests[1:])
            )

    def test_accepted_get_timeout_at_exact_poll_boundary_is_pending(self) -> None:
        now = [0.0]
        transport = client.T3Client(
            "http://127.0.0.1:9",
            uuid.uuid4().hex,
            mutation_timeout=2.0,
            mutation_poll_timeout=1.0,
            clock=lambda: now[0],
        )
        requests: list[tuple[str, str]] = []

        def request(method: str, path: str, **_kwargs: object) -> object:
            requests.append((method, path))
            if method == "POST":
                return {"sequence": 5}
            now[0] = 1.0
            raise client.NetworkError()

        with mock.patch.object(transport, "_request_json", side_effect=request):
            result = transport.mutate("thread-1", self.command, self.predicate)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertFalse(result["completed"])
        self.assertEqual(result["projection_cause_code"], "poll_deadline")
        self.assertIsNone(result["detail"])
        self.assertNotIn("observed_snapshot_sequence", result)
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
        self.assertEqual(
            requests,
            [
                ("POST", "/api/orchestration/dispatch"),
                ("GET", "/api/orchestration/threads/thread-1?turnLimit=150"),
            ],
        )

    def test_accepted_poll_schema_failure_is_pending_with_bounded_cause(self) -> None:
        with LoopbackServer(
            [Response(value={"sequence": 5}), Response(value={"malformed": True})]
        ) as server:
            transport = client.T3Client(server.base_url, server.token)
            result = transport.mutate("thread-1", self.command, self.predicate)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["verification"], "accepted_pending_projection")
        self.assertEqual(result["projection_cause_code"], "response_schema_error")
        self.assertIsNone(result["detail"])
        self.assertEqual(
            [request["method"] for request in server.requests], ["POST", "GET"]
        )

    def test_exact_projected_user_message_with_null_turn_is_verified_as_accepted(self) -> None:
        message_id = str(uuid.uuid4())
        text = "Queue this exact request."
        command = {
            "type": "thread.turn.start",
            "commandId": self.command_id,
            "threadId": "thread-1",
            "message": {
                "messageId": message_id,
                "role": "user",
                "text": text,
                "attachments": [],
            },
        }
        projected = detail_snapshot(
            sequence=6,
            messages=[message(message_id=message_id, text=text, turn_id=None)],
        )

        def exact_message_observed(detail: dict[str, object]) -> bool:
            return any(
                item["id"] == message_id
                and item["role"] == "user"
                and item["text"] == text
                for item in detail["thread"]["messages"]  # type: ignore[index,union-attr]
            )

        with LoopbackServer(
            [Response(value={"sequence": 6}), Response(value=projected)]
        ) as server:
            transport = client.T3Client(server.base_url, server.token)
            result = transport.mutate(
                "thread-1", command, exact_message_observed
            )

        self.assertTrue(result["accepted"])
        self.assertEqual(result["verification"], "verified")
        self.assertTrue(result["completed"])
        self.assertEqual(result["message_id"], message_id)
        self.assertEqual(result["detail"]["thread"]["messages"], [
            message(message_id=message_id, text=text, turn_id=None)
        ])
        self.assertEqual(
            [request["method"] for request in server.requests], ["POST", "GET"]
        )

    def test_accepted_retry_poll_expiry_is_pending_after_byte_identical_ambiguous_retry(self) -> None:
        dispatch_body = client.canonical_command_bytes(self.command)
        responses = [
            Response(value={}, delay_before_body=0.08),
            Response(value=detail_snapshot(sequence=3)),
            Response(value={"sequence": 4}),
        ] + [Response(value=detail_snapshot(sequence=4)) for _ in range(20)]
        with LoopbackServer(responses) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.03,
                mutation_timeout=0.3,
                mutation_poll_timeout=0.02,
                poll_interval=0.005,
                retry_backoff=(),
            )
            result = transport.mutate("thread-1", self.command, self.predicate)
            self.assertEqual(result["command_id"], self.command_id)
            self.assertEqual(result["thread_id"], "thread-1")
            self.assertEqual(result["dispatch_sequence"], 4)
            self.assertEqual(result["dispatch_attempts"], 2)
            self.assertTrue(result["recovered_after_ambiguous_dispatch"])
            self.assertTrue(result["accepted"])
            self.assertEqual(result["verification"], "accepted_pending_projection")
            self.assertFalse(result["completed"])
            self.assertEqual(result["observed_snapshot_sequence"], 4)
            self.assertEqual(
                result["reconciliation"],
                {
                    "tool": "t3_thread_read",
                    "arguments": {
                        "thread_id": "thread-1",
                        "view": "raw",
                        "turn_limit": 150,
                    },
                    "required_snapshot_sequence": 4,
                },
            )
            posts = [
                request["body"]
                for request in server.requests
                if request["method"] == "POST"
            ]
            self.assertEqual(posts, [dispatch_body, dispatch_body])

    def test_newer_turn_race_is_more_specific_than_ambiguity(self) -> None:
        detail = detail_snapshot(sequence=6)
        detail["thread"]["newerTurn"] = True
        with LoopbackServer([Response(value={"sequence": 6}), Response(value=detail)]) as server:
            transport = client.T3Client(server.base_url, server.token)
            with self.assertRaises(client.ConcurrentStateChangeError) as caught:
                transport.mutate(
                    "thread-1",
                    self.command,
                    self.predicate,
                    race_detector=lambda snapshot: bool(snapshot["thread"].get("newerTurn")),
                )
            self.assertEqual(caught.exception.command_id, self.command_id)
            self.assertEqual(caught.exception.error_code, "concurrent_state_change")

    def test_newer_turn_race_remains_concurrent_after_ambiguous_attempt(self) -> None:
        detail = detail_snapshot(sequence=4)
        detail["thread"]["newerTurn"] = True
        with LoopbackServer(
            [
                Response(value={}, delay_before_body=0.08),
                Response(value=detail_snapshot(sequence=3)),
                Response(value={"sequence": 4}),
                Response(value=detail),
            ]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                request_timeout=0.03,
                mutation_timeout=0.3,
                mutation_poll_timeout=0.1,
                retry_backoff=(),
            )
            with self.assertRaises(client.ConcurrentStateChangeError) as caught:
                transport.mutate(
                    "thread-1",
                    self.command,
                    self.predicate,
                    race_detector=lambda snapshot: bool(
                        snapshot["thread"].get("newerTurn")
                    ),
                )
            self.assertEqual(caught.exception.command_id, self.command_id)
            self.assertEqual(caught.exception.error_code, "concurrent_state_change")
            self.assertEqual(
                [request["method"] for request in server.requests],
                ["POST", "GET", "POST", "GET"],
            )

    def test_injected_uuid_factory_drives_distinct_production_mutations(self) -> None:
        generated = iter(
            (
                uuid.UUID("00000000-0000-4000-8000-000000000001"),
                uuid.UUID("00000000-0000-4000-8000-000000000002"),
            )
        )
        with LoopbackServer(
            [
                Response(value={"sequence": 1}),
                Response(value=self.verified_detail(1)),
                Response(value={"sequence": 2}),
                Response(value=self.verified_detail(2)),
            ]
        ) as server:
            transport = client.T3Client(
                server.base_url,
                server.token,
                uuid_factory=lambda: next(generated),
            )
            first_id = transport.new_uuid4()
            second_id = transport.new_uuid4()
            first = dict(self.command, commandId=first_id)
            second = dict(self.command, commandId=second_id)
            first_result = transport.mutate("thread-1", first, self.predicate)
            second_result = transport.mutate("thread-1", second, self.predicate)
            self.assertNotEqual(first_result["command_id"], second_result["command_id"])
            post_ids = [
                json.loads(request["body"])["commandId"]
                for request in server.requests
                if request["method"] == "POST"
            ]
            self.assertEqual(post_ids, [first_id, second_id])

    def test_canonical_body_limit_and_target_match(self) -> None:
        first = dict(self.command)
        too_large = dict(first, payload="x" * client.MAX_REQUEST_BODY_BYTES)
        with self.assertRaises(client.InvalidInputError):
            client.canonical_command_bytes(too_large)
        mismatched = dict(first, threadId="different-thread")
        transport = client.T3Client("http://127.0.0.1:9", uuid.uuid4().hex)
        with self.assertRaises(client.InvalidInputError):
            transport.mutate("thread-1", mismatched, self.predicate)
        with self.assertRaises(client.InvalidInputError):
            transport.mutate(
                "thread-1",
                first,
                self.predicate,
                require_accepted_sequence=1,  # type: ignore[arg-type]
            )

    def test_active_token_in_command_recursively_fails_before_http(self) -> None:
        with LoopbackServer([]) as server:
            transport = client.T3Client(server.base_url, server.token)
            key_command = dict(self.command)
            key_command[f"key-{server.token}"] = "safe"
            commands = (
                MappingProxyType(key_command),
                dict(self.command, payload=f"value-{server.token}"),
                dict(self.command, payload=[{"nested": f"list-{server.token}"}]),
                dict(self.command, payload=("safe", f"tuple-{server.token}")),
            )
            for command in commands:
                with self.assertRaises(client.InvalidInputError) as caught:
                    transport.mutate("thread-1", command, self.predicate)
                self.assertEqual(
                    caught.exception.safe_message,
                    "command contains the active credential.",
                )
                encoded = json.dumps(caught.exception.to_dict())
                self.assertFalse(
                    server.token in encoded,
                    "The sanitized command error reflected the active credential.",
                )
            self.assertEqual(len(server.requests), 0)

    def test_active_token_in_mutation_target_fails_before_http(self) -> None:
        with LoopbackServer([]) as server:
            transport = client.T3Client(server.base_url, server.token)
            with self.assertRaises(client.InvalidInputError) as caught:
                transport.mutate(
                    f"  thread-{server.token}  ", self.command, self.predicate
                )
            self.assertEqual(
                caught.exception.safe_message,
                "thread_id contains the active credential.",
            )
            encoded = json.dumps(caught.exception.to_dict())
            self.assertFalse(
                server.token in encoded,
                "The sanitized target error reflected the active credential.",
            )
            self.assertEqual(len(server.requests), 0)

    def test_active_token_preflight_is_cycle_safe(self) -> None:
        cycle_mapping: dict[str, object] = {}
        cycle_list: list[object] = [cycle_mapping]
        cycle_mapping["cycle"] = cycle_list
        command = dict(self.command, payload=cycle_mapping)
        with LoopbackServer([]) as server:
            transport = client.T3Client(server.base_url, server.token)
            with self.assertRaises(client.InvalidInputError):
                transport.mutate("thread-1", command, self.predicate)
            self.assertEqual(len(server.requests), 0)


if __name__ == "__main__":
    unittest.main()
