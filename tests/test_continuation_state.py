from __future__ import annotations

import pathlib
import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
from unittest import mock
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import continuation_state


def binding_values(**overrides):
    values = {
        "binding_id": "mission-1",
        "profile_name": "default",
        "t3_thread_id": "thread-1",
        "t3_owner_id": "owner-1",
        "t3_environment_id": "environment-1",
        "hermes_session_key": "agent:main:telegram:dm:1:2",
        "hermes_session_id": "session-1",
        "platform": "telegram",
        "user_id": "user-1",
        "chat_id": "chat-1",
        "topic_id": "topic-1",
        "sunsama_task_id": "task-1",
        "source_identity": "source-1",
        "followup_scope": "none",
        "max_continuations": 1,
    }
    values.update(overrides)
    return values


def envelope(binding, *, sequence=1, event_id="event-1", turn_id="turn-1"):
    return {
        "version": 1,
        "binding_id": binding.binding_id,
        "profile_name": binding.profile_name,
        "t3_thread_id": binding.t3_thread_id,
        "t3_owner_id": binding.t3_owner_id,
        "t3_environment_id": binding.t3_environment_id,
        "hermes_session_key": binding.hermes_session_key,
        "hermes_session_id": binding.hermes_session_id,
        "platform": binding.platform,
        "user_id": binding.user_id,
        "chat_id": binding.chat_id,
        "topic_id": binding.topic_id,
        "sunsama_task_id": binding.sunsama_task_id,
        "source_identity": binding.source_identity,
        "followup_scope": binding.followup_scope,
        "max_continuations": binding.max_continuations,
        "source_sequence": sequence,
        "source_event_id": event_id,
        "source_turn_id": turn_id,
        "event_kind": "external_tool_completed",
        "occurred_at": "2099-01-01T00:00:00Z",
    }


