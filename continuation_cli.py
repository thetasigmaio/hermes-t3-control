"""Operator CLI for explicit T3 continuation bindings."""

from __future__ import annotations

import json
from typing import Any

try:
    from . import continuation_notifications
    from .continuation_state import ContinuationStateError, ContinuationStore, utc_now
    from .continuation_transport import read_thread_snapshot, ContinuationTransportError
    from .client import T3ClientError
except ImportError:
    import continuation_notifications
    from continuation_state import ContinuationStateError, ContinuationStore, utc_now
    from continuation_transport import read_thread_snapshot, ContinuationTransportError
    from client import T3ClientError


def setup_continuation_cli(parser: Any) -> None:
    actions = parser.add_subparsers(dest="continuation_action", required=True)
    bind = actions.add_parser("bind", help="Bind one exact T3 source to one existing Hermes session")
    _add_binding_arguments(bind)
    renew = actions.add_parser("renew", help="Renew a stopped, acknowledged mission with explicit new authority")
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


def _add_binding_arguments(parser: Any, *, renewal: bool = False) -> None:
    parser.add_argument("--notify-telegram", action="store_true", help="Notify the configured Telegram home after Desktop PM verification")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-message-id")
    source.add_argument("--attach-current-turn", help="Attach only the exact currently running turn from a trusted snapshot")
    parser.add_argument("--desktop-ws-url")
    parser.add_argument("--desktop-upgrade", action="store_true")
    if renewal:
        parser.add_argument("--replace-session-id", help="Explicitly replace this predecessor physical session when attaching a current turn")
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
    parser.add_argument("--sunsama-task-id", default="", help="Optional external ledger task ID")
    parser.add_argument("--source-identity", required=True)
    parser.add_argument("--followup-scope", required=renewal)
    parser.add_argument("--max-continuations", type=int, required=renewal)


def _current_turn_source(snapshot: dict, thread_id: str, turn_id: str) -> str:
    """Only the authoritative running snapshot can associate a nullable native user row."""
    thread = snapshot["thread"]
    latest, session = thread.get("latestTurn") or {}, thread.get("session") or {}
    if (thread.get("id") != thread_id or thread.get("archivedAt") or thread.get("deletedAt")
            or latest.get("state") != "running" or latest.get("turnId") != turn_id
            or latest.get("completedAt") is not None
            or session.get("status") != "running" or session.get("activeTurnId") != turn_id
            or session.get("threadId") != thread_id):
        raise ValueError("current-turn attachment requires the exact running source turn")
    requested_at = latest.get("requestedAt")
    candidates = [m for m in thread.get("messages", []) if m.get("role") == "user"
        and (m.get("turnId") == turn_id or ("turnId" in m and m["turnId"] is None
             and isinstance(requested_at, str) and requested_at and m.get("createdAt") == requested_at))]
    if (len(candidates) != 1 or candidates[0].get("streaming") is not False
            or candidates[0].get("historyImport")
            or (candidates[0].get("metadata") or {}).get("historyImport")):
        raise ValueError("current-turn source message provenance is missing or ambiguous")
    return candidates[0]["id"]


