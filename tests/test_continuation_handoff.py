import asyncio
import contextlib
import io
import json
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest import mock

import continuation
import continuation_handoff
import continuation_cli
import continuation_transport
from continuation_state import ContinuationStore, ContinuationStateError, utc_now
from tests.test_continuation_state import binding_values, envelope
from tests.support import detail_snapshot, latest_turn, session


class ScopedHandoffTests(unittest.TestCase):
    def test_required_handoff_reserves_only_initiating_message_and_ack_does_not_rearm(self):
        with tempfile.TemporaryDirectory() as directory:
            target = {"profile_name": "default", "session_id": "session-1"}
            ctx = SimpleNamespace(state=SimpleNamespace(data_dir=directory),
                current_desktop_destination=lambda: target,
                desktop_continuation_readiness=lambda destination: {"ready": True},
                get_config=lambda key, default=None: True if key == "continuation_enabled" else default)
            args = {"binding_id": "mission-1", "owner_id": "owner-1", "environment_id": "environment-1",
                "sunsama_task_id": "task-1", "source_identity": "source-1", "followup_scope": "none",
                "max_continuations": 1, "expected_turn_id": "turn-old"}
            before = detail_snapshot(sequence=3, turn=latest_turn(turn_id="turn-old", state="completed"), current_session=session(status="ready"))
            transport = SimpleNamespace(get_environment_descriptor=lambda: {"environmentId": "environment-1"})
            result = continuation_handoff.prepare_handoff(ctx, args, "thread-1", before, transport, "message-1")
            store = ContinuationStore(directory)
            binding = store.get_binding(result["binding_id"])
            event = envelope(binding, sequence=4)
            self.assertIsNone(continuation.reserved_event(store, binding, event,
                {"thread": {"messages": [{"id": "message-other", "role": "user", "turnId": "turn-1"}]}}))
            self.assertIsNone(continuation.reserved_event(store, binding, event,
                {"thread": {"messages": [{"id": "message-1", "role": "user", "turnId": None}]}}))
            mapped = continuation.reserved_event(store, binding, event,
                {"thread": {"messages": [{"id": "message-1", "role": "user", "turnId": "turn-1"}]}})
            self.assertTrue(store.ingest(binding, cursor_sequence=4, event=mapped))
            row = store.claim_next(binding.binding_id)
            self.assertTrue(store.dispatch_is_eligible(binding, 4, row["envelope_mac"]))
            store.acknowledge(binding.binding_id, event["source_event_id"])
            self.assertFalse(store.dispatch_is_eligible(binding, 4, row["envelope_mac"]))
            self.assertTrue(store.dispatch_is_eligible(binding, 4, row["envelope_mac"], completion=True))
            store.set_binding_state(binding.binding_id, "cancelled")
            self.assertFalse(store.dispatch_is_eligible(binding, 4, row["envelope_mac"], completion=True))
            with self.assertRaises(ContinuationStateError):
                store.reserve_source(binding, "different-message")

    def test_captured_native_subscribe_stream_maps_reserved_nullable_user(self):
        # Actual authenticated 2026-09-09 subscribeThread catch-up frames, text removed.
        frames = json.loads((Path(__file__).parent / "fixtures/native_subscribe_nullable_turn.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            store = ContinuationStore(directory)
            store.initialize()
            binding = store.bind(baseline=(884768, "2026-09-09T21:01:30Z"),
                **binding_values(platform="desktop", hermes_session_key="session-1", user_id="", chat_id="", topic_id="",
                    t3_thread_id="fixture-id-37"))
            store.reserve_source(binding, "fixture-id-7")
            for frame in frames:
                if frame["kind"] == "event":
                    event = frame["event"]
                    store.ingest(binding, cursor_sequence=event["sequence"], event=None, native_event=event)
            self.assertEqual(store.reserved_turn(binding), "fixture-id-1")
            mapped = continuation.reserved_event(store, binding,
                envelope(binding, sequence=884803, turn_id="fixture-id-1"),
                {"thread": {"messages": [{"id": "fixture-id-7", "role": "user", "turnId": None}]}})
            self.assertIsNotNone(mapped)

    def test_captured_native_witness_rejects_import_and_missing_command_or_message_fields(self):
        captured = json.loads((Path(__file__).parent / "fixtures/native_subscribe_nullable_turn.json").read_text())
        for field, value in (("commandId", None), ("metadata", {"historyImport": True}),
                             ("role", "assistant"), ("turnId", "turn-other"),
                             ("streaming", True), ("messageId", "other-user")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                frames = json.loads(json.dumps(captured))
                first = frames[0]["event"]
                (first if field in {"commandId", "metadata"} else first["payload"])[field] = value
                store = ContinuationStore(directory)
                store.initialize()
                binding = store.bind(baseline=(884768, "2026-09-09T21:01:30Z"),
                    **binding_values(platform="desktop", hermes_session_key="session-1", user_id="", chat_id="", topic_id="",
                        t3_thread_id="fixture-id-37"))
                store.reserve_source(binding, "fixture-id-7")
                for frame in frames:
                    if frame["kind"] == "event":
                        event = frame["event"]
                        store.ingest(binding, cursor_sequence=event["sequence"], event=None, native_event=event)
                self.assertIsNone(store.reserved_turn(binding))

    def test_native_nullable_message_maps_from_start_and_running_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContinuationStore(directory)
            store.initialize()
            binding = store.bind(baseline=(3, utc_now()), **binding_values(platform="desktop", hermes_session_key="session-1", user_id="", chat_id="", topic_id=""))
            store.reserve_source(binding, "message-1", excluded_turn_ids=["turn-old"])
            def native(sequence, kind, **payload):
                if kind == "thread.message-sent":
                    payload = {"role": "user", "turnId": None, "streaming": False, **payload}
                return {"sequence": sequence, "eventId": f"native-{sequence}",
                    "aggregateKind": "thread", "aggregateId": "thread-1", "type": kind,
                    "occurredAt": "2099-01-01T00:00:00Z", "commandId": "command-1", "metadata": {},
                    "payload": {"threadId": "thread-1", **payload}}
            start = native(4, "thread.message-sent", messageId="message-1")
            store.ingest(binding, cursor_sequence=4, event=None, native_event=start)
            store = ContinuationStore(directory)
            store.ingest(binding, cursor_sequence=4, event=None, native_event=start)
            running = native(5, "thread.session-set", session={"status": "running", "activeTurnId": "turn-1"})
            store.ingest(binding, cursor_sequence=5, event=None, native_event=running)
            store = ContinuationStore(directory)
            event = envelope(binding, sequence=6)
            mapped = continuation.reserved_event(store, binding, event,
                {"thread": {"messages": [{"id": "message-1", "role": "user", "turnId": None}]}})
            self.assertIsNotNone(mapped)
            self.assertTrue(store.ingest(binding, cursor_sequence=6, event=mapped))
            self.assertEqual(json.loads(store.claim_next(binding.binding_id)["envelope_json"])["source_message_id"], "message-1")

    def test_native_correlation_rejects_ambiguity_and_keeps_cursor_atomic(self):
        cases = {
            "competing_start": [("thread.message-sent", {"messageId": "other"})],
            "repeated_start_new_sequence": [("thread.message-sent", {"messageId": "message-1"})],
            "stopped": [("thread.session-set", {"session": {"status": "stopped", "activeTurnId": None}})],
            "error": [("thread.session-set", {"session": {"status": "error", "activeTurnId": None}})],
            "revert": [("thread.checkpoint-revert-requested", {})],
            "start_failed": [("thread.activity-appended", {"activity": {"kind": "provider.turn.start.failed"}})],
            "old_turn": [("thread.session-set", {"session": {"status": "running", "activeTurnId": "turn-old"}})],
        }
        for case, intervening in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                store, binding = self._reserved_store(directory)
                self._native_ingest(store, binding, 4, "thread.message-sent", messageId="message-1")
                for sequence, (kind, payload) in enumerate(intervening, 5):
                    self._native_ingest(store, binding, sequence, kind, **payload)
                self._native_ingest(store, binding, 6, "thread.session-set", session={"status": "running", "activeTurnId": "turn-1"})
                self.assertIsNone(store.reserved_turn(binding))
        with tempfile.TemporaryDirectory() as directory:
            store, binding = self._reserved_store(directory)
            self._native_ingest(store, binding, 4, "thread.session-set", session={"status": "running", "activeTurnId": "turn-1"})
            self._native_ingest(store, binding, 5, "thread.message-sent", messageId="message-1")
            self.assertIsNone(store.reserved_turn(binding))
        with tempfile.TemporaryDirectory() as directory:
            store, binding = self._reserved_store(directory)
            self._native_ingest(store, binding, 4, "thread.message-sent", messageId="message-1")
            store.ingest(binding, cursor_sequence=5, event=None, snapshot_gap=True)
            self._native_ingest(store, binding, 6, "thread.session-set", session={"status": "running", "activeTurnId": "turn-1"})
            self.assertIsNone(store.reserved_turn(binding))
        with tempfile.TemporaryDirectory() as directory:
            store, binding = self._reserved_store(directory)
            self._native_ingest(store, binding, 4, "thread.message-sent", messageId="message-1")
            with mock.patch.object(store, "sign", side_effect=RuntimeError("disk boundary")):
                with self.assertRaises(RuntimeError):
                    self._native_ingest(store, binding, 5, "thread.session-set", session={"status": "running", "activeTurnId": "turn-1"})
            self.assertEqual(store.get_binding(binding.binding_id).cursor_sequence, 4)
            self.assertIsNone(store.reserved_turn(binding))
            self._native_ingest(store, binding, 5, "thread.session-set", session={"status": "running", "activeTurnId": "turn-1"})
            self.assertEqual(store.reserved_turn(binding), "turn-1")
            with store._connect() as db:
                db.execute("UPDATE source_reservations SET envelope_mac='tampered'")
            with self.assertRaises(ContinuationStateError):
                store.reserved_turn(binding)

    @staticmethod
    def _reserved_store(directory):
        store = ContinuationStore(directory)
        store.initialize()
        binding = store.bind(baseline=(3, utc_now()), **binding_values(platform="desktop",
            hermes_session_key="session-1", user_id="", chat_id="", topic_id=""))
        store.reserve_source(binding, "message-1", excluded_turn_ids=["turn-old"])
        return store, binding

    @staticmethod
    def _native_ingest(store, binding, sequence, kind, **payload):
        if kind == "thread.message-sent":
            payload = {"role": "user", "turnId": None, "streaming": False, **payload}
        return store.ingest(binding, cursor_sequence=sequence, event=None, native_event={
            "sequence": sequence, "eventId": f"native-{sequence}", "type": kind,
            "aggregateKind": "thread", "aggregateId": binding.t3_thread_id,
            "occurredAt": "2099-01-01T00:00:00Z", "commandId": "command-1", "metadata": {},
            "payload": {"threadId": binding.t3_thread_id, **payload}})

    def test_operator_cli_baseline_and_delayed_native_worker_with_global_sequence_gaps(self):
        for busy in (False, True):
            with self.subTest(busy=busy), tempfile.TemporaryDirectory() as directory:
                ctx = SimpleNamespace(state=SimpleNamespace(data_dir=directory), get_config=lambda key, default=None: default)
                args = SimpleNamespace(continuation_action="bind", binding_id="mission-1", thread_id="thread-1",
                    owner_id="owner-1", environment_id="environment-1", session_key="session-1",
                    session_id="session-1", platform="desktop", user_id="", chat_id="", topic_id="",
                    sunsama_task_id="task-1", source_identity="source-1", followup_scope="none",
                    max_continuations=1, source_message_id="message-1")
                before = detail_snapshot(sequence=3, turn=latest_turn(turn_id="turn-old", state="completed"),
                    current_session=session(status="running" if busy else "ready"))
                with (mock.patch.object(continuation_cli, "read_thread_snapshot", return_value=before),
                      mock.patch.object(continuation_transport, "operator_desktop_readiness"),
                      contextlib.redirect_stdout(io.StringIO()) as output):
                    code = continuation_cli.make_continuation_cli_handler(ctx)(args)
                self.assertEqual(code, 1 if busy else 0, output.getvalue())
                store = ContinuationStore(directory)
                if busy:
                    self.assertEqual(store.status()["bindings"], [])
                    continue
                binding = store.get_binding("mission-1")
                with store._connect() as db:
                    self.assertIn("turn-old", store._source_correlation(db, binding)["correlation"]["excluded_turn_ids"])
                def native(sequence, kind, **payload):
                    if kind == "thread.message-sent":
                        payload = {"role": "user", "turnId": None, "streaming": False, **payload}
                    return {"sequence": sequence, "eventId": f"event-{sequence}", "type": kind,
                        "aggregateKind": "thread", "aggregateId": "thread-1", "occurredAt": "2099-01-01T00:00:00Z", "commandId": "command-1", "metadata": {},
                        "payload": {"threadId": "thread-1", **payload}}
                start = native(10, "thread.message-sent", messageId="message-1")
                running = native(90, "thread.session-set", session={"status": "running", "activeTurnId": "turn-1"})
                completed = native(140, "thread.session-set", session={"status": "ready", "activeTurnId": None})
                async def subscribe(*_args, **kwargs):
                    self.assertEqual(kwargs["after_sequence"], 3)
                    for event in (start, start, running, completed):
                        yield {"kind": "event", "event": event}
                snapshot = detail_snapshot(sequence=140,
                    turn=latest_turn(turn_id="turn-1", state="completed"),
                    current_session=session(status="ready"))
                snapshot["thread"]["latestTurn"]["completedAt"] = "2099-01-01T00:00:00Z"
                snapshot["thread"]["messages"] = [{"id": "message-1", "role": "user", "turnId": None}]
                with (mock.patch.object(continuation, "subscribe_thread", subscribe),
                      mock.patch.object(continuation, "read_thread_snapshot", return_value=snapshot),
                      mock.patch.object(continuation, "_drain_pending", new=mock.AsyncMock(return_value=True))):
                    asyncio.run(continuation._binding_worker(ctx, store, binding.binding_id, asyncio.Event(), 1, 0.1))
                row = store.claim_next(binding.binding_id)
                self.assertIsNotNone(row)
                self.assertEqual(row["source_turn_id"], "turn-1")
                self.assertEqual(store.get_binding(binding.binding_id).cursor_sequence, 140)

    def test_surface_recovery_and_explicit_same_physical_upgrade_preserve_old_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ContinuationStore(directory)
            store.initialize()
            old = store.bind(**binding_values())
            store.ingest(old, cursor_sequence=1, event=envelope(old))
            row = store.claim_next(old.binding_id)
            self.assertEqual(store.recover_dispatching(surface="desktop"), 0)
            self.assertTrue(store.dispatch_is_eligible(old, 1, row["envelope_mac"]))
            store.acknowledge(old.binding_id, "event-1")
            store.finish(old.binding_id, 1, "completed", receipt={"status": "completed"})
            store.set_binding_state(old.binding_id, "stopped")
            new_values = binding_values(binding_id="mission-2", platform="desktop", hermes_session_key="session-1",
                                        user_id="", chat_id="", topic_id="", source_identity="new-scope")
            with self.assertRaises(ContinuationStateError):
                store.renew(old.binding_id, **new_values)
            successor = store.renew(old.binding_id, desktop_upgrade=True, baseline=(2, utc_now()), **new_values)
            self.assertEqual(successor.platform, "desktop")
            self.assertEqual(store.get_binding(old.binding_id).platform, "telegram")
            self.assertEqual(store.get_binding(old.binding_id).state, "stopped")
