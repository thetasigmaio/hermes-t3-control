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

    def test_gateway_captured_native_subscribe_stream_maps_reserved_nullable_user(self):
        # Actual authenticated 2026-09-09 subscribeThread catch-up frames, text removed.
        frames = json.loads((Path(__file__).parent / "fixtures/native_subscribe_nullable_turn.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            store = ContinuationStore(directory)
            store.initialize()
            binding = store.bind(baseline=(884768, "2026-09-09T21:01:30Z"),
                **binding_values(platform="telegram",
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

class AutomaticMissionTests(unittest.TestCase):
    def make_store(self, directory):
        store = ContinuationStore(directory)
        store.initialize()
        return store

    def auto(self, store, name="auto-1", **overrides):
        return store.bind_automatic(baseline=(3, utc_now()), message_id=name + "-message",
            **binding_values(binding_id=name, sunsama_task_id="", t3_owner_id="thread-1", **overrides))

    def test_parallel_sends_reserve_exactly_one_active_mission(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            self.make_store(directory)
            def attempt(index):
                try:
                    return self.auto(ContinuationStore(directory), f"auto-{index}").binding_id
                except ContinuationStateError:
                    return None
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, range(2)))
            self.assertEqual(sum(result is not None for result in results), 1)
            store = ContinuationStore(directory)
            binding = store.active_bindings("default")[0]
            self.assertEqual(store.reserved_source(binding), binding.binding_id + "-message")

    def test_terminal_uncertain_history_is_byte_preserved_and_not_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            old = self.auto(store)
            event = dict(envelope(old, sequence=4), source_message_id="auto-1-message")
            self.assertTrue(store.ingest(old, cursor_sequence=4, event=event))
            store.claim_next(old.binding_id)
            store.finish(old.binding_id, 4, "uncertain", receipt={"status": "unknown"})
            store.set_binding_state(old.binding_id, "stopped")
            with store._connect() as db:
                before = [tuple(row) for row in db.execute("SELECT * FROM events WHERE binding_id=?", (old.binding_id,))]
                binding_before = tuple(db.execute("SELECT * FROM bindings WHERE binding_id=?", (old.binding_id,)).fetchone())
            fresh = self.auto(store, "auto-2")
            self.assertEqual(fresh.max_continuations, 1)
            with store._connect() as db:
                self.assertEqual(before, [tuple(row) for row in db.execute("SELECT * FROM events WHERE binding_id=?", (old.binding_id,))])
                self.assertEqual(binding_before, tuple(db.execute("SELECT * FROM bindings WHERE binding_id=?", (old.binding_id,)).fetchone()))
            self.assertIsNone(store.claim_next(old.binding_id))

    def test_paused_queued_dispatching_and_foreign_identity_refuse_atomically(self):
        for state in ("paused", "queued", "dispatching", "foreign"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                store = self.make_store(directory)
                old = self.auto(store)
                if state == "paused":
                    store.set_binding_state(old.binding_id, "paused")
                elif state in {"queued", "dispatching"}:
                    store.ingest(old, cursor_sequence=4, event=dict(envelope(old, sequence=4), source_message_id="auto-1-message"))
                    if state == "dispatching":
                        store.claim_next(old.binding_id)
                    with store._connect() as db:
                        db.execute("UPDATE bindings SET state='stopped' WHERE binding_id=?", (old.binding_id,))
                with self.assertRaises(ContinuationStateError):
                    self.auto(store, "auto-2", **({"hermes_session_id": "foreign"} if state == "foreign" else {}))
                with store._connect() as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM bindings").fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT count(*) FROM source_reservations").fetchone()[0], 1)

    def test_terminal_foreign_history_does_not_inherit_or_block_new_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            old = self.auto(store)
            store.set_binding_state(old.binding_id, "stopped")
            with store._connect() as db:
                before = tuple(db.execute("SELECT * FROM bindings WHERE binding_id=?", (old.binding_id,)).fetchone())
            fresh = self.auto(store, "auto-2", profile_name="other", hermes_session_id="current", t3_environment_id="current-env")
            self.assertEqual(fresh.hermes_session_id, "current")
            with store._connect() as db:
                self.assertEqual(before, tuple(db.execute("SELECT * FROM bindings WHERE binding_id=?", (old.binding_id,)).fetchone()))

    def test_completed_acknowledged_budget_retires_only_settled_active_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            old = self.auto(store)
            event = dict(envelope(old, sequence=4), source_message_id="auto-1-message")
            store.ingest(old, cursor_sequence=4, event=event)
            store.claim_next(old.binding_id)
            store.finish(old.binding_id, 4, "completed", receipt={"status": "completed"})
            store.acknowledge(old.binding_id, event["source_event_id"])
            fresh = self.auto(store, "auto-2")
            self.assertEqual(store.get_binding(old.binding_id).state, "stopped")
            self.assertEqual(fresh.state, "active")

    def test_second_readiness_exception_retires_undispatched_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = {"profile_name": "default", "session_id": "session-1"}
            ctx = SimpleNamespace(state=SimpleNamespace(data_dir=directory), current_desktop_destination=lambda: target,
                desktop_continuation_readiness=mock.Mock(side_effect=[{"ready": True}, RuntimeError("gone")]),
                get_config=lambda key, default=None: True if key == "continuation_enabled" else default)
            before = detail_snapshot(sequence=3, turn=latest_turn(turn_id="old", state="completed"), current_session=session(status="ready"))
            transport = SimpleNamespace(get_environment_descriptor=lambda: {"environmentId": "environment-1"})
            with self.assertRaises(RuntimeError):
                continuation_handoff.prepare_handoff(ctx, None, "thread-1", before, transport, "message-1")
            ctx.desktop_continuation_readiness = lambda target: {"ready": True}
            result = continuation_handoff.prepare_handoff(ctx, None, "thread-1", before, transport, "message-2")
            self.assertTrue(result["armed"])

class LocalV2MigrationTests(unittest.TestCase):
    def fixture(self, directory):
        import sqlite3
        store = ContinuationStore(directory)
        store.data_dir.mkdir(exist_ok=True)
        store.key_path.write_bytes(b"k" * 32)
        schema = json.loads((Path(__file__).parent / "fixtures/local-v2-schema.json").read_text())
        with store._connect() as db:
            for kind, name, sql in sorted(schema, key=lambda row: row[0] == "index"):
                db.execute(sql)
            db.execute("INSERT INTO meta VALUES('schema_version','2')")
            db.execute("INSERT INTO meta VALUES('source_reservation_version','1')")
            values = binding_values(sunsama_task_id="")
            columns = list(values) + ["state", "cursor_sequence", "created_at", "updated_at", "baseline_captured"]
            db.execute(f"INSERT INTO bindings({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", tuple(values.values()) + ("stopped", 0, utc_now(), utc_now(), 1))
            signed = dict(values, source_message_id="source-message", status="uncertain")
            encoded = json.dumps(signed)
            signature = store.sign(signed)
            db.execute("INSERT INTO source_reservations VALUES(?,?,?)", ("mission-1", encoded, signature))
            db.execute("INSERT INTO notification_targets VALUES(?,?,?)", ("mission-1", encoded, signature))
            db.execute("INSERT INTO notifications VALUES(?,?,?,?,?,?,?,?)", ("mission-1", 1, encoded, signature, "uncertain", 1, 0, '{"status":"uncertain"}',))
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("mission-1", 1, "event-1", "turn-1", "external_tool_completed", utc_now(), encoded, signature, "uncertain", 1, 0, '{"status":"uncertain"}', None, utc_now(), utc_now()))
        return store

    def test_exact_local_v2_migration_preserves_signed_rows_and_empty_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.fixture(directory)
            with store._connect() as db:
                old = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in ("source_reservations", "notification_targets", "notifications", "events", "binding_lineage")}
            key = store.key_path.read_bytes()
            store.initialize()
            store.initialize()
            with store._connect() as db:
                self.assertEqual(old, {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")] for table in old})
            self.assertTrue(store.get_binding("mission-1").baseline_captured)
            self.assertEqual(store.get_binding("mission-1").cursor_sequence, 0)
            self.assertEqual(store.key_path.read_bytes(), key)

    def test_unknown_shape_and_injected_failure_leave_database_unchanged(self):
        for corrupt in (True, False):
            with tempfile.TemporaryDirectory() as directory:
                store = self.fixture(directory)
                if corrupt:
                    with store._connect() as db:
                        db.execute("CREATE TABLE unexpected (id TEXT)")
                before = store.db_path.read_bytes()
                with mock.patch.object(store, "_create_bindings_table", side_effect=RuntimeError("injected")):
                    with self.assertRaises((ContinuationStateError, RuntimeError)):
                        store.initialize()
                self.assertEqual(store.db_path.read_bytes(), before)


    def test_duplicate_live_rows_wrong_metadata_and_bad_signature_refuse_without_mutation(self):
        for fault in ("duplicate", "metadata", "signature", "missing_key"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                store = self.fixture(directory)
                with store._connect() as db:
                    if fault == "duplicate":
                        db.execute("UPDATE bindings SET state='active'")
                        row = dict(db.execute("SELECT * FROM bindings").fetchone())
                        row["binding_id"] = "duplicate"
                        db.execute(f"INSERT INTO bindings({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
                    elif fault == "metadata":
                        db.execute("INSERT INTO meta VALUES('unexpected','1')")
                    elif fault == "signature":
                        db.execute("UPDATE events SET envelope_mac='wrong'")
                if fault == "missing_key":
                    store.key_path.unlink()
                before = store.db_path.read_bytes()
                with self.assertRaises(ContinuationStateError):
                    store.initialize()
                self.assertEqual(store.db_path.read_bytes(), before)
                if fault == "missing_key":
                    self.assertFalse(store.key_path.exists())
