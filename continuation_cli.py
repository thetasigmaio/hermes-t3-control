"""Operator CLI for explicit T3 continuation bindings."""

from __future__ import annotations

import json
from typing import Any

try:
    from .continuation_state import ContinuationStateError, ContinuationStore, utc_now
    from .continuation_transport import read_thread_snapshot, ContinuationTransportError
    from .client import T3ClientError
except ImportError:
    from continuation_state import ContinuationStateError, ContinuationStore, utc_now
    from continuation_transport import read_thread_snapshot, ContinuationTransportError
    from client import T3ClientError


def _add_binding_arguments(parser: Any, *, renewal: bool = False) -> None:
    parser.add_argument("--binding-id", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--session-key", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--chat-id", required=True)
    parser.add_argument("--topic-id", default="", required=renewal)
    parser.add_argument("--sunsama-task-id", required=True)
    parser.add_argument("--source-identity", required=True)
    parser.add_argument("--followup-scope", default="none", required=renewal)
    parser.add_argument("--max-continuations", type=int, default=1, required=renewal)


def setup_continuation_cli(parser: Any) -> None:
    actions = parser.add_subparsers(dest="continuation_action", required=True)
    bind = actions.add_parser("bind", help="Bind one exact T3 source to one existing Hermes session")
    _add_binding_arguments(bind)
    renew = actions.add_parser(
        "renew", help="Renew one exhausted binding with a new explicit authority"
    )
    renew.add_argument("--replaces", required=True)
    _add_binding_arguments(renew, renewal=True)
    status = actions.add_parser("status", help="Show bounded continuation ledger status")
    status.add_argument("--binding-id")
    for name in ("resume", "pause", "stop", "cancel"):
        action = actions.add_parser(name, help=f"{name.title()} one exact binding")
        action.add_argument("binding_id")
    acknowledge = actions.add_parser("ack", help="Acknowledge one completed event")
    acknowledge.add_argument("binding_id")
    acknowledge.add_argument("source_event_id")


def make_continuation_cli_handler(ctx: Any):
    def binding_values(args: Any) -> dict[str, Any]:
        return {
            "binding_id": args.binding_id,
            "profile_name": str(getattr(ctx, "profile_name", "default")),
            "t3_thread_id": args.thread_id,
            "t3_owner_id": args.owner_id,
            "t3_environment_id": args.environment_id,
            "hermes_session_key": args.session_key,
            "hermes_session_id": args.session_id,
            "platform": args.platform,
            "user_id": args.user_id,
            "chat_id": args.chat_id,
            "topic_id": args.topic_id,
            "sunsama_task_id": args.sunsama_task_id,
            "source_identity": args.source_identity,
            "followup_scope": args.followup_scope,
            "max_continuations": args.max_continuations,
        }

    def handle(args: Any) -> int:
        store = ContinuationStore(ctx.state.data_dir)
        try:
            store.initialize()
            action = args.continuation_action
            if action in {"bind", "renew"}:
                values = binding_values(args)
                store._normalize_binding_values(values)
                captured_at = utc_now()
                snapshot = read_thread_snapshot(
                    ctx, thread_id=args.thread_id, environment_id=args.environment_id
                )
                baseline = (snapshot["snapshotSequence"], captured_at)
                binding = (
                    store.renew(args.replaces, baseline=baseline, **values)
                    if action == "renew" else store.bind(baseline=baseline, **values)
                )
                status = store.status(binding.binding_id)["bindings"][0]
                output = {
                    "ok": True,
                    "binding_id": binding.binding_id,
                    **({"replaces_binding_id": args.replaces} if action == "renew" else {}),
                    "state": binding.state,
                    "cursor_sequence": binding.cursor_sequence,
                    "baseline_captured": status["baseline_captured"],
                    "armed": status["armed"],
                }
            elif action == "status":
                output = {"ok": True, **store.status(args.binding_id)}
            elif action in {"resume", "pause", "stop", "cancel"}:
                state = {
                    "resume": "active",
                    "pause": "paused",
                    "stop": "stopped",
                    "cancel": "cancelled",
                }[action]
                binding = store.set_binding_state(args.binding_id, state)
                output = {"ok": True, "binding_id": binding.binding_id, "state": binding.state}
            elif action == "ack":
                store.acknowledge(args.binding_id, args.source_event_id)
                output = {"ok": True, "binding_id": args.binding_id, "status": "acknowledged"}
            else:
                raise ValueError("unsupported continuation action")
        except (ContinuationStateError, ContinuationTransportError, T3ClientError, OSError, ValueError) as exc:
            output = {"ok": False, "error": str(exc)}
            print(json.dumps(output, sort_keys=True))
            return 1
        print(json.dumps(output, sort_keys=True))
        return 0

    return handle
