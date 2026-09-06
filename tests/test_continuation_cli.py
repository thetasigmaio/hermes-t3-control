from __future__ import annotations

import argparse
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
                },
            )


if __name__ == "__main__":
    unittest.main()