def make_continuation_cli_handler(ctx: Any):
    def handle(args: Any) -> int:
        store = ContinuationStore(ctx.state.data_dir)
        try:
            store.initialize()
            action = args.continuation_action
            if action in {"bind", "renew"}:
                try:
                    from .continuation import binding_enabled
                except ImportError:
                    from continuation import binding_enabled
                if not binding_enabled(ctx, args.binding_id):
                    raise ValueError("continuation binding is excluded by current configuration")
                current_turn = getattr(args, "attach_current_turn", None)
                source_message_id = getattr(args, "source_message_id", None)
                replace_session_id = getattr(args, "replace_session_id", None)
                if replace_session_id is not None and (action != "renew" or not current_turn or args.platform != "desktop"):
                    raise ValueError("session replacement requires Desktop current-turn renewal")
                if current_turn and (source_message_id or args.platform != "desktop"):
                    raise ValueError("current-turn attachment requires Desktop and excludes a future source message")
                if current_turn and (args.followup_scope is None or args.max_continuations is None):
                    raise ValueError("current-turn attachment requires explicit scope and continuation budget")
                values = dict(
                    binding_id=args.binding_id,
                    profile_name=str(getattr(ctx, "profile_name", "default")),
                    t3_thread_id=args.thread_id,
                    t3_owner_id=args.owner_id,
                    t3_environment_id=args.environment_id,
                    hermes_session_key=args.session_key,
                    hermes_session_id=args.session_id,
                    platform=args.platform,
                    user_id=args.user_id,
                    chat_id=args.chat_id,
                    topic_id=args.topic_id,
                    sunsama_task_id=args.sunsama_task_id,
                    source_identity=args.source_identity,
                    followup_scope=args.followup_scope if args.followup_scope is not None else "none",
                    max_continuations=args.max_continuations if args.max_continuations is not None else 1,
                )
                store._normalize_binding_values(values)
                desktop = args.platform == "desktop"
                if desktop:
                    if not source_message_id and not current_turn:
                        raise ValueError("Desktop handoff requires an explicit source message id")
                    try:
                        from .continuation_transport import operator_desktop_readiness
                    except ImportError:
                        from continuation_transport import operator_desktop_readiness
                    operator_desktop_readiness(ctx, {"profile_name": values["profile_name"], "session_id": args.session_id}, getattr(args, "desktop_ws_url", None))
                elif getattr(args, "desktop_upgrade", False):
                    raise ValueError("desktop upgrade requires a Desktop destination")
                notification_target = continuation_notifications.resolve_requested_target(ctx, getattr(args, "notify_telegram", False))
                if notification_target and not desktop:
                    raise ValueError("additional Telegram notice requires a Desktop primary destination")
                captured_at = utc_now()
                snapshot = read_thread_snapshot(
                    ctx, thread_id=args.thread_id, environment_id=args.environment_id
                )
                attachment = None
                if desktop:
                    thread = snapshot["thread"]
                    latest = thread.get("latestTurn") or {}
                    session = thread.get("session") or {}
                    if current_turn:
                        source_message_id = _current_turn_source(snapshot, args.thread_id, current_turn)
                        attachment = (source_message_id, current_turn)
                    elif latest.get("state") == "running" or session.get("status") in {"starting", "running"}:
                        raise ValueError("required continuation cannot register against a busy source")
                baseline = (snapshot["snapshotSequence"], captured_at)
                if action == "renew":
                    binding = store.renew(args.replaces, baseline=baseline, current_turn=attachment,
                        replace_session_id=replace_session_id,
                        desktop_upgrade=getattr(args, "desktop_upgrade", False), **values)
                else:
                    binding = store.bind(baseline=baseline, current_turn=attachment, **values)
                continuation_notifications.register_target(store, binding, notification_target)
                if desktop and not current_turn:
                    store.reserve_source(binding, source_message_id,
                        excluded_turn_ids=[latest.get("turnId"), session.get("activeTurnId")])
                status = store.status(binding.binding_id)["bindings"][0]
                output = {
                    "ok": True,
                    "binding_id": binding.binding_id,
                    "state": binding.state,
                    "cursor_sequence": binding.cursor_sequence,
                    "baseline_captured": status["baseline_captured"],
                    "armed": status["armed"],
                    "consumer_ready": desktop,
                    "notification_target": notification_target,
                }
                if action == "renew":
                    output["replaces_binding_id"] = args.replaces
            elif action == "status":
                output = {"ok": True, **store.status(args.binding_id), "notifications": continuation_notifications.status(store, args.binding_id)}
                try:
                    from .continuation import binding_enabled
                except ImportError:
                    from continuation import binding_enabled
                for binding in output["bindings"]:
                    binding["observer_excluded"] = not binding_enabled(ctx, binding["binding_id"])
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
