from __future__ import annotations

import pathlib
import json
import sqlite3
import tempfile
import unittest

import continuation_state
import continuation


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


class DesktopContinuationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = continuation_state.ContinuationStore(
            pathlib.Path(self.temporary.name) / "plugin-data"
        )
        self.store.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_current_turn_attachment_is_atomic_signed_and_never_remapped_or_rearmed(self):
        values = binding_values(platform="desktop", hermes_session_key="session-1",
                                user_id="", chat_id="", topic_id="", max_continuations=2)
        baseline = (10, "2026-09-08T10:00:00Z")
        with self.assertRaises(ValueError):
            self.store.bind(baseline=baseline, current_turn=("", "turn-1"), **values)
        self.assertEqual(self.store.status()["bindings"], [])
        binding = self.store.bind(baseline=baseline, current_turn=("message-1", "turn-1"), **values)
        snapshot = {"thread": {"messages": [{"id": "message-1", "role": "user", "turnId": None}]}}
        for turn in ("old-turn", "later-turn"):
            self.assertIsNone(continuation.reserved_event(self.store, binding,
                envelope(binding, sequence=11, turn_id=turn), snapshot))
        event = continuation.reserved_event(self.store, binding, envelope(binding, sequence=11), snapshot)
        self.assertFalse(self.store.ingest(binding, cursor_sequence=10, event=event))
        self.assertTrue(self.store.ingest(binding, cursor_sequence=11, event=event))
        row = self.store.claim_next(binding.binding_id)
        self.assertTrue(self.store.dispatch_is_eligible(binding, 11, row["envelope_mac"]))
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.reserve_source(binding, "another-message")
        self.store.set_binding_state(binding.binding_id, "cancelled")
        self.assertFalse(self.store.dispatch_is_eligible(binding, 11, row["envelope_mac"]))
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.bind(baseline=baseline, current_turn=("message-1", "turn-1"), **values)
        with self.store._connect() as db:
            value = json.loads(db.execute("SELECT envelope_json FROM source_reservations").fetchone()[0])
            value["correlation"]["turn_id"] = "forged-turn"
            db.execute("UPDATE source_reservations SET envelope_json=?", (json.dumps(value),))
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.reserved_turn(binding)

    def test_current_turn_renewal_preserves_predecessor_receipt_requirements(self):
        predecessor = self.store.bind(**binding_values())
        self.store.set_binding_state(predecessor.binding_id, "stopped")
        values = binding_values(binding_id="mission-2", platform="desktop", hermes_session_key="session-1",
                                user_id="", chat_id="", topic_id="")
        kwargs = dict(baseline=(10, "2026-09-08T10:00:00Z"), desktop_upgrade=True,
                      current_turn=("current-message", "current-turn"), **values)
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(predecessor.binding_id, **kwargs)
        self.store.set_binding_state(predecessor.binding_id, "active")
        self.store.ingest(predecessor, cursor_sequence=1, event=envelope(predecessor))
        self.store.claim_next(predecessor.binding_id)
        self.store.finish(predecessor.binding_id, 1, "completed", receipt={"status": "completed"})
        self.store.acknowledge(predecessor.binding_id, "event-1")
        self.store.set_binding_state(predecessor.binding_id, "stopped")
        successor = self.store.renew(predecessor.binding_id, **kwargs)
        self.assertEqual(self.store.reserved_turn(successor), "current-turn")
        self.assertEqual(successor.cursor_sequence, 10)
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(predecessor.binding_id, **kwargs)

    def test_session_replacement_requires_exact_old_session_and_explicit_scoped_renewal(self):
        predecessor = self.completed_acknowledged_binding()
        self.store.set_binding_state(predecessor.binding_id, "stopped")
        values = binding_values(binding_id="mission-2", platform="desktop", hermes_session_key="new-session",
            hermes_session_id="new-session", user_id="", chat_id="", topic_id="", followup_scope="new-scope")
        kwargs = dict(baseline=(10, "2026-09-08T10:00:00Z"), desktop_upgrade=True,
                      current_turn=("current-message", "current-turn"), **values)
        for overrides in ({}, {"replace_session_id": "wrong-session"},
                          {"replace_session_id": "session-1", "current_turn": None},
                          {"replace_session_id": "session-1", "desktop_upgrade": False},
                          {"replace_session_id": "session-1", "hermes_session_id": "session-1", "hermes_session_key": "session-1"},
                          {"replace_session_id": "session-1", "t3_owner_id": "other-owner"},
                          {"replace_session_id": "session-1", "t3_environment_id": "other-environment"}):
            with self.subTest(overrides=overrides), self.assertRaises((ValueError, continuation_state.ContinuationStateError)):
                self.store.renew(predecessor.binding_id, **{**kwargs, **overrides})
        successor = self.store.renew(predecessor.binding_id, replace_session_id="session-1", **kwargs)
        self.assertEqual(successor.hermes_session_id, "new-session")
        self.assertEqual(self.store.reserved_turn(successor), "current-turn")
        self.assertEqual(self.store.get_binding(predecessor.binding_id).hermes_session_id, "session-1")
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(predecessor.binding_id, replace_session_id="session-1", **kwargs)
        next_values = {**values, "binding_id": "mission-3", "hermes_session_key": "next-session",
                       "hermes_session_id": "next-session"}
        next_kwargs = dict(baseline=(20, "2026-09-08T10:00:00Z"), current_turn=("next-message", "next-turn"),
                           replace_session_id="new-session", **next_values)
        self.store.set_binding_state(successor.binding_id, "stopped")
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(successor.binding_id, **next_kwargs)  # No acknowledged completion yet.
        self.store.set_binding_state(successor.binding_id, "active")
        event = {**envelope(successor, sequence=11, event_id="current-event", turn_id="current-turn"), "source_message_id": "current-message"}
        self.store.ingest(successor, cursor_sequence=11, event=event)
        self.store.claim_next(successor.binding_id)
        self.store.finish(successor.binding_id, 11, "completed", receipt={"status": "completed"})
        self.store.acknowledge(successor.binding_id, "current-event")
        self.store.set_binding_state(successor.binding_id, "stopped")
        with self.assertRaises(continuation_state.ContinuationStateError):
            self.store.renew(successor.binding_id, desktop_upgrade=True, **next_kwargs)
        self.assertEqual(self.store.renew(successor.binding_id, **next_kwargs).hermes_session_id, "next-session")

    def completed_acknowledged_binding(self):
        binding = self.store.bind(**binding_values(followup_scope="old-scope"))
        self.store.ingest(binding, cursor_sequence=1, event=envelope(binding))
        self.store.claim_next(binding.binding_id)
        self.store.finish(binding.binding_id, 1, "completed", receipt={"status": "completed"})
        self.store.acknowledge(binding.binding_id, "event-1")
        return binding

