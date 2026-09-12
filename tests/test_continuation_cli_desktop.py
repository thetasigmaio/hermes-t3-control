from __future__ import annotations

import asyncio
import argparse
import copy
import contextlib
import io
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import continuation
import continuation_cli
import continuation_state
import continuation_transport
from tests.support import detail_snapshot, latest_turn, message, session
from tests.test_continuation import FakeContext
from tests import test_continuation as worker_tests
from tests.test_continuation_state import binding_values


class DesktopContinuationCliTests(unittest.TestCase):
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

    def attach(self, snapshot, **overrides):
        values = binding_values(platform="desktop", hermes_session_key="session-1",
                                user_id="", chat_id="", topic_id="")
        mapping = {"t3_thread_id": "thread_id", "t3_owner_id": "owner_id",
                   "t3_environment_id": "environment_id", "hermes_session_key": "session_key",
                   "hermes_session_id": "session_id"}
        args = SimpleNamespace(continuation_action="bind", attach_current_turn="turn-1",
            source_message_id=None, **{mapping.get(k, k): v for k, v in values.items()})
        for key, value in overrides.items():
            setattr(args, key, value)
        with (mock.patch.object(continuation_cli, "read_thread_snapshot", return_value=snapshot),
              mock.patch.object(continuation_transport, "operator_desktop_readiness") as ready,
              mock.patch.object(continuation_cli, "utc_now", return_value="2026-09-08T10:00:00Z"),
              contextlib.redirect_stdout(io.StringIO()) as output):
            code = continuation_cli.make_continuation_cli_handler(self.ctx)(args)
        return code, json.loads(output.getvalue()), ready

    def test_attach_current_turn_requires_unique_trusted_running_provenance(self):
        snapshot = detail_snapshot(sequence=10, turn=latest_turn(state="running"),
            current_session=session(status="running", active_turn_id="turn-1"),
            messages=[message(turn_id=None)])
        for mutation in ("completed", "wrong_thread", "other_turn", "other_active", "ambiguous",
                         "missing", "stale_time", "imported", "streaming", "missing_scope"):
            with self.subTest(mutation=mutation):
                candidate = copy.deepcopy(snapshot)
                thread = candidate["thread"]
                extra = {}
                if mutation == "completed":
                    thread["latestTurn"]["state"] = "completed"
                elif mutation == "wrong_thread":
                    thread["id"] = "other"
                elif mutation == "other_turn":
                    thread["latestTurn"]["turnId"] = "other"
                elif mutation == "other_active":
                    thread["session"]["activeTurnId"] = "other"
                elif mutation == "ambiguous":
                    thread["messages"].append(message(message_id="another", turn_id=None))
                elif mutation == "missing":
                    thread["messages"] = []
                elif mutation == "stale_time":
                    thread["messages"][0]["createdAt"] = "2020-01-01T00:00:00Z"
                elif mutation == "imported":
                    thread["messages"][0]["metadata"] = {"historyImport": True}
                elif mutation == "streaming":
                    thread["messages"][0]["streaming"] = True
                else:
                    extra["followup_scope"] = None
                code, output, _ = self.attach(candidate, **extra)
                self.assertEqual(code, 1, output)
                self.assertEqual(self.store.status()["bindings"], [])
        code, output, ready = self.attach(snapshot)
        self.assertEqual(code, 0, output)
        ready.assert_called_once()
        binding = self.store.get_binding("mission-1")
        self.assertEqual(binding.cursor_sequence, 10)
        self.assertEqual(self.store.reserved_source(binding), "message-1")
        self.assertEqual(self.store.reserved_turn(binding), "turn-1")
        self.assertEqual(self.attach(snapshot)[0], 1)  # Fresh authority never rearms a prior binding.
        completed = copy.deepcopy(snapshot)
        completed["snapshotSequence"] = 11
        completed["thread"]["latestTurn"].update(state="completed", completedAt="2099-01-01T00:00:00Z")
        completed["thread"]["session"].update(status="ready", activeTurnId=None)
        self.ctx.inject_desktop_system_event = self.ctx.inject_gateway_system_event

        async def subscribe(*_args, **kwargs):
            self.assertEqual(kwargs["after_sequence"], 10)
            yield {"kind": "event", "event": worker_tests.t3_event(sequence=11, turn_id="turn-1")}

        with (mock.patch.object(continuation, "subscribe_thread", subscribe),
              mock.patch.object(continuation, "read_thread_snapshot", return_value=completed)):
            asyncio.run(continuation._binding_worker(self.ctx, self.store, "mission-1", asyncio.Event(), 1, 0.1))
        self.assertEqual(len(self.ctx.calls), 1)
        self.assertEqual(self.store.status("mission-1")["bindings"][0]["events"], {"completed": 1})

    def test_attach_cli_is_explicit_and_mutually_exclusive_with_future_handoff(self):
        parser = argparse.ArgumentParser()
        continuation_cli.setup_continuation_cli(parser)
        argv = ["bind", "--binding-id", "m", "--thread-id", "t", "--owner-id", "o",
                "--environment-id", "e", "--session-key", "s", "--session-id", "s",
                "--platform", "desktop", "--user-id", "", "--chat-id", "",
                "--source-identity", "source", "--attach-current-turn", "turn-1",
                "--followup-scope", "none", "--max-continuations", "1"]
        args = parser.parse_args(argv)
        self.assertEqual(args.attach_current_turn, "turn-1")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(argv + ["--source-message-id", "future-message"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(argv + ["--replace-session-id", "old-session"])
        renewed = parser.parse_args(["renew", *argv[1:], "--replaces", "old-binding", "--topic-id", "",
                                     "--replace-session-id", "old-session"])
        self.assertEqual(renewed.replace_session_id, "old-session")
        self.store.initialize()
        predecessor = self.store.bind(**binding_values(hermes_session_id="old-session"))
        from tests.test_continuation_state import envelope
        self.store.ingest(predecessor, cursor_sequence=1, event=envelope(predecessor))
        self.store.claim_next(predecessor.binding_id)
        self.store.finish(predecessor.binding_id, 1, "completed", receipt={"status": "completed"})
        self.store.acknowledge(predecessor.binding_id, "event-1")
        self.store.set_binding_state(predecessor.binding_id, "stopped")
        snapshot = detail_snapshot(sequence=10, turn=latest_turn(state="running"),
            current_session=session(status="running", active_turn_id="turn-1"), messages=[message(turn_id=None)])
        code, output, ready = self.attach(snapshot, continuation_action="renew", replaces="mission-1",
            binding_id="mission-2", desktop_upgrade=True, replace_session_id="old-session")
        self.assertEqual(code, 0, output)
        ready.assert_called_once()
        self.assertEqual(self.store.get_binding("mission-2").hermes_session_id, "session-1")

