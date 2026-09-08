from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace
from unittest import mock
import continuation
from tests.test_continuation import FakeContext
from tests import test_continuation as worker_tests
import contextlib
import io
import json
import pathlib
import tempfile
import unittest

import continuation_cli
import continuation_state
from tests.test_continuation_state import binding_values, envelope


class CliContext:
    profile_name = "default"

    def __init__(self, data_dir: pathlib.Path):
        self.state = type("State", (), {"data_dir": data_dir})()


def parse_action(*arguments: str):
    parser = argparse.ArgumentParser()
    continuation_cli.setup_continuation_cli(parser)
    return parser.parse_args(arguments)


def binding_arguments(action: str, binding_id: str, **overrides) -> tuple[str, ...]:
    values = binding_values(binding_id=binding_id, **overrides)
    return (
        action,
        "--binding-id",
        values["binding_id"],
        "--thread-id",
        values["t3_thread_id"],
        "--owner-id",
        values["t3_owner_id"],
        "--environment-id",
        values["t3_environment_id"],
        "--session-key",
        values["hermes_session_key"],
        "--session-id",
        values["hermes_session_id"],
        "--platform",
        values["platform"],
        "--user-id",
        values["user_id"],
        "--chat-id",
        values["chat_id"],
        "--topic-id",
        values["topic_id"],
        "--sunsama-task-id",
        values["sunsama_task_id"],
        "--source-identity",
        values["source_identity"],
        "--followup-scope",
        values["followup_scope"],
        "--max-continuations",
        str(values["max_continuations"]),
    )