def create_v1_database(data_dir: pathlib.Path) -> continuation_state.ContinuationStore:
    data_dir.mkdir(mode=0o700)
    key_path = data_dir / "continuation.hmac"
    key_path.write_bytes(b"k" * 32)
    key_path.chmod(0o600)
    db_path = data_dir / "continuation.sqlite3"
    with closing(sqlite3.connect(db_path)) as db:
        db.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta(key,value) VALUES('schema_version','1');
            CREATE TABLE bindings (
                binding_id TEXT PRIMARY KEY,
                profile_name TEXT NOT NULL,
                t3_thread_id TEXT NOT NULL UNIQUE,
                t3_owner_id TEXT NOT NULL,
                t3_environment_id TEXT NOT NULL,
                hermes_session_key TEXT NOT NULL,
                hermes_session_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                user_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                sunsama_task_id TEXT NOT NULL,
                source_identity TEXT NOT NULL,
                followup_scope TEXT NOT NULL,
                max_continuations INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('active','paused','stopped','cancelled')),
                cursor_sequence INTEGER NOT NULL DEFAULT 0 CHECK(cursor_sequence >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE events (
                binding_id TEXT NOT NULL REFERENCES bindings(binding_id),
                source_sequence INTEGER NOT NULL CHECK(source_sequence >= 0),
                source_event_id TEXT NOT NULL,
                source_turn_id TEXT NOT NULL,
                event_kind TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                envelope_json TEXT NOT NULL,
                envelope_mac TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('queued','dispatching','completed','acknowledged','cancelled','blocked','uncertain')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                busy_attempts INTEGER NOT NULL DEFAULT 0 CHECK(busy_attempts >= 0),
                receipt_json TEXT,
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(binding_id, source_sequence),
                UNIQUE(binding_id, source_event_id),
                UNIQUE(binding_id, event_kind, source_turn_id)
            );
            CREATE INDEX events_dispatch_idx
                ON events(status, updated_at, source_sequence);
            """
        )
    db_path.chmod(0o600)
    return continuation_state.ContinuationStore(data_dir)


def exhaust_and_acknowledge(
    store: continuation_state.ContinuationStore,
    binding: continuation_state.Binding,
    *,
    sequence: int = 1,
    event_id: str = "event-1",
    turn_id: str = "turn-1",
) -> None:
    value = envelope(binding, sequence=sequence, event_id=event_id, turn_id=turn_id)
    if binding.max_continuations > 1:
        raise AssertionError("test helper supports one-continuation predecessors")
    if not store.ingest(binding, cursor_sequence=sequence, event=value):
        raise AssertionError("test predecessor event was not inserted")
    if store.claim_next(binding.binding_id) is None:
        raise AssertionError("test predecessor event was not claimable")
    if not store.finish(
        binding.binding_id,
        sequence,
        "completed",
        receipt={"status": "completed", "receipt_id": f"receipt-{sequence}"},
    ):
        raise AssertionError("test predecessor event was not completed")
    store.acknowledge(binding.binding_id, event_id)
    store.set_binding_state(binding.binding_id, "stopped")


@dataclass(frozen=True)
class LegacyV1BindingFixture:
    """Exact public v1 row shape retained by an already-running supervisor."""

    binding_id: str
    profile_name: str
    t3_thread_id: str
    t3_owner_id: str
    t3_environment_id: str
    hermes_session_key: str
    hermes_session_id: str
    platform: str
    user_id: str
    chat_id: str
    topic_id: str
    sunsama_task_id: str
    source_identity: str
    followup_scope: str
    max_continuations: int
    state: str
    cursor_sequence: int
    created_at: str
    updated_at: str


def populate_exhausted_v1_database(
    store: continuation_state.ContinuationStore,
) -> LegacyV1BindingFixture:
    values = binding_values()
    now = "2099-01-01T00:00:00Z"
    binding = LegacyV1BindingFixture(
        **values,
        state="stopped",
        cursor_sequence=1,
        created_at=now,
        updated_at=now,
    )
    value = envelope(binding)
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    signature = hmac.new(
        store.key_path.read_bytes(), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    columns = tuple(LegacyV1BindingFixture.__dataclass_fields__)
    with closing(sqlite3.connect(store.db_path)) as db:
        db.execute(
            f"INSERT INTO bindings({','.join(columns)}) "
            f"VALUES({','.join('?' for _ in columns)})",
            tuple(getattr(binding, column) for column in columns),
        )
        db.execute(
            """INSERT INTO events(
                binding_id,source_sequence,source_event_id,source_turn_id,event_kind,
                occurred_at,envelope_json,envelope_mac,status,attempts,busy_attempts,
                receipt_json,last_error_code,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,1,0,?,NULL,?,?)""",
            (
                binding.binding_id,
                1,
                value["source_event_id"],
                value["source_turn_id"],
                value["event_kind"],
                value["occurred_at"],
                encoded,
                signature,
                "acknowledged",
                json.dumps(
                    {"status": "completed", "receipt_id": "receipt-1"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now,
                now,
            ),
        )
        db.commit()
    return binding


class RetainedV1SupervisorFixture:
    """Portable vulnerable v1 scan/ingest/delivery path for compatibility testing."""

    def __init__(self, db_path: pathlib.Path):
        self.db_path = db_path
        self.adoptions = 0
        self.ingestions = 0
        self.deliveries = 0

    def scan_once(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM bindings WHERE profile_name='default' AND state='active'"
            ).fetchall()
            bindings = [LegacyV1BindingFixture(**dict(row)) for row in rows]
            for binding in bindings:
                self.adoptions += 1
                now = "2099-01-01T00:00:00Z"
                value = {
                    "binding_id": binding.binding_id,
                    "source_event_id": "legacy-event",
                    "source_turn_id": "legacy-turn",
                    "event_kind": "external_tool_completed",
                }
                self.ingestions += db.execute(
                    """INSERT OR IGNORE INTO events(
                        binding_id,source_sequence,source_event_id,source_turn_id,
                        event_kind,occurred_at,envelope_json,envelope_mac,status,
                        attempts,created_at,updated_at
                    ) VALUES(?,1,?,?,?,?,?,'legacy-v1-mac','queued',0,?,?)""",
                    (
                        binding.binding_id,
                        value["source_event_id"],
                        value["source_turn_id"],
                        value["event_kind"],
                        now,
                        json.dumps(value, sort_keys=True),
                        now,
                        now,
                    ),
                ).rowcount
                self.deliveries += 1
            db.commit()


class ContinuationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = continuation_state.ContinuationStore(
            pathlib.Path(self.temporary.name) / "plugin-data"
        )
        self.store.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_owner_only_state_and_key(self):
        self.assertEqual(self.store.data_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.db_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.key_path.stat().st_mode & 0o777, 0o600)

    def test_binding_authority_is_immutable(self):
        first = self.store.bind(**binding_values())
        self.assertEqual(first.state, "active")
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.bind(**binding_values(hermes_session_id="other-session"))

    def test_idempotent_bind_does_not_reactivate_a_stopped_binding(self):
        binding = self.store.bind(**binding_values())
        self.store.set_binding_state(binding.binding_id, "stopped")
        rebound = self.store.bind(**binding_values())
        self.assertEqual(rebound.state, "stopped")

    def test_renewal_requires_exact_route_but_allows_new_mission_authority(self):
        predecessor = self.store.bind(**binding_values())
        exhaust_and_acknowledge(self.store, predecessor)

        renewed = self.store.renew(
            predecessor.binding_id,
            **binding_values(
                binding_id="mission-2",
                source_identity="source-2",
                sunsama_task_id="task-2",
                followup_scope="read-only-followup",
                max_continuations=2,
            ),
        )

        self.assertEqual(renewed.state, "active")
        self.assertEqual(renewed.cursor_sequence, 0)
        self.assertEqual(renewed.source_identity, "source-2")
        self.assertEqual(renewed.max_continuations, 2)
        self.assertEqual(self.store.get_binding(predecessor.binding_id).state, "stopped")
        status = self.store.status()
        old, new = status["bindings"]
        self.assertEqual(old["successor_binding_id"], "mission-2")
        self.assertEqual(new["predecessor_binding_id"], "mission-1")
        self.assertEqual(old["events"], {"acknowledged": 1})

    def test_renewal_route_mismatch_is_failure_atomic(self):
        predecessor = self.store.bind(**binding_values())
        exhaust_and_acknowledge(self.store, predecessor)

        with self.assertRaisesRegex(
            continuation_state.ContinuationStateError,
            "source and Hermes destination must match",
        ):
            self.store.renew(
                predecessor.binding_id,
                **binding_values(
                    binding_id="mission-2", hermes_session_key="other-route"
                ),
            )

        self.assertEqual(self.store.get_binding(predecessor.binding_id).state, "stopped")
        self.assertEqual(len(self.store.status()["bindings"]), 1)

    def test_renewal_rejects_nonterminal_and_cancelled_predecessors(self):
        predecessor = self.store.bind(**binding_values())
        self.store.ingest(predecessor, cursor_sequence=1, event=envelope(predecessor))
        self.assertIsNotNone(self.store.claim_next(predecessor.binding_id))
        self.store.recover_dispatching()
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(
                predecessor.binding_id,
                **binding_values(binding_id="mission-2"),
            )

        paused_store = continuation_state.ContinuationStore(
            pathlib.Path(self.temporary.name) / "paused-data"
        )
        paused_store.initialize()
        paused = paused_store.bind(**binding_values())
        exhaust_and_acknowledge(paused_store, paused)
        paused_store.set_binding_state(paused.binding_id, "paused")
        with self.assertRaisesRegex(
            continuation_state.ContinuationStateError, "explicitly stopped"
        ):
            paused_store.renew(
                paused.binding_id,
                **binding_values(binding_id="mission-2"),
            )
        self.store.set_binding_state(predecessor.binding_id, "cancelled")
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(
                predecessor.binding_id,
                **binding_values(binding_id="mission-2"),
            )

    def test_exact_renewal_retry_is_idempotent_and_superseded_binding_cannot_resume(self):
        predecessor = self.store.bind(**binding_values())
        exhaust_and_acknowledge(self.store, predecessor)
        values = binding_values(binding_id="mission-2", source_identity="source-2")
        renewed = self.store.renew(predecessor.binding_id, **values)
        retried = self.store.renew(predecessor.binding_id, **values)
        self.assertEqual(retried, renewed)
        self.store.set_binding_state(renewed.binding_id, "stopped")
        with self.assertRaisesRegex(
            continuation_state.ContinuationStateError, "superseded binding"
        ):
            self.store.set_binding_state(predecessor.binding_id, "active")

    def test_concurrent_renewals_create_only_one_successor(self):
        predecessor = self.store.bind(**binding_values())
        exhaust_and_acknowledge(self.store, predecessor)

        def attempt(identifier: str):
            store = continuation_state.ContinuationStore(self.store.data_dir)
            try:
                return store.renew(
                    predecessor.binding_id,
                    **binding_values(
                        binding_id=identifier, source_identity=f"source-{identifier}"
                    ),
                ).binding_id
            except continuation_state.ContinuationStateError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("mission-2a", "mission-2b")))

        winners = [result for result in results if result.startswith("mission-")]
        failures = [result for result in results if not result.startswith("mission-")]
        self.assertEqual(len(winners), 1)
        self.assertEqual(failures, ["predecessor binding already has a successor"])
        bindings = self.store.status()["bindings"]
        self.assertEqual(len(bindings), 2)
        self.assertEqual(sum(item["state"] == "active" for item in bindings), 1)

    def test_cross_generation_dedup_rejects_old_turn_across_event_kinds(self):
        predecessor = self.store.bind(**binding_values())
        exhaust_and_acknowledge(self.store, predecessor, turn_id="shared-turn")
        renewed = self.store.renew(
            predecessor.binding_id,
            **binding_values(binding_id="mission-2", source_identity="source-2"),
        )
        duplicate = envelope(
            renewed,
            sequence=2,
            event_id="fresh-event-id",
            turn_id="shared-turn",
        )
        duplicate["event_kind"] = "external_tool_error"

        self.assertFalse(
            self.store.ingest(renewed, cursor_sequence=2, event=duplicate)
        )
        status = self.store.status(renewed.binding_id)["bindings"][0]
        self.assertEqual(status["cursor_sequence"], 2)
        self.assertEqual(status["events"], {})

        duplicate_id = envelope(
            renewed,
            sequence=3,
            event_id="event-1",
            turn_id="fresh-turn",
        )
        self.assertFalse(
            self.store.ingest(renewed, cursor_sequence=3, event=duplicate_id)
        )
        status = self.store.status(renewed.binding_id)["bindings"][0]
        self.assertEqual(status["cursor_sequence"], 3)
        self.assertEqual(status["events"], {})

    def test_cursor_and_event_insert_are_atomic_and_semantically_deduplicated(self):
        binding = self.store.bind(**binding_values(max_continuations=3))
        self.assertTrue(
            self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        )
        duplicate_turn = envelope(
            binding, sequence=2, event_id="event-2", turn_id="turn-1"
        )
        self.assertFalse(
            self.store.ingest(binding, cursor_sequence=2, event=duplicate_turn)
        )
        status = self.store.status("mission-1")["bindings"][0]
        self.assertEqual(status["cursor_sequence"], 2)
        self.assertEqual(status["events"], {"queued": 1})

    def test_continuation_budget_advances_cursor_without_queueing_more_work(self):
        binding = self.store.bind(**binding_values(max_continuations=1))
        self.assertTrue(
            self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        )
        second = envelope(binding, sequence=2, event_id="event-2", turn_id="turn-2")
        self.assertFalse(
            self.store.ingest(binding, cursor_sequence=2, event=second)
        )
        status = self.store.status(binding.binding_id)["bindings"][0]
        self.assertEqual(status["cursor_sequence"], 2)
        self.assertEqual(status["events"], {"queued": 1})

    def test_restart_marks_ambiguous_dispatch_uncertain(self):
        binding = self.store.bind(**binding_values())
        self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        self.assertIsNotNone(self.store.claim_next(binding.binding_id))
        self.assertEqual(self.store.recover_dispatching(), 1)
        status = self.store.status(binding.binding_id)["bindings"][0]
        self.assertEqual(status["events"], {"uncertain": 1})

    def test_ack_during_host_future_is_idempotent_and_survives_completion(self):
        binding = self.store.bind(**binding_values())
        self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        self.assertIsNotNone(self.store.claim_next(binding.binding_id))

        self.store.acknowledge(binding.binding_id, "event-1")
        self.store.acknowledge(binding.binding_id, "event-1")
        self.assertFalse(
            self.store.finish(
                binding.binding_id,
                1,
                "completed",
                receipt={"status": "completed", "receipt_id": "receipt-1"},
            )
        )

        status = self.store.status(binding.binding_id)["bindings"][0]
        self.assertEqual(status["events"], {"acknowledged": 1})

    def test_ack_requires_active_binding_and_current_signed_authority(self):
        binding = self.store.bind(**binding_values())
        self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        self.assertIsNotNone(self.store.claim_next(binding.binding_id))
        self.store.set_binding_state(binding.binding_id, "paused")
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.acknowledge(binding.binding_id, "event-1")

        second_store = continuation_state.ContinuationStore(
            pathlib.Path(self.temporary.name) / "tampered-data"
        )
        second_store.initialize()
        second = second_store.bind(**binding_values(binding_id="mission-2"))
        value = envelope(second)
        value["binding_id"] = "mission-2"
        second_store.ingest(second, cursor_sequence=1, event=value)
        self.assertIsNotNone(second_store.claim_next(second.binding_id))
        with second_store._connect() as db:
            db.execute(
                "UPDATE events SET envelope_json=? WHERE binding_id=?",
                ("{}", second.binding_id),
            )
        with self.assertRaises(continuation_state.ContinuationStateError):
            second_store.acknowledge(second.binding_id, "event-1")

    def test_dispatch_eligibility_reopens_current_state_and_fails_closed(self):
        binding = self.store.bind(**binding_values())
        value = envelope(binding)
        self.store.ingest(binding, cursor_sequence=1, event=value)
        row = self.store.claim_next(binding.binding_id)
        self.assertTrue(
            self.store.dispatch_is_eligible(
                binding, 1, row["envelope_mac"]
            )
        )
        self.store.set_binding_state(binding.binding_id, "paused")
        self.assertFalse(
            self.store.dispatch_is_eligible(
                binding, 1, row["envelope_mac"]
            )
        )

    def test_connection_context_always_closes_connection(self):
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT 1").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")

    def test_pause_cancels_queued_work_and_resume_does_not_revive_it(self):
        binding = self.store.bind(**binding_values())
        self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        self.store.set_binding_state(binding.binding_id, "paused")
        self.store.set_binding_state(binding.binding_id, "active")
        self.assertIsNone(self.store.claim_next(binding.binding_id))
        status = self.store.status(binding.binding_id)["bindings"][0]
        self.assertEqual(status["events"], {"cancelled": 1})

    def test_signature_detects_tampering(self):
        binding = self.store.bind(**binding_values())
        value = envelope(binding)
        signature = self.store.sign(value)
        self.assertTrue(self.store.verify(value, signature))
        value["hermes_session_id"] = "other"
        self.assertFalse(self.store.verify(value, signature))

    def test_ack_before_terminal_host_error_keeps_audit_and_error_receipt_without_retry(self):
        binding = self.store.bind(**binding_values())
        self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        self.store.claim_next(binding.binding_id)
        self.store.acknowledge(binding.binding_id, "event-1")
        receipt = {"status": "agent_error", "reason": "delivery_failed"}
        self.assertFalse(self.store.finish(binding.binding_id, 1, "uncertain", receipt=receipt,
                                          error_code="agent_error_after_admission"))
        with self.store._connect() as db:
            row = db.execute("SELECT * FROM events").fetchone()
        self.assertEqual(row["status"], "acknowledged")
        self.assertEqual(json.loads(row["receipt_json"]), receipt)
        self.assertEqual(row["last_error_code"], "agent_error_after_admission")
        self.assertIsNone(self.store.claim_next(binding.binding_id))
        self.assertEqual(self.store.active_bindings("default"), [])
        self.store.set_binding_state(binding.binding_id, "stopped")
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(binding.binding_id, **binding_values(binding_id="mission-2"))


    def completed_acknowledged_binding(self):
        binding = self.store.bind(**binding_values(followup_scope="old-scope"))
        self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        self.store.claim_next(binding.binding_id)
        self.store.finish(binding.binding_id, 1, "completed", receipt={"status": "completed"})
        self.store.acknowledge(binding.binding_id, "event-1")
        return binding


    def test_spent_binding_is_truthfully_exhausted_and_not_observed_after_restart(self):
        binding = self.completed_acknowledged_binding()
        restarted = continuation_state.ContinuationStore(self.store.data_dir)
        restarted.initialize()
        status = restarted.status(binding.binding_id)["bindings"][0]
        self.assertEqual(status["state"], "exhausted")
        self.assertEqual(status["remaining_continuations"], 0)
        self.assertFalse(status["armed"])
        self.assertEqual(restarted.active_bindings("default"), [])
        self.assertEqual(restarted.get_binding(binding.binding_id).state, "exhausted")


    def test_same_source_new_id_requires_explicit_renewal(self):
        self.completed_acknowledged_binding()
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.bind(**binding_values(binding_id="mission-2"))


    def test_renewal_preserves_audit_and_uses_only_explicit_new_authority(self):
        binding = self.completed_acknowledged_binding()
        self.store.set_binding_state(binding.binding_id, "stopped")
        with self.store._connect() as db:
            before = dict(db.execute("SELECT * FROM events").fetchone())
        values = binding_values(binding_id="mission-2", hermes_session_id="new-session",
                                followup_scope="none", sunsama_task_id="new-task")
        renewed = self.store.renew(binding.binding_id, **values)
        self.assertEqual(renewed.hermes_session_id, "new-session")
        self.assertEqual(renewed.followup_scope, "none")
        self.assertEqual(renewed.cursor_sequence, 0)
        self.assertFalse(self.store.status(renewed.binding_id)["bindings"][0]["armed"])
        self.assertEqual(self.store.renew(binding.binding_id, **values), renewed)
        with self.store._connect() as db:
            self.assertEqual(dict(db.execute("SELECT * FROM events").fetchone()), before)
        restarted = continuation_state.ContinuationStore(self.store.data_dir)
        restarted.initialize()
        self.assertEqual([b.binding_id for b in restarted.active_bindings("default")], ["mission-2"])
        with self.assertRaises(continuation_state.ContinuationStateError):
            restarted.renew(binding.binding_id, **binding_values(binding_id="mission-3"))
        with self.assertRaises(continuation_state.ContinuationStateError):
            restarted.set_binding_state(binding.binding_id, "active")


    def test_renewal_rejects_active_paused_cancelled_or_wrong_route(self):
        binding = self.completed_acknowledged_binding()
        for state in ("active", "paused"):
            self.store.set_binding_state(binding.binding_id, state)
            with self.subTest(state=state), self.assertRaises(continuation_state.ContinuationStateError):
                self.store.renew(binding.binding_id, **binding_values(binding_id="mission-2"))
        self.store.set_binding_state(binding.binding_id, "stopped")
        for field in ("t3_thread_id", "t3_owner_id", "t3_environment_id", "hermes_session_key",
                      "profile_name", "platform", "user_id", "chat_id", "topic_id"):
            with self.subTest(field=field), self.assertRaises(continuation_state.ContinuationStateError):
                self.store.renew(binding.binding_id, **binding_values(binding_id="mission-2", **{field: "other"}))
        self.assertEqual(len(self.store.status()["bindings"]), 1)
        self.store.set_binding_state(binding.binding_id, "cancelled")
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(binding.binding_id, **binding_values(binding_id="mission-2"))
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.set_binding_state(binding.binding_id, "stopped")


    def test_renewal_rejects_missing_explicit_scope_and_incomplete_receipt(self):
        binding = self.completed_acknowledged_binding()
        self.store.set_binding_state(binding.binding_id, "stopped")
        values = binding_values(binding_id="mission-2")
        del values["followup_scope"]
        with self.assertRaises((ValueError, continuation_state.ContinuationStateError)):
            self.store.renew(binding.binding_id, **values)
        with self.store._connect() as db:
            db.execute("UPDATE events SET receipt_json=NULL")
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(binding.binding_id, **binding_values(binding_id="mission-2"))


    def test_unknown_schema_is_rejected_without_changing_rows(self):
        self.completed_acknowledged_binding()
        with self.store._connect() as db:
            db.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
            before = dict(db.execute("SELECT * FROM events").fetchone())
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.initialize()
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "999")
            self.assertEqual(dict(db.execute("SELECT * FROM events").fetchone()), before)


    def test_restart_after_ack_before_receipt_retires_waiter_without_replay_or_renewal(self):
        binding = self.store.bind(**binding_values())
        self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        self.store.claim_next(binding.binding_id)
        self.store.acknowledge(binding.binding_id, "event-1")
        self.assertEqual(self.store.get_binding(binding.binding_id).state, "active")
        self.assertEqual(self.store.recover_dispatching(), 1)
        self.assertEqual(self.store.recover_dispatching(), 0)
        self.assertEqual(self.store.get_binding(binding.binding_id).state, "exhausted")
        self.assertEqual(self.store.status(binding.binding_id)["bindings"][0]["events"], {"acknowledged": 1})
        self.assertIsNone(self.store.claim_next(binding.binding_id))
        self.store.set_binding_state(binding.binding_id, "stopped")
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(binding.binding_id, **binding_values(binding_id="mission-2"))


    def test_retry_cancellation_and_restart_never_replay_the_prior_attempt(self):
        for retry_status in ("busy", "stopping"):
            for ending in ("cancelled", "restart"):
                with self.subTest(retry=retry_status, ending=ending):
                    store = continuation_state.ContinuationStore(self.store.data_dir / f"{retry_status}-{ending}")
                    store.initialize()
                    binding = store.bind(**binding_values())
                    store.ingest(binding, cursor_sequence=1, event=envelope(binding))
                    store.claim_next(binding.binding_id)
                    store.finish(binding.binding_id, 1, "queued", receipt={"status": retry_status},
                                 refund_attempt=True, count_busy=True)
                    if ending == "cancelled":
                        store.set_binding_state(binding.binding_id, "cancelled")
                    else:
                        claimed = store.claim_next(binding.binding_id)
                        self.assertIsNone(claimed["receipt_json"])
                        self.assertIsNone(claimed["last_error_code"])
                        store.acknowledge(binding.binding_id, "event-1")
                        self.assertEqual(store.get_binding(binding.binding_id).state, "active")
                        self.assertEqual(store.recover_dispatching(), 1)
                        self.assertEqual(store.recover_dispatching(), 0)
                        with store._connect() as db:
                            row = db.execute("SELECT * FROM events").fetchone()
                        self.assertEqual(row["status"], "acknowledged")
                        self.assertIsNone(row["receipt_json"])
                        self.assertEqual(row["last_error_code"], "process_restart")
                    self.assertIsNone(store.claim_next(binding.binding_id))
                    self.assertEqual(store.active_bindings("default"), [])




def create_public_v2_database(data_dir: pathlib.Path):
    """Build the released v2 layout from the retained v1 fixture, without current DDL."""
    store = create_v1_database(data_dir)
    predecessor = populate_exhausted_v1_database(store)
    with closing(sqlite3.connect(store.db_path)) as db:
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='bindings'").fetchone()[0]
        sql = sql.replace("CREATE TABLE bindings", "CREATE TABLE bindings_v2")
        sql = sql.replace("t3_thread_id TEXT NOT NULL UNIQUE", "t3_thread_id TEXT NOT NULL")
        sql = sql.replace("updated_at TEXT NOT NULL", "updated_at TEXT NOT NULL, "
                          "binding_schema_version INTEGER NOT NULL CHECK(binding_schema_version = 2)")
        db.execute(sql)
        columns = ",".join(continuation_state.V1_BINDING_COLUMNS)
        db.execute(f"INSERT INTO bindings_v2 SELECT {columns},2 FROM bindings")
        db.execute("DROP TABLE bindings")
        db.execute("ALTER TABLE bindings_v2 RENAME TO bindings")
        db.execute("CREATE UNIQUE INDEX bindings_live_thread_idx ON bindings(t3_thread_id) "
                   "WHERE state IN ('active','paused')")
        db.execute("CREATE TABLE binding_lineage ("
                   "predecessor_binding_id TEXT PRIMARY KEY REFERENCES bindings(binding_id),"
                   "successor_binding_id TEXT NOT NULL UNIQUE REFERENCES bindings(binding_id),"
                   "renewed_at TEXT NOT NULL)")
        values = binding_values(binding_id="mission-2", source_identity="source-2")
        values.update(state="active", cursor_sequence=0, created_at="2099-01-02T00:00:00Z",
                      updated_at="2099-01-02T00:00:00Z", binding_schema_version=2)
        columns = continuation_state.V2_BINDING_COLUMNS
        db.execute(f"INSERT INTO bindings({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                   tuple(values[column] for column in columns))
        db.execute("INSERT INTO binding_lineage VALUES(?,?,?)",
                   (predecessor.binding_id, "mission-2", "2099-01-02T00:00:00Z"))
        db.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        db.commit()
    return store


def create_public_v3_database(data_dir):
    store = create_public_v2_database(data_dir)
    with closing(sqlite3.connect(store.db_path)) as db:
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='bindings'").fetchone()[0]
        sql = sql.replace('CREATE TABLE "bindings"', 'CREATE TABLE bindings_v3')
        sql = sql.replace('binding_schema_version = 2', 'binding_schema_version = 3')
        sql = sql[:-1] + ', baseline_captured INTEGER NOT NULL DEFAULT 0 CHECK(baseline_captured IN (0,1)))'
        db.execute(sql)
        db.execute(f"INSERT INTO bindings_v3 SELECT {','.join(continuation_state.V1_BINDING_COLUMNS)},3,1 FROM bindings")
        db.execute("DROP TABLE bindings")
        db.execute("ALTER TABLE bindings_v3 RENAME TO bindings")
        db.execute("CREATE UNIQUE INDEX bindings_live_thread_idx ON bindings(t3_thread_id) WHERE state IN ('active','paused')")
        db.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
        db.commit()
    return store


class ContinuationMigrationTests(unittest.TestCase):
    def test_public_v3_migration_preserves_empty_baseline_audit_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = create_public_v3_database(pathlib.Path(directory) / "state")
            key = store.key_path.read_bytes()
            with store._connect() as db:
                before = {t: [dict(row) for row in db.execute(f"SELECT * FROM {t}")] for t in ("bindings", "events", "binding_lineage")}
            store.initialize()
            store.initialize()
            with store._connect() as db:
                after = {t: [dict(row) for row in db.execute(f"SELECT * FROM {t}")] for t in before}
                self.assertEqual(db.execute("SELECT count(*) FROM source_reservations").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM notifications").fetchone()[0], 0)
            for row in before["bindings"]:
                row["binding_schema_version"] = 4
            self.assertEqual(before, after)
            self.assertEqual(store.key_path.read_bytes(), key)
            self.assertTrue(store.get_binding("mission-2").baseline_captured)
            self.assertEqual(store.get_binding("mission-2").cursor_sequence, 0)

    def test_public_v3_migration_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            store = create_public_v3_database(pathlib.Path(directory) / "state")
            before = store.db_path.read_bytes()
            key = store.key_path.read_bytes()
            with mock.patch.object(store, "_create_extensions", side_effect=continuation_state.ContinuationStateError("fixture")):
                with self.assertRaises(continuation_state.ContinuationStateError):
                    store.initialize()
            self.assertEqual(store.db_path.read_bytes(), before)
            self.assertEqual(store.key_path.read_bytes(), key)


    def test_public_v2_migration_preserves_authority_signed_events_and_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = create_public_v2_database(pathlib.Path(temporary) / "state")
            key_before = store.key_path.read_bytes()
            with store._connect() as db:
                bindings_before = [dict(row) for row in db.execute("SELECT * FROM bindings ORDER BY binding_id")]
                events_before = [tuple(row) for row in db.execute("SELECT * FROM events")]
                lineage_before = [tuple(row) for row in db.execute("SELECT * FROM binding_lineage")]
            store.initialize()
            store.initialize()  # Restart validates v3 without changing authority or audit.
            self.assertEqual(store.status()["schema_version"], 4)
            self.assertEqual(store.key_path.read_bytes(), key_before)
            with store._connect() as db:
                self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM events")], events_before)
                self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM binding_lineage")], lineage_before)
                for before in bindings_before:
                    after = dict(db.execute("SELECT * FROM bindings WHERE binding_id=?", (before["binding_id"],)).fetchone())
                    self.assertEqual(after.pop("baseline_captured"), int(before["cursor_sequence"] > 0))
                    self.assertEqual(after.pop("binding_schema_version"), 4)
                    before.pop("binding_schema_version")
                    self.assertEqual(after, before)
                event = db.execute("SELECT * FROM events").fetchone()
                self.assertTrue(store.verify(json.loads(event["envelope_json"]), event["envelope_mac"]))
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            successor = store.get_binding("mission-2")
            self.assertFalse(successor.baseline_captured)  # Unknown legacy zero stays unarmed.
            self.assertFalse(store.ingest(successor, cursor_sequence=2,
                                         event=envelope(successor, sequence=2, event_id="replayed", turn_id="turn-1")))
            with self.assertRaises(continuation_state.ContinuationStateError):
                store.set_binding_state("mission-1", "active")

    def test_public_v2_migration_failure_rolls_back_database_and_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = create_public_v2_database(pathlib.Path(temporary) / "state")
            with store._connect() as db:
                db.execute("CREATE TRIGGER fail_schema_update BEFORE UPDATE OF value ON meta "
                           "BEGIN SELECT RAISE(ABORT, 'forced migration failure'); END")
            before = store.db_path.read_bytes(), store.key_path.read_bytes()
            with self.assertRaisesRegex(continuation_state.ContinuationStateError, "database operation failed"):
                store.initialize()
            self.assertEqual((store.db_path.read_bytes(), store.key_path.read_bytes()), before)

    def test_nonpublic_v2_and_dirty_public_v2_fail_closed_without_mutation(self):
        for variant in ("experimental", "missing_live_index", "wrong_discriminator"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as temporary:
                data_dir = pathlib.Path(temporary) / "state"
                if variant == "experimental":
                    store = create_v1_database(data_dir)
                    with store._connect() as db:
                        db.execute("ALTER TABLE bindings ADD COLUMN baseline_captured INTEGER NOT NULL DEFAULT 0")
                        db.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
                else:
                    store = create_public_v2_database(data_dir)
                    with store._connect() as db:
                        if variant == "missing_live_index":
                            db.execute("DROP INDEX bindings_live_thread_idx")
                        else:
                            db.execute("PRAGMA writable_schema=ON")
                            db.execute("UPDATE sqlite_master SET sql=replace(sql, 'binding_schema_version = 2', "
                                       "'binding_schema_version >= 2') WHERE name='bindings'")
                before = store.db_path.read_bytes(), store.key_path.read_bytes()
                with self.assertRaisesRegex(continuation_state.ContinuationStateError, "not canonical"):
                    store.initialize()
                self.assertEqual((store.db_path.read_bytes(), store.key_path.read_bytes()), before)

    def test_retained_v2_writer_cannot_insert_into_migrated_v3(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = create_public_v2_database(pathlib.Path(temporary) / "state")
            store.initialize()
            with store._connect() as db:
                columns = continuation_state.V2_BINDING_COLUMNS
                old = dict(db.execute("SELECT * FROM bindings WHERE binding_id='mission-2'").fetchone())
                old.update(binding_id="old-worker", t3_thread_id="other-thread", binding_schema_version=2)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "binding_schema_version"):
                    db.execute(f"INSERT INTO bindings({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                               tuple(old[column] for column in columns))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM bindings").fetchone()[0], 2)

    def test_v2_discriminator_has_no_default_and_rejects_legacy_insert(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = continuation_state.ContinuationStore(
                pathlib.Path(temporary) / "plugin-data"
            )
            store.initialize()
            values = binding_values()
            values.update(
                state="active",
                cursor_sequence=0,
                created_at="2099-01-01T00:00:00Z",
                updated_at="2099-01-01T00:00:00Z",
            )
            columns = continuation_state.V1_BINDING_COLUMNS

            with store._connect() as db:
                discriminator = next(
                    row
                    for row in db.execute("PRAGMA table_info(bindings)")
                    if row["name"] == "binding_schema_version"
                )
                self.assertEqual(discriminator["notnull"], 1)
                self.assertIsNone(discriminator["dflt_value"])
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "binding_schema_version"
                ):
                    db.execute(
                        f"INSERT INTO bindings({','.join(columns)}) "
                        f"VALUES({','.join('?' for _ in columns)})",
                        tuple(values[column] for column in columns),
                    )
                self.assertIsNone(
                    db.execute(
                        "SELECT 1 FROM bindings WHERE binding_id=?",
                        (values["binding_id"],),
                    ).fetchone()
                )

    def test_retained_v1_supervisor_cannot_adopt_v2_successor(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = pathlib.Path(temporary) / "plugin-data"
            store = create_v1_database(data_dir)
            predecessor = populate_exhausted_v1_database(store)
            retained_v1 = RetainedV1SupervisorFixture(store.db_path)

            store.initialize()
            successor = store.renew(
                predecessor.binding_id,
                **binding_values(binding_id="mission-2", source_identity="source-2"),
            )

            try:
                retained_v1.scan_once()
            except TypeError:
                pass
            else:
                self.fail(
                    "retained v1 supervisor adopted v2 successor: "
                    f"adoptions={retained_v1.adoptions}, "
                    f"ingestions={retained_v1.ingestions}, "
                    f"deliveries={retained_v1.deliveries}"
                )
            self.assertEqual(retained_v1.adoptions, 0)
            self.assertEqual(retained_v1.ingestions, 0)
            self.assertEqual(retained_v1.deliveries, 0)
            self.assertEqual(
                store.status(successor.binding_id)["bindings"][0]["events"], {}
            )

    def test_v1_to_v2_preserves_authority_events_key_permissions_and_foreign_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = pathlib.Path(temporary) / "plugin-data"
            store = create_v1_database(data_dir)
            binding_before = populate_exhausted_v1_database(store)
            key_before = store.key_path.read_bytes()

            store.initialize()

            self.assertEqual(store.status()["schema_version"], 4)
            migrated = store.get_binding(binding_before.binding_id)
            for field in LegacyV1BindingFixture.__dataclass_fields__:
                self.assertEqual(getattr(migrated, field), getattr(binding_before, field))
            self.assertEqual(migrated.binding_schema_version, 4)
            self.assertEqual(
                store.status(binding_before.binding_id)["bindings"][0]["events"],
                {"acknowledged": 1},
            )
            self.assertEqual(store.key_path.read_bytes(), key_before)
            self.assertEqual(store.data_dir.stat().st_mode & 0o777, 0o700)
            self.assertEqual(store.db_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(store.key_path.stat().st_mode & 0o777, 0o600)
            with store._connect() as db:
                self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(
                        "INSERT INTO events(binding_id,source_sequence,source_event_id,"
                        "source_turn_id,event_kind,occurred_at,envelope_json,envelope_mac,"
                        "status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            "missing", 9, "x", "x", "external_tool_error",
                            "2099-01-01T00:00:00Z", "{}", "x", "blocked",
                            "2099-01-01T00:00:00Z", "2099-01-01T00:00:00Z",
                        ),
                    )

    def test_failed_v1_migration_rolls_back_exact_database_and_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = pathlib.Path(temporary) / "plugin-data"
            store = create_v1_database(data_dir)
            populate_exhausted_v1_database(store)
            with closing(sqlite3.connect(store.db_path)) as db:
                db.execute(
                    """CREATE TRIGGER fail_schema_update BEFORE UPDATE OF value ON meta
                    BEGIN SELECT RAISE(ABORT, 'forced migration failure'); END"""
                )
                db.commit()
            database_before = hashlib.sha256(store.db_path.read_bytes()).digest()
            key_before = hashlib.sha256(store.key_path.read_bytes()).digest()
            modes_before = (
                store.data_dir.stat().st_mode & 0o777,
                store.db_path.stat().st_mode & 0o777,
                store.key_path.stat().st_mode & 0o777,
            )

            with self.assertRaisesRegex(
                continuation_state.ContinuationStateError,
                "database operation failed",
            ):
                store.initialize()

            self.assertEqual(hashlib.sha256(store.db_path.read_bytes()).digest(), database_before)
            self.assertEqual(hashlib.sha256(store.key_path.read_bytes()).digest(), key_before)
            self.assertEqual(
                (
                    store.data_dir.stat().st_mode & 0o777,
                    store.db_path.stat().st_mode & 0o777,
                    store.key_path.stat().st_mode & 0o777,
                ),
                modes_before,
            )
            with closing(sqlite3.connect(store.db_path)) as db:
                self.assertEqual(
                    db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                    "1",
                )
                self.assertEqual(db.execute("SELECT COUNT(*) FROM bindings").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
                self.assertIn("UNIQUE", db.execute(
                    "SELECT sql FROM sqlite_master WHERE name='bindings'"
                ).fetchone()[0])

    def test_dirty_v1_is_rejected_without_silent_schema_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = create_v1_database(pathlib.Path(temporary) / "plugin-data")
            with closing(sqlite3.connect(store.db_path)) as db:
                db.execute("DROP INDEX events_dispatch_idx")
                db.commit()
            database_before = hashlib.sha256(store.db_path.read_bytes()).digest()

            with self.assertRaisesRegex(
                continuation_state.ContinuationStateError, "indexes are not canonical"
            ):
                store.initialize()

            self.assertEqual(hashlib.sha256(store.db_path.read_bytes()).digest(), database_before)
            with closing(sqlite3.connect(store.db_path)) as db:
                self.assertEqual(
                    db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                    "1",
                )
                self.assertIsNone(db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='events_dispatch_idx'"
                ).fetchone())

    def test_future_schema_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = pathlib.Path(temporary) / "plugin-data"
            store = continuation_state.ContinuationStore(data_dir)
            store.initialize()
            with store._connect() as db:
                db.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
            with self.assertRaisesRegex(
                continuation_state.ContinuationStateError, "newer than this plugin"
            ):
                store.initialize()


if __name__ == "__main__":
    unittest.main()