class ContinuationCliTests(unittest.TestCase):
    def setUp(self):
        reader = mock.patch.object(continuation_cli, "read_thread_snapshot", return_value={"snapshotSequence": 0})
        reader.start()
        self.addCleanup(reader.stop)

    def test_new_id_on_historical_thread_requires_explicit_renewal_without_sqlite_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = pathlib.Path(temporary) / "plugin-data"
            ctx = CliContext(data_dir)
            handler = continuation_cli.make_continuation_cli_handler(ctx)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    handler(parse_action(*binding_arguments("bind", "mission-1"))), 0
                )

            store = continuation_state.ContinuationStore(data_dir)
            store.initialize()
            predecessor = store.get_binding("mission-1")
            self.assertTrue(
                store.ingest(
                    predecessor,
                    cursor_sequence=1,
                    event=envelope(predecessor),
                )
            )
            self.assertIsNotNone(store.claim_next(predecessor.binding_id))
            self.assertTrue(
                store.finish(
                    predecessor.binding_id,
                    1,
                    "completed",
                    receipt={"status": "completed", "receipt_id": "receipt-1"},
                )
            )
            store.acknowledge(predecessor.binding_id, "event-1")
            store.set_binding_state(predecessor.binding_id, "stopped")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = handler(
                    parse_action(*binding_arguments("bind", "mission-2"))
                )

            self.assertEqual(result, 1)
            self.assertEqual(
                json.loads(output.getvalue()),
                {
                    "error": "thread has binding history; use explicit renewal",
                    "ok": False,
                },
            )

    def test_explicit_renewal_accepts_full_new_mission_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = pathlib.Path(temporary) / "plugin-data"
            ctx = CliContext(data_dir)
            handler = continuation_cli.make_continuation_cli_handler(ctx)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    handler(parse_action(*binding_arguments("bind", "mission-1"))), 0
                )
            store = continuation_state.ContinuationStore(data_dir)
            store.initialize()
            predecessor = store.get_binding("mission-1")
            store.ingest(predecessor, cursor_sequence=1, event=envelope(predecessor))
            store.claim_next(predecessor.binding_id)
            store.finish(
                predecessor.binding_id,
                1,
                "completed",
                receipt={"status": "completed", "receipt_id": "receipt-1"},
            )
            store.acknowledge(predecessor.binding_id, "event-1")
            store.set_binding_state(predecessor.binding_id, "stopped")

            arguments = binding_arguments(
                "renew",
                "mission-2",
                source_identity="source-2",
                sunsama_task_id="task-2",
                followup_scope="read-only-followup",
                max_continuations=2,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = handler(parse_action("renew", "--replaces", "mission-1", *arguments[1:]))

            self.assertEqual(result, 0)
            self.assertEqual(
                json.loads(output.getvalue()),
                {
                    "binding_id": "mission-2",
                    "cursor_sequence": 0,
                    "ok": True,
                    "replaces_binding_id": "mission-1",
                    "state": "active",
                    "baseline_captured": True,
                    "armed": True,
                },
            )


class ContinuationBaselineCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = continuation_state.ContinuationStore(pathlib.Path(self.temporary.name) / "data")
        self.ctx = FakeContext(self.store, {"status": "completed"})
        self.ctx.get_config = lambda key, default=None: default

    def bind(self, sequence=0, *, action="bind", **overrides):
        values = binding_values(**overrides)
        mapping = {"t3_thread_id": "thread_id", "t3_owner_id": "owner_id",
                   "t3_environment_id": "environment_id", "hermes_session_key": "session_key",
                   "hermes_session_id": "session_id"}
        args = SimpleNamespace(continuation_action=action, replaces="mission-1",
                               **{mapping.get(k, k): v for k, v in values.items()})
        snapshot = worker_tests.ContinuationTests.completed_snapshot(state="running", status="running")
        snapshot["snapshotSequence"] = sequence
        with (mock.patch.object(continuation_cli, "read_thread_snapshot", return_value=snapshot) as read,
              mock.patch.object(continuation_cli, "utc_now", return_value="2026-09-08T10:00:00Z"),
              contextlib.redirect_stdout(io.StringIO()) as output):
            code = continuation_cli.make_continuation_cli_handler(self.ctx)(args)
        return code, json.loads(output.getvalue()), read

    def test_bind_captures_trusted_zero_baseline_before_observer_start(self):
        code, output, read = self.bind()
        self.assertEqual(code, 0, output)
        read.assert_called_once_with(self.ctx, thread_id="thread-1", environment_id="environment-1")
        self.assertTrue(output["baseline_captured"])
        self.assertTrue(output["armed"])
        self.assertNotIn("observer_ready", output)
        binding = self.store.get_binding("mission-1")
        self.assertEqual(binding.cursor_sequence, 0)
        self.assertTrue(binding.baseline_captured)
        self.assertEqual(binding.created_at, "2026-09-08T10:00:00Z")

    def test_delayed_start_delivers_post_bind_completion_including_read_commit_race(self):
        self.bind()
        # Completion timestamp is after registration's boundary and before its commit.
        snapshot = worker_tests.ContinuationTests.completed_snapshot(completed_at="2026-09-08T10:00:00.001Z")
        seen = {}

        async def subscribe(*_args, **kwargs):
            seen.update(kwargs)
            yield {"kind": "snapshot", "snapshot": snapshot}

        with mock.patch.object(continuation, "subscribe_thread", subscribe):
            asyncio.run(continuation._binding_worker(self.ctx, self.store, "mission-1", asyncio.Event(), 1, 0.1))
        self.assertEqual(seen["after_sequence"], 0)
        self.assertEqual(len(self.ctx.calls), 1)
        self.assertEqual(self.store.status("mission-1")["bindings"][0]["events"], {"completed": 1})

    def test_registration_baseline_does_not_replay_existing_terminal_history(self):
        self.bind(sequence=2)

        async def subscribe(*_args, **kwargs):
            self.assertEqual(kwargs["after_sequence"], 2)
            yield {"kind": "snapshot", "snapshot": worker_tests.ContinuationTests.completed_snapshot()}

        with mock.patch.object(continuation, "subscribe_thread", subscribe):
            asyncio.run(continuation._binding_worker(self.ctx, self.store, "mission-1", asyncio.Event(), 1, 0.1))
        self.assertEqual(self.ctx.calls, [])
        self.assertEqual(self.store.status("mission-1")["bindings"][0]["events"], {})

    def test_renew_cli_captures_new_baseline_and_cancelled_successor_is_not_revived(self):
        self.bind()
        binding = self.store.get_binding("mission-1")
        from tests.test_continuation_state import envelope
        self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        self.store.claim_next(binding.binding_id)
        self.store.finish(binding.binding_id, 1, "completed", receipt={"status": "completed"})
        self.store.acknowledge(binding.binding_id, "event-1")
        self.store.set_binding_state(binding.binding_id, "stopped")
        code, output, read = self.bind(sequence=10, action="renew", binding_id="mission-2",
                                       hermes_session_id="new-session", followup_scope="none")
        self.assertEqual(code, 0, output)
        self.assertTrue(output["armed"])
        self.assertEqual(output["cursor_sequence"], 10)
        self.store.set_binding_state("mission-2", "cancelled")
        code, output, read = self.bind(sequence=20, action="renew", binding_id="mission-2",
                                       hermes_session_id="new-session", followup_scope="none")
        self.assertEqual(code, 0, output)
        self.assertEqual(output["state"], "cancelled")
        self.assertFalse(output["armed"])
        self.assertEqual(output["cursor_sequence"], 10)

    def test_baseline_failure_does_not_create_a_binding(self):
        with mock.patch.object(continuation_cli, "read_thread_snapshot",
                               side_effect=continuation_cli.ContinuationTransportError("wrong environment")):
            args = SimpleNamespace(continuation_action="bind", binding_id="mission-1", thread_id="thread-1",
                owner_id="owner-1", environment_id="environment-1", session_key="session-key",
                session_id="session-id", platform="telegram", user_id="user-1", chat_id="chat-1",
                topic_id="topic-1", sunsama_task_id="task-1", source_identity="source-1",
                followup_scope="none", max_continuations=1)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = continuation_cli.make_continuation_cli_handler(self.ctx)(args)
        self.assertEqual(code, 1, output.getvalue())
        self.assertEqual(self.store.status()["bindings"], [])


if __name__ == "__main__":
    unittest.main()
