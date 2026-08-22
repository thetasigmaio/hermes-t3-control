"""Synchronous Hermes handlers for bounded T3 thread orchestration."""

from __future__ import annotations

import copy
import json
import math
import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any

try:
    from .client import (
        ConflictError,
        ConfigurationError,
        INTERACTION_MODES,
        InvalidInputError,
        MAX_CURSOR_CHARS,
        MAX_IDENTIFIER_CHARS,
        MAX_TITLE_CHARS,
        MAX_TURN_LIMIT,
        RUNTIME_MODES,
        T3Client,
        T3ClientError,
        is_valid_bearer_credential,
        normalize_message,
        normalize_string,
        validate_turn_limit,
    )
except ImportError:  # Direct repository import used by unit tests.
    from client import (
        ConflictError,
        ConfigurationError,
        INTERACTION_MODES,
        InvalidInputError,
        MAX_CURSOR_CHARS,
        MAX_IDENTIFIER_CHARS,
        MAX_TITLE_CHARS,
        MAX_TURN_LIMIT,
        RUNTIME_MODES,
        T3Client,
        T3ClientError,
        is_valid_bearer_credential,
        normalize_message,
        normalize_string,
        validate_turn_limit,
    )


TOKEN_ENV = "T3_ORCHESTRATION_TOKEN"
TOOLSET = "t3_control"
MAX_BRANCH_CHARS = 512
MAX_WORKTREE_PATH_CHARS = 4_096
MAX_MODEL_OPTIONS = 64
MAX_MODEL_OPTION_CHARS = 512
FULL_ACCESS_WARNING = (
    "full-access permits trusted provider work to execute commands and modify or delete "
    "files without approval."
)
MODE_RACE_SEMANTICS = (
    "T3 has no atomic expected-mode guard; pre-read and post-read verification cannot "
    "exclude a transient concurrent mode change before provider acceptance."
)
PENDING_APPROVAL_WARNING = (
    "Changing the persisted runtime mode after a turn starts cannot cancel, remove, or "
    "retroactively authorize an approval already pending for that turn."
)
PLAN_IMPLEMENT_PREFIX = "PLEASE IMPLEMENT THIS PLAN:\n"
PLAN_RACE_SEMANTICS = (
    "T3 has no atomic expected-mode, idle-state, unimplemented-plan, or at-most-once guard; "
    "success proves observed ordering and same-thread provenance but cannot exclude a "
    "transient mode change or duplicate concurrent implementation work."
)
THREAD_VIEWS = frozenset({"compact", "raw"})
READ_VIEWS = frozenset({"material", "raw"})
THREAD_LIFECYCLES = frozenset(
    {"idle", "starting", "running", "ready", "interrupted", "stopped", "error", "blocked"}
)
BUSY_POLICIES = frozenset({"reject", "queue"})
WAIT_UNTIL = frozenset({"change", "running", "blocked", "terminal", "error"})
APPROVAL_DECISIONS = frozenset({"accept", "acceptForSession", "decline", "cancel"})
MAX_UPDATED_WITHIN_MINUTES = 10_080
MAX_COMPACT_LIMIT = 50
MAX_WAIT_SECONDS = 30
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
MAX_MODEL_TEXT_UTF16_UNITS = 8_192
MAX_MODEL_PLAN_UTF16_UNITS = 16_384
MAX_MODEL_COLLECTION_ITEMS = 32
MAX_MODEL_PROJECTION_DEPTH = 5
QUEUE_SEMANTICS = (
    "Explicit busy_policy queue acknowledges that T3 may start immediately or queue "
    "the exact persisted message; the server has no atomic idle guard."
)
RESPONSE_RACE_WARNING = (
    "T3 has no atomic expected-turn guard for pending responses. This best-effort "
    "current provider session response was revalidated immediately before dispatch; "
    "a residual same-user race remains."
)
AUTH_CLEANUP_FAILURE_WARNING = (
    "Temporary T3 authentication cleanup could not be confirmed. The short-lived "
    "session expires automatically. Do not repeat an accepted mutation before "
    "reconciling its exact state."
)


def _profile_secret(name: str) -> str | None:
    try:
        from . import auth
    except ImportError:
        import auth
    return auth._profile_secret(name)


def check_t3_available() -> bool:
    """Passive availability probe; operation auth validates the selected mode."""
    return True


def _make_client(ctx: Any) -> T3Client:
    try:
        base_url = ctx.get_config("base_url", "")
    except Exception as exc:
        raise ConfigurationError("The plugin base_url setting is unavailable.") from exc
    try:
        token = _profile_secret(TOKEN_ENV)
    except Exception as exc:
        raise ConfigurationError("The profile-scoped T3 credential is unavailable.") from exc
    if not is_valid_bearer_credential(token):
        raise ConfigurationError("The profile-scoped T3 credential is unavailable.")
    return T3Client(base_url, token)


_DEFAULT_MAKE_CLIENT = _make_client


class _CompatibilityLease:
    def __init__(self, client: T3Client) -> None:
        self.client = client

    @staticmethod
    def annotate(payload: dict[str, Any]) -> dict[str, Any]:
        return payload


@contextmanager
def _operation_client(ctx: Any, public_arguments: dict[str, Any]):
    if _make_client is not _DEFAULT_MAKE_CLIENT:
        transport = _make_client(ctx)
        transport._preflight_public_arguments(public_arguments)
        yield _CompatibilityLease(transport)
        return
    try:
        from . import auth
    except ImportError:
        import auth
    with auth.operation_client(ctx, public_arguments) as lease:
        yield lease


def _execute_operation(
    ctx: Any,
    public_arguments: dict[str, Any],
    operation: Callable[[T3Client], dict[str, Any]],
) -> dict[str, Any]:
    lease = None
    payload: dict[str, Any] | None = None
    with _operation_client(ctx, public_arguments) as lease:
        payload = operation(lease.client)
    if lease is None or payload is None:
        raise T3ClientError()
    return lease.annotate(payload)


def _now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_result(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def bind_handler(ctx: Any, operation: Callable[[Any, Any], dict[str, Any]]) -> Callable[..., str]:
    """Bind a plugin context without constructing a client or performing I/O."""

    @wraps(operation)
    def handler(args: Any, **kwargs: Any) -> str:
        del kwargs
        try:
            payload = operation(ctx, args)
            return _json_result({"ok": True, **payload})
        except T3ClientError as exc:
            return _json_result(exc.to_dict())
        except Exception as exc:
            try:
                cleanup = getattr(exc, "auth_cleanup_metadata", None)
            except Exception:
                cleanup = None
            details = (
                {
                    "auth_cleanup": "failed",
                    "auth_cleanup_warning": AUTH_CLEANUP_FAILURE_WARNING,
                }
                if isinstance(cleanup, dict) and cleanup.get("auth_cleanup") == "failed"
                else None
            )
            return _json_result(T3ClientError(details=details).to_dict())

    return handler


def _args(value: Any, *, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidInputError("Tool arguments must be an object.")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        _invalid("Tool arguments contain unknown fields.")
    if missing:
        _invalid("Tool arguments are missing required fields.")
    return value


def _invalid(message: str) -> None:
    raise InvalidInputError(message)


def _enum(value: Any, name: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        _invalid(f"{name} has an unsupported value.")
    return value


def _configured_runtime_mode(ctx: Any) -> str:
    try:
        value = ctx.get_config("default_runtime_mode", None)
    except Exception as exc:
        raise ConfigurationError(
            "The plugin default_runtime_mode setting is unavailable."
        ) from exc
    if value is None:
        return "approval-required"
    if not isinstance(value, str) or value not in RUNTIME_MODES:
        raise ConfigurationError("The plugin default_runtime_mode setting is unsupported.")
    return value


def _normalize_model_options(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _invalid("model_options must be an array.")
    if len(value) > MAX_MODEL_OPTIONS:
        _invalid("model_options exceeds 64 entries.")
    normalized: list[dict[str, Any]] = []
    option_ids: set[str] = set()
    for raw_option in value:
        if not isinstance(raw_option, dict) or set(raw_option) != {"id", "value"}:
            _invalid("Each model_options entry must contain only id and value.")
        option_id = normalize_string(
            raw_option["id"], "model_options.id", max_chars=MAX_MODEL_OPTION_CHARS
        )
        if option_id in option_ids:
            _invalid("model_options IDs must be unique.")
        option_value = raw_option["value"]
        if isinstance(option_value, bool):
            pass
        elif isinstance(option_value, str):
            if not option_value.strip():
                _invalid("model_options string values must not be empty.")
            if len(option_value) > MAX_MODEL_OPTION_CHARS:
                _invalid("model_options string values exceed their character limit.")
            try:
                option_value.encode("utf-8")
            except UnicodeEncodeError:
                _invalid("model_options string values contain invalid Unicode.")
        else:
            _invalid("model_options values must be non-empty strings or Booleans.")
        option_ids.add(option_id)
        normalized.append({"id": option_id, "value": option_value})
    return normalized


def _ensure_mutable_thread(thread: dict[str, Any]) -> None:
    if thread["deletedAt"] is not None or thread["archivedAt"] is not None:
        raise ConflictError("The target thread is deleted or archived.")


def _active_turn_id(thread: dict[str, Any]) -> str | None:
    current_session = thread["session"]
    if isinstance(current_session, dict):
        active_turn_id = current_session["activeTurnId"]
        if isinstance(active_turn_id, str) and active_turn_id:
            return active_turn_id
    latest = thread["latestTurn"]
    if isinstance(latest, dict) and latest["state"] == "running":
        return latest["turnId"]
    return None


def _build_turn_command(
    transport: T3Client,
    thread: dict[str, Any],
    text: str,
    *,
    source_plan: dict[str, str] | None = None,
) -> tuple[dict[str, Any], str]:
    message_id = transport.new_uuid4()
    command: dict[str, Any] = {
        "type": "thread.turn.start",
        "commandId": transport.new_uuid4(),
        "threadId": thread["id"],
        "message": {
            "messageId": message_id,
            "role": "user",
            "text": text,
            "attachments": [],
        },
        "modelSelection": copy.deepcopy(thread["modelSelection"]),
        "titleSeed": thread["title"],
        "runtimeMode": thread["runtimeMode"],
        "interactionMode": thread["interactionMode"],
        "createdAt": _now_rfc3339(),
    }
    if source_plan is not None:
        command["sourceProposedPlan"] = dict(source_plan)
    return command, message_id


def _turn_observed(
    detail: dict[str, Any],
    command: dict[str, Any],
    message_id: str,
    *,
    source_plan: dict[str, str] | None = None,
) -> bool:
    thread = detail["thread"]
    latest = thread["latestTurn"]
    messages = [
        item
        for item in thread["messages"]
        if item["id"] == message_id
        and item["role"] == "user"
        and item["text"] == command["message"]["text"]
    ]
    if (
        thread["modelSelection"] != command["modelSelection"]
        or thread["runtimeMode"] != command["runtimeMode"]
        or thread["interactionMode"] != command["interactionMode"]
        or not isinstance(latest, dict)
        or not any(item["turnId"] == latest["turnId"] for item in messages)
    ):
        return False
    if source_plan is None:
        return True
    if latest.get("sourceProposedPlan") != source_plan:
        return False
    plans = [item for item in thread["proposedPlans"] if item["id"] == source_plan["planId"]]
    return (
        len(plans) == 1
        and plans[0]["implementedAt"] is not None
        and plans[0]["implementationThreadId"] == source_plan["threadId"]
    )


def _annotate_error(exc: T3ClientError, **details: Any) -> T3ClientError:
    exc.details.update(details)
    return exc


def _bounded_integer(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _invalid(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        _invalid(f"{name} must be a Boolean.")
    return value


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _normalized_project(project: dict[str, Any]) -> dict[str, str]:
    return {
        "id": project["id"],
        "title": project["title"],
        "workspace": project["workspaceRoot"],
    }


def _truncate_utf16(value: str, maximum: int) -> tuple[str, bool, int]:
    units = sum(2 if ord(character) > 0xFFFF else 1 for character in value)
    if units <= maximum:
        return value, False, units
    consumed = 0
    end = 0
    for end, character in enumerate(value, start=1):
        width = 2 if ord(character) > 0xFFFF else 1
        if consumed + width > maximum:
            end -= 1
            break
        consumed += width
    return value[:end], True, units


def _bounded_model_value(value: Any, *, depth: int = 0) -> tuple[Any, bool]:
    if isinstance(value, str):
        projected, truncated, _units = _truncate_utf16(
            value, MAX_MODEL_TEXT_UTF16_UNITS
        )
        return projected, truncated
    if value is None or isinstance(value, (bool, int, float)):
        return value, False
    if depth >= MAX_MODEL_PROJECTION_DEPTH:
        return None, True
    if isinstance(value, list):
        projected_items: list[Any] = []
        truncated = len(value) > MAX_MODEL_COLLECTION_ITEMS
        for item in value[:MAX_MODEL_COLLECTION_ITEMS]:
            projected, item_truncated = _bounded_model_value(item, depth=depth + 1)
            projected_items.append(projected)
            truncated = truncated or item_truncated
        return projected_items, truncated
    if isinstance(value, dict):
        projected_items: dict[str, Any] = {}
        entries = list(value.items())
        truncated = len(entries) > MAX_MODEL_COLLECTION_ITEMS
        for key, item in entries[:MAX_MODEL_COLLECTION_ITEMS]:
            projected, item_truncated = _bounded_model_value(item, depth=depth + 1)
            projected_items[key] = projected
            truncated = truncated or item_truncated
        return projected_items, truncated
    return None, True


def _normalized_session(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    last_error = value["lastError"]
    projected_error: Any = last_error
    error_truncated = False
    error_units = 0
    if isinstance(last_error, str):
        projected_error, error_truncated, error_units = _truncate_utf16(
            last_error, MAX_MODEL_TEXT_UTF16_UNITS
        )
    return {
        "thread_id": value["threadId"],
        "status": value["status"],
        "provider_name": value["providerName"],
        "provider_instance_id": value.get("providerInstanceId"),
        "runtime_mode": value["runtimeMode"],
        "active_turn_id": value["activeTurnId"],
        "last_error": projected_error,
        "last_error_truncated": error_truncated,
        "last_error_original_utf16_units": error_units,
        "updated_at": value["updatedAt"],
    }


def _normalized_latest_turn(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result = {
        "turn_id": value["turnId"],
        "state": value["state"],
        "requested_at": value["requestedAt"],
        "started_at": value["startedAt"],
        "completed_at": value["completedAt"],
        "assistant_message_id": value["assistantMessageId"],
    }
    if "sourceProposedPlan" in value:
        source = value["sourceProposedPlan"]
        result["source_proposed_plan"] = {
            "thread_id": source["threadId"],
            "plan_id": source["planId"],
        }
    return result


def _normalized_message(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    text, truncated, original_units = _truncate_utf16(
        value["text"], MAX_MODEL_TEXT_UTF16_UNITS
    )
    return {
        "id": value["id"],
        "text": text,
        "text_truncated": truncated,
        "text_original_utf16_units": original_units,
        "turn_id": value["turnId"],
        "streaming": value["streaming"],
        "created_at": value["createdAt"],
        "updated_at": value["updatedAt"],
    }


def _latest_message(thread: dict[str, Any], role: str) -> dict[str, Any] | None:
    matches = [item for item in thread.get("messages", []) if item.get("role") == role]
    if not matches:
        return None
    latest = max(matches, key=lambda item: (item["updatedAt"], item["createdAt"], item["id"]))
    return _normalized_message(latest)


def _activity_order(activity: dict[str, Any]) -> tuple[int, str, str]:
    sequence = activity.get("sequence")
    return (sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else -1, activity["createdAt"], activity["id"])


def _activity_is_after(candidate: dict[str, Any], reference: dict[str, Any]) -> bool:
    candidate_sequence = candidate.get("sequence")
    reference_sequence = reference.get("sequence")
    if (
        isinstance(candidate_sequence, int)
        and not isinstance(candidate_sequence, bool)
        and isinstance(reference_sequence, int)
        and not isinstance(reference_sequence, bool)
    ):
        return candidate_sequence > reference_sequence
    return _timestamp_value(candidate["createdAt"]) > _timestamp_value(
        reference["createdAt"]
    )


def _failure_is_stale(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    text = " ".join(str(value) for value in payload.values() if isinstance(value, str)).casefold()
    return "stale pending" in text or "unknown pending" in text


def _normalized_question(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "id": value.get("id"),
        "header": value.get("header"),
        "question": value.get("question"),
        "options": copy.deepcopy(value.get("options", [])),
        "multi_select": value.get("multiSelect", False),
    }


def _pending_requests(thread: dict[str, Any]) -> list[dict[str, Any]]:
    request_states: dict[
        tuple[str, str, str | None],
        dict[str, tuple[dict[str, Any], dict[str, Any]]],
    ] = {}
    terminal_states: dict[
        tuple[str, str, str | None], dict[str, dict[str, Any]]
    ] = {}
    unscoped_failure_states: dict[
        tuple[str, str], dict[str, dict[str, Any]]
    ] = {}

    def remember_request(
        key: tuple[str, str, str | None],
        source: dict[str, Any],
        request: dict[str, Any],
    ) -> None:
        activities = request_states.setdefault(key, {})
        current = activities.get(source["id"])
        if current is None or _activity_is_after(source, current[0]):
            activities[source["id"]] = (source, request)

    def remember_terminal(
        key: tuple[str, str, str | None], source: dict[str, Any]
    ) -> None:
        if key[2] is None:
            if source["kind"].startswith("provider."):
                activities = unscoped_failure_states.setdefault(key[:2], {})
                current = activities.get(source["id"])
                if current is None or _activity_is_after(source, current):
                    activities[source["id"]] = source
            return
        activities = terminal_states.setdefault(key, {})
        current = activities.get(source["id"])
        if current is None or _activity_is_after(source, current):
            activities[source["id"]] = source

    for item in thread.get("activities", []):
        payload = item.get("payload")
        request_id = payload.get("requestId") if isinstance(payload, dict) else None
        if not isinstance(request_id, str) or not request_id:
            continue
        turn_id = item.get("turnId")
        exact_turn_id = turn_id if isinstance(turn_id, str) and turn_id else None
        if item["kind"] == "approval.requested":
            key = ("approval", request_id, exact_turn_id)
            request = {
                "kind": "approval",
                "request_id": request_id,
                "request_kind": payload.get("requestKind"),
                "request_type": payload.get("requestType"),
                "detail": payload.get("detail"),
                "turn_id": item.get("turnId"),
                "created_at": item["createdAt"],
            }
            remember_request(key, item, request)
        elif item["kind"] == "approval.resolved" or (
            item["kind"] == "provider.approval.respond.failed"
            and _failure_is_stale(payload)
        ):
            key = ("approval", request_id, exact_turn_id)
            remember_terminal(key, item)
        elif item["kind"] == "user-input.requested":
            key = ("user_input", request_id, exact_turn_id)
            raw_questions = payload.get("questions", [])
            questions = raw_questions if isinstance(raw_questions, list) else []
            request = {
                "kind": "user_input",
                "request_id": request_id,
                "questions": [_normalized_question(question) for question in questions],
                "turn_id": item.get("turnId"),
                "created_at": item["createdAt"],
            }
            remember_request(key, item, request)
        elif item["kind"] == "user-input.resolved" or (
            item["kind"] == "provider.user-input.respond.failed"
            and _failure_is_stale(payload)
        ):
            key = ("user_input", request_id, exact_turn_id)
            remember_terminal(key, item)
    request_counts: dict[tuple[str, str], int] = {}
    for key, activities in request_states.items():
        request_counts[key[:2]] = request_counts.get(key[:2], 0) + len(activities)

    pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for key, activities in request_states.items():
        terminals = list(terminal_states.get(key, {}).values())
        if request_counts[key[:2]] == 1:
            terminals.extend(unscoped_failure_states.get(key[:2], {}).values())
        for source, request in activities.values():
            if not terminals or all(
                _activity_is_after(source, terminal) for terminal in terminals
            ):
                pending.append((source, request))
    pending.sort(
        key=lambda value: (
            _timestamp_value(value[0]["createdAt"]),
            value[0]["id"],
        )
    )
    return [request for _source, request in pending]


def _actionable_plan(thread: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        item
        for item in thread.get("proposedPlans", [])
        if item["implementedAt"] is None and item["implementationThreadId"] is None
    ]
    if not candidates:
        return None
    value = max(candidates, key=lambda item: (item["updatedAt"], item["id"]))
    markdown, truncated, original_units = _truncate_utf16(
        value["planMarkdown"], MAX_MODEL_PLAN_UTF16_UNITS
    )
    return {
        "id": value["id"],
        "turn_id": value["turnId"],
        "plan_markdown": markdown,
        "plan_markdown_truncated": truncated,
        "plan_markdown_original_utf16_units": original_units,
        "implemented_at": value["implementedAt"],
        "implementation_thread_id": value["implementationThreadId"],
        "created_at": value["createdAt"],
        "updated_at": value["updatedAt"],
    }


def _plan_progress(thread: dict[str, Any]) -> tuple[Any, bool]:
    updates = [item for item in thread.get("activities", []) if item["kind"] == "turn.plan.updated"]
    if not updates:
        return None, False
    projected, truncated = _bounded_model_value(
        max(updates, key=_activity_order)["payload"]
    )
    return projected, truncated


def _last_error(thread: dict[str, Any]) -> str | None:
    current_session = thread.get("session")
    if isinstance(current_session, dict) and isinstance(current_session.get("lastError"), str):
        return current_session["lastError"]
    errors = [item for item in thread.get("activities", []) if item["kind"] == "runtime.error"]
    if not errors:
        return None
    payload = max(errors, key=_activity_order).get("payload")
    if not isinstance(payload, dict):
        return None
    for field in ("message", "detail", "error"):
        if isinstance(payload.get(field), str):
            return payload[field]
    return None


def _projected_last_error(thread: dict[str, Any]) -> tuple[str | None, bool, int]:
    value = _last_error(thread)
    if value is None:
        return None, False, 0
    return _truncate_utf16(value, MAX_MODEL_TEXT_UTF16_UNITS)


def _thread_state(
    thread: dict[str, Any], *, pending: list[dict[str, Any]] | None = None
) -> tuple[str, str]:
    if pending:
        return "blocked", "action_required"
    if thread.get("hasPendingApprovals") or thread.get("hasPendingUserInput"):
        return "blocked", "action_required"
    current_session = thread.get("session")
    status = current_session.get("status") if isinstance(current_session, dict) else None
    latest = thread.get("latestTurn")
    turn_state = latest.get("state") if isinstance(latest, dict) else None
    if status in {"starting", "running"}:
        return status, "working"
    if status == "stopped":
        return "stopped", "stopped"
    if status == "error" or turn_state == "error":
        return "error", "error"
    if turn_state == "interrupted":
        return "interrupted", "settled"
    if status == "ready":
        return "ready", "settled"
    return "idle", "settled"


def _compact_thread(thread: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    lifecycle, liveness = _thread_state(thread)
    latest = thread.get("latestTurn")
    settled_at = latest.get("completedAt") if isinstance(latest, dict) else None
    last_error, error_truncated, error_units = _projected_last_error(thread)
    return {
        "id": thread["id"],
        "project": _normalized_project(project),
        "title": thread["title"],
        "model_selection": copy.deepcopy(thread["modelSelection"]),
        "runtime_mode": thread["runtimeMode"],
        "interaction_mode": thread["interactionMode"],
        "branch": thread["branch"],
        "worktree_path": thread["worktreePath"],
        "lifecycle": lifecycle,
        "liveness": liveness,
        "latest_turn": _normalized_latest_turn(latest),
        "session": _normalized_session(thread.get("session")),
        "last_error": last_error,
        "last_error_truncated": error_truncated,
        "last_error_original_utf16_units": error_units,
        "latest_user_message_at": thread.get("latestUserMessageAt"),
        "has_pending_approvals": bool(thread.get("hasPendingApprovals", False)),
        "has_pending_user_input": bool(thread.get("hasPendingUserInput", False)),
        "has_actionable_plan": bool(thread.get("hasActionableProposedPlan", False)),
        "created_at": thread["createdAt"],
        "updated_at": thread["updatedAt"],
        "settled_at": settled_at,
    }


def _normalized_page(detail: dict[str, Any]) -> tuple[dict[str, Any], int]:
    raw = detail.get("page")
    if not isinstance(raw, dict):
        sequence = detail["snapshotSequence"]
        return {
            "before_cursor": None,
            "has_more": False,
            "snapshot_sequence": sequence,
            "thread_sequence": sequence,
        }, sequence
    thread_sequence = raw.get("threadSequence", detail["snapshotSequence"])
    return {
        "before_cursor": raw["beforeCursor"],
        "has_more": raw["hasMore"],
        "snapshot_sequence": raw["snapshotSequence"],
        "thread_sequence": thread_sequence,
    }, thread_sequence


def _material_projection(detail: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    thread = detail["thread"]
    pending = _pending_requests(thread)
    lifecycle, liveness = _thread_state(thread, pending=pending)
    latest_user = _latest_message(thread, "user")
    latest_assistant = _latest_message(thread, "assistant")
    latest = thread.get("latestTurn")
    page, thread_sequence = _normalized_page(detail)
    projected_pending, pending_truncated = _bounded_model_value(pending)
    plan_progress, plan_progress_truncated = _plan_progress(thread)
    last_error, error_truncated, error_units = _projected_last_error(thread)
    return {
        "view": "material",
        "snapshot_sequence": detail["snapshotSequence"],
        "thread_sequence": thread_sequence,
        "page": page,
        "thread": {
            "id": thread["id"],
            "project": _normalized_project(project),
            "title": thread["title"],
            "model_selection": copy.deepcopy(thread["modelSelection"]),
            "runtime_mode": thread["runtimeMode"],
            "interaction_mode": thread["interactionMode"],
            "branch": thread["branch"],
            "worktree_path": thread["worktreePath"],
            "lifecycle": lifecycle,
            "liveness": liveness,
            "latest_turn": _normalized_latest_turn(latest),
            "session": _normalized_session(thread.get("session")),
            "pending_requests": projected_pending,
            "pending_requests_truncated": pending_truncated,
            "last_error": last_error,
            "last_error_truncated": error_truncated,
            "last_error_original_utf16_units": error_units,
            "latest_user_update": latest_user,
            "latest_assistant_update": latest_assistant,
            "actionable_plan": _actionable_plan(thread),
            "plan_progress": plan_progress,
            "plan_progress_truncated": plan_progress_truncated,
            "created_at": thread["createdAt"],
            "updated_at": thread["updatedAt"],
            "settled_at": latest.get("completedAt") if isinstance(latest, dict) else None,
            "latest_user_message_at": latest_user["created_at"] if latest_user else None,
        },
    }


def _project_for_thread(shell: dict[str, Any], thread: dict[str, Any]) -> dict[str, Any]:
    matches = [project for project in shell["projects"] if project["id"] == thread["projectId"]]
    if len(matches) != 1:
        raise ConflictError("The thread project does not exist uniquely in the current shell.")
    return matches[0]


def t3_threads(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(
        raw_args,
        allowed={
            "view",
            "project",
            "workspace",
            "title_query",
            "lifecycle",
            "updated_within_minutes",
            "limit",
            "require_one",
        },
        required=set(),
    )
    view = _enum(args.get("view", "compact"), "view", THREAD_VIEWS)
    project_filter = (
        normalize_string(args["project"], "project", max_chars=MAX_IDENTIFIER_CHARS)
        if "project" in args
        else None
    )
    workspace_filter = (
        normalize_string(args["workspace"], "workspace", max_chars=MAX_WORKTREE_PATH_CHARS)
        if "workspace" in args
        else None
    )
    title_query = (
        normalize_string(args["title_query"], "title_query", max_chars=MAX_TITLE_CHARS)
        if "title_query" in args
        else None
    )
    lifecycle = (
        _enum(args["lifecycle"], "lifecycle", THREAD_LIFECYCLES)
        if "lifecycle" in args
        else None
    )
    updated_within = (
        _bounded_integer(
            args["updated_within_minutes"],
            "updated_within_minutes",
            minimum=1,
            maximum=MAX_UPDATED_WITHIN_MINUTES,
        )
        if "updated_within_minutes" in args
        else None
    )
    limit = _bounded_integer(
        args.get("limit", 20), "limit", minimum=1, maximum=MAX_COMPACT_LIMIT
    )
    require_one = _boolean(args.get("require_one", False), "require_one")
    normalized = {
        "view": view,
        "project": project_filter,
        "workspace": workspace_filter,
        "title_query": title_query,
        "lifecycle": lifecycle,
        "updated_within_minutes": updated_within,
        "limit": limit,
        "require_one": require_one,
    }
    if view == "raw" and any(
        key in args
        for key in {
            "project",
            "workspace",
            "title_query",
            "lifecycle",
            "updated_within_minutes",
            "limit",
            "require_one",
        }
    ):
        _invalid("Compact filters require view compact.")

    def perform(transport: T3Client) -> dict[str, Any]:
        shell = transport.get_shell()
        if view == "raw":
            return {"shell": shell}
        projects = {item["id"]: item for item in shell["projects"]}

        def project_matches(project: dict[str, Any]) -> bool:
            if project_filter is not None:
                needle = project_filter.casefold()
                identities = {
                    project["id"].casefold(),
                    project["title"].casefold(),
                    os.path.basename(project["workspaceRoot"].rstrip("/")).casefold(),
                }
                if needle not in identities:
                    return False
            if workspace_filter is not None:
                needle = workspace_filter.casefold()
                identities = {
                    project["workspaceRoot"].casefold(),
                    os.path.basename(project["workspaceRoot"].rstrip("/")).casefold(),
                }
                if needle not in identities:
                    return False
            return True

        matching_projects = [
            project for project in shell["projects"] if project_matches(project)
        ]
        matching_projects.sort(
            key=lambda project: (project["title"].casefold(), project["id"])
        )
        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        cutoff = (
            datetime.now(timezone.utc) - timedelta(minutes=updated_within)
            if updated_within is not None
            else None
        )
        for thread in shell["threads"]:
            project = projects.get(thread["projectId"])
            if project is None:
                continue
            if not project_matches(project):
                continue
            if title_query is not None and title_query.casefold() not in thread["title"].casefold():
                continue
            state, _liveness = _thread_state(thread)
            if lifecycle is not None and state != lifecycle:
                continue
            if cutoff is not None and _timestamp_value(thread["updatedAt"]) < cutoff:
                continue
            matches.append((thread, project))
        matched_count = len(matches)
        if require_one and matched_count != 1:
            raise ConflictError("The compact thread query did not resolve exactly one thread.")
        matches.sort(key=lambda pair: pair[0]["id"])
        matches.sort(key=lambda pair: pair[0]["updatedAt"], reverse=True)
        return {
            "view": "compact",
            "snapshot_sequence": shell["snapshotSequence"],
            "updated_at": shell["updatedAt"],
            "project_count": len(matching_projects),
            "projects_truncated": len(matching_projects) > limit,
            "projects": [
                _normalized_project(project) for project in matching_projects[:limit]
            ],
            "matched_count": matched_count,
            "threads": [_compact_thread(thread, project) for thread, project in matches[:limit]],
        }

    return _execute_operation(ctx, normalized, perform)


def t3_thread_read(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(
        raw_args,
        allowed={"thread_id", "view", "turn_limit", "before_cursor"},
        required={"thread_id"},
    )
    thread_id = normalize_string(args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS)
    view = _enum(args.get("view", "material"), "view", READ_VIEWS)
    turn_limit = validate_turn_limit(args.get("turn_limit", 20))
    before_cursor = None
    if "before_cursor" in args:
        before_cursor = normalize_string(
            args["before_cursor"], "before_cursor", max_chars=MAX_CURSOR_CHARS
        )
        if "turn_limit" not in args:
            _invalid("before_cursor requires an explicit turn_limit.")
    normalized = {
        "thread_id": thread_id,
        "view": view,
        "turn_limit": turn_limit,
        "before_cursor": before_cursor,
    }

    def perform(transport: T3Client) -> dict[str, Any]:
        if view == "raw":
            return {
                "detail": transport.get_thread(
                    thread_id,
                    turn_limit=turn_limit,
                    before_cursor=before_cursor,
                )
            }
        shell = transport.get_shell()
        detail = transport.get_thread(
            thread_id,
            turn_limit=turn_limit,
            before_cursor=before_cursor,
        )
        return _material_projection(detail, _project_for_thread(shell, detail["thread"]))

    return _execute_operation(ctx, normalized, perform)


def t3_thread_create(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(
        raw_args,
        allowed={
            "project_id",
            "title",
            "instance_id",
            "model",
            "model_options",
            "runtime_mode",
            "interaction_mode",
            "initial_message",
            "branch",
            "worktree_path",
        },
        required={"project_id", "title"},
    )
    project_id = normalize_string(args["project_id"], "project_id", max_chars=MAX_IDENTIFIER_CHARS)
    title = normalize_string(args["title"], "title", max_chars=MAX_TITLE_CHARS)
    has_instance = "instance_id" in args
    has_model = "model" in args
    if has_instance != has_model:
        _invalid("instance_id and model must be supplied together.")
    if "model_options" in args and not has_instance:
        _invalid("model_options requires an explicit instance_id and model pair.")
    explicit_selection: dict[str, Any] | None = None
    if has_instance:
        explicit_selection = {
            "instanceId": normalize_string(
                args["instance_id"], "instance_id", max_chars=MAX_IDENTIFIER_CHARS
            ),
            "model": normalize_string(args["model"], "model", max_chars=MAX_IDENTIFIER_CHARS),
        }
    explicit_options = (
        _normalize_model_options(args["model_options"])
        if "model_options" in args
        else None
    )
    explicit_runtime_mode = (
        _enum(args["runtime_mode"], "runtime_mode", RUNTIME_MODES)
        if "runtime_mode" in args
        else None
    )
    configured_runtime_mode = _configured_runtime_mode(ctx)
    runtime_mode = explicit_runtime_mode or configured_runtime_mode
    interaction_mode = _enum(
        args.get("interaction_mode", "default"), "interaction_mode", INTERACTION_MODES
    )
    initial_message = (
        normalize_message(args["initial_message"])
        if "initial_message" in args
        else None
    )
    branch = (
        normalize_string(args["branch"], "branch", max_chars=MAX_BRANCH_CHARS)
        if "branch" in args
        else None
    )
    worktree_path = (
        normalize_string(
            args["worktree_path"],
            "worktree_path",
            max_chars=MAX_WORKTREE_PATH_CHARS,
        )
        if "worktree_path" in args
        else None
    )

    normalized = {
        "project_id": project_id,
        "title": title,
        "instance_id": (
            explicit_selection["instanceId"] if explicit_selection is not None else None
        ),
        "model": explicit_selection["model"] if explicit_selection is not None else None,
        "model_options": explicit_options,
        "runtime_mode": runtime_mode,
        "interaction_mode": interaction_mode,
        "initial_message": initial_message,
        "branch": branch,
        "worktree_path": worktree_path,
    }

    def perform(transport: T3Client) -> dict[str, Any]:
        shell = transport.get_shell()
        project = next((item for item in shell["projects"] if item["id"] == project_id), None)
        if project is None:
            raise ConflictError("The requested project does not exist in the current T3 shell.")
        default_selection = project.get("defaultModelSelection")
        if explicit_selection is None:
            if default_selection is None:
                raise ConflictError(
                    "The project has no default model selection; supply instance_id and model."
                )
            model_selection = copy.deepcopy(default_selection)
        else:
            same_as_default = (
                isinstance(default_selection, dict)
                and explicit_selection["instanceId"] == default_selection["instanceId"]
                and explicit_selection["model"] == default_selection["model"]
            )
            model_selection = (
                copy.deepcopy(default_selection) if same_as_default else copy.deepcopy(explicit_selection)
            )
            if explicit_options is not None:
                model_selection["options"] = explicit_options

        command_id = transport.new_uuid4()
        thread_id = transport.new_uuid4()
        command = {
            "type": "thread.create",
            "commandId": command_id,
            "threadId": thread_id,
            "projectId": project_id,
            "title": title,
            "modelSelection": model_selection,
            "runtimeMode": runtime_mode,
            "interactionMode": interaction_mode,
            "branch": branch,
            "worktreePath": worktree_path,
            "createdAt": _now_rfc3339(),
        }

        def created(detail: dict[str, Any]) -> bool:
            thread = detail["thread"]
            return (
                thread["id"] == thread_id
                and thread["projectId"] == project_id
                and thread["title"] == title
                and thread["modelSelection"] == model_selection
                and thread["runtimeMode"] == runtime_mode
                and thread["interactionMode"] == interaction_mode
                and thread["branch"] == branch
                and thread["worktreePath"] == worktree_path
            )

        warning = FULL_ACCESS_WARNING if runtime_mode == "full-access" else None
        try:
            create_result = transport.mutate(thread_id, command, created)
        except T3ClientError as exc:
            if warning is not None:
                _annotate_error(exc, warning=warning)
            raise
        if initial_message is None:
            payload = {"action": "thread_created", **create_result}
            if warning is not None:
                payload["warning"] = warning
            return payload
        if create_result["verification"] == "accepted_pending_projection":
            payload = {
                "action": "thread_create_accepted_pending_projection",
                **create_result,
            }
            if warning is not None:
                payload["warning"] = warning
            return payload

        try:
            created_thread = create_result["detail"]["thread"]
            turn_command, message_id = _build_turn_command(
                transport, created_thread, initial_message
            )

            def initial_turn_observed(detail: dict[str, Any]) -> bool:
                return _turn_observed(detail, turn_command, message_id)

            turn_result = transport.mutate(
                thread_id, turn_command, initial_turn_observed
            )
        except T3ClientError as exc:
            details = {
                "workflow_phase": "initial_turn",
                "verified_create_command_id": create_result["command_id"],
                "created_thread_id": thread_id,
                "race_semantics": MODE_RACE_SEMANTICS,
            }
            if warning is not None:
                details["warning"] = warning
            raise _annotate_error(exc, **details)
        if turn_result["verification"] == "accepted_pending_projection":
            payload = {
                "action": "initial_turn_accepted_pending_projection",
                **turn_result,
                "create_command_id": create_result["command_id"],
                "message_id": message_id,
                "race_semantics": MODE_RACE_SEMANTICS,
            }
            if warning is not None:
                payload["warning"] = warning
            return payload
        observed = turn_result["detail"]["thread"]
        payload = {
            "action": "thread_created_with_initial_turn",
            **turn_result,
            "create_command_id": create_result["command_id"],
            "turn_command_id": turn_result["command_id"],
            "message_id": message_id,
            "provider_session": observed["session"],
            "latest_turn": observed["latestTurn"],
            "race_semantics": MODE_RACE_SEMANTICS,
        }
        if warning is not None:
            payload["warning"] = warning
        return payload

    return _execute_operation(ctx, normalized, perform)


def t3_thread_send(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(
        raw_args,
        allowed={"thread_id", "message", "busy_policy"},
        required={"thread_id", "message"},
    )
    thread_id = normalize_string(args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS)
    text = normalize_message(args["message"])
    busy_policy = _enum(args.get("busy_policy", "reject"), "busy_policy", BUSY_POLICIES)
    normalized = {"thread_id": thread_id, "message": text, "busy_policy": busy_policy}

    def perform(transport: T3Client) -> dict[str, Any]:
        before = transport.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT)
        stored = before["thread"]
        _ensure_mutable_thread(stored)
        current_session = stored.get("session")
        busy = (
            isinstance(current_session, dict)
            and current_session.get("status") in {"starting", "running"}
        ) or _active_turn_id(stored) is not None
        if busy_policy == "reject":
            if busy:
                raise ConflictError(
                    "The target thread is busy; busy_policy reject performed no dispatch. "
                    "Select busy_policy queue explicitly to accept start-or-queue semantics."
                )
            raise ConflictError(
                "The target appears idle, but T3 has no atomic idle guard; busy_policy "
                "reject never dispatches. Select busy_policy queue explicitly to accept "
                "start-or-queue semantics."
            )
        warning = FULL_ACCESS_WARNING if stored["runtimeMode"] == "full-access" else None
        try:
            command, message_id = _build_turn_command(transport, stored, text)

            def message_observed(detail: dict[str, Any]) -> bool:
                thread = detail["thread"]
                return any(
                    item["id"] == message_id
                    and item["role"] == "user"
                    and item["text"] == text
                    and (
                        item["turnId"] is None
                        or (
                            isinstance(thread.get("latestTurn"), dict)
                            and item["turnId"] == thread["latestTurn"]["turnId"]
                        )
                    )
                    for item in thread["messages"]
                )

            result = transport.mutate(thread_id, command, message_observed)
        except T3ClientError as exc:
            details: dict[str, Any] = {
                "race_semantics": MODE_RACE_SEMANTICS,
                "queue_semantics": QUEUE_SEMANTICS,
            }
            if warning is not None:
                details["warning"] = warning
            raise _annotate_error(exc, **details)
        if result["verification"] == "accepted_pending_projection":
            payload = {
                "action": "new_turn_accepted_pending_projection",
                "command_state": "accepted_pending_projection",
                "message_id": message_id,
                **result,
                "race_semantics": MODE_RACE_SEMANTICS,
                "queue_semantics": QUEUE_SEMANTICS,
            }
            if warning is not None:
                payload["warning"] = warning
            return payload
        detail = result["detail"]
        if not isinstance(detail, dict):
            raise T3ClientError()
        observed = detail["thread"]
        matches = [item for item in observed["messages"] if item["id"] == message_id]
        if len(matches) != 1:
            raise T3ClientError()
        persisted = matches[0]
        pending = _pending_requests(observed)
        latest = observed.get("latestTurn")
        if persisted["turnId"] is None:
            command_state = "queued"
        elif pending:
            command_state = "blocked"
        elif isinstance(latest, dict) and latest["turnId"] == persisted["turnId"]:
            command_state = {
                "running": "started",
                "completed": "completed",
                "error": "error",
                "interrupted": "completed",
            }[latest["state"]]
        else:
            raise T3ClientError()
        payload = {
            "action": "new_turn_same_thread",
            "message_id": message_id,
            **result,
            "command_state": command_state,
            "persisted_message": _normalized_message(persisted),
            "provider_session": observed["session"],
            "latest_turn": observed["latestTurn"],
            "race_semantics": MODE_RACE_SEMANTICS,
            "queue_semantics": QUEUE_SEMANTICS,
        }
        if warning is not None:
            payload["warning"] = warning
        return payload

    return _execute_operation(ctx, normalized, perform)


def t3_thread_wait(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(
        raw_args,
        allowed={"thread_id", "after_thread_sequence", "until", "timeout_seconds"},
        required={"thread_id"},
    )
    thread_id = normalize_string(args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS)
    after_sequence = (
        _bounded_integer(
            args["after_thread_sequence"],
            "after_thread_sequence",
            minimum=0,
            maximum=2**63 - 1,
        )
        if "after_thread_sequence" in args
        else None
    )
    until = _enum(args.get("until", "terminal"), "until", WAIT_UNTIL)
    timeout_seconds = _bounded_integer(
        args.get("timeout_seconds", 0),
        "timeout_seconds",
        minimum=0,
        maximum=MAX_WAIT_SECONDS,
    )
    normalized = {
        "thread_id": thread_id,
        "after_thread_sequence": after_sequence,
        "until": until,
        "timeout_seconds": timeout_seconds,
    }

    def perform(transport: T3Client) -> dict[str, Any]:
        started = time.monotonic()
        deadline = started + max(float(timeout_seconds), 0.25)
        shell = transport.get_shell(deadline=deadline)
        while True:
            detail = transport.get_thread(
                thread_id,
                turn_limit=MAX_TURN_LIMIT,
                deadline=deadline,
            )
            project = _project_for_thread(shell, detail["thread"])
            material = _material_projection(detail, project)
            projected = material["thread"]
            observed_sequence = material["thread_sequence"]
            progressed = after_sequence is None or observed_sequence > after_sequence
            liveness = projected["liveness"]
            lifecycle = projected["lifecycle"]
            if liveness == "action_required":
                outcome = "action_required"
            elif until == "change" and progressed:
                outcome = "progressed"
            elif until == "running" and progressed and lifecycle == "running":
                outcome = "progressed"
            elif until == "blocked" and progressed and lifecycle == "blocked":
                outcome = "action_required"
            elif until == "error" and progressed and lifecycle == "error":
                outcome = "settled"
            elif (
                until == "terminal"
                and progressed
                and liveness in {"settled", "stopped", "error"}
            ):
                outcome = "settled"
            elif timeout_seconds == 0 or time.monotonic() >= deadline:
                outcome = "timeout"
            else:
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
                continue
            material_delta = {
                "pending_requests": projected["pending_requests"],
                "pending_requests_truncated": projected[
                    "pending_requests_truncated"
                ],
                "last_error": projected["last_error"],
                "last_error_truncated": projected["last_error_truncated"],
                "last_error_original_utf16_units": projected[
                    "last_error_original_utf16_units"
                ],
                "latest_user_update": projected["latest_user_update"],
                "actionable_plan": projected["actionable_plan"],
                "plan_progress": projected["plan_progress"],
                "plan_progress_truncated": projected[
                    "plan_progress_truncated"
                ],
                "updated_at": projected["updated_at"],
                "settled_at": projected["settled_at"],
            }
            return {
                "action": "thread_wait_observed",
                "thread_id": thread_id,
                "wait_outcome": outcome,
                "snapshot_sequence": material["snapshot_sequence"],
                "thread_sequence": observed_sequence,
                "after_thread_sequence": after_sequence,
                "lifecycle": lifecycle,
                "liveness": liveness,
                "progress": progressed,
                "latest_assistant_update": projected["latest_assistant_update"],
                "material_delta": material_delta,
            }

    return _execute_operation(ctx, normalized, perform)


def _normalize_answers(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value or len(value) > 64:
        _invalid("answers must be a non-empty object with at most 64 entries.")
    normalized: dict[str, Any] = {}
    for key, answer in value.items():
        normalized_key = normalize_string(key, "answers key", max_chars=MAX_IDENTIFIER_CHARS)
        if normalized_key in normalized:
            _invalid("answers keys must be unique.")
        if isinstance(answer, str):
            if len(answer) > 120_000:
                _invalid("answers string values exceed their character limit.")
            try:
                answer.encode("utf-8")
            except UnicodeEncodeError:
                _invalid("answers contain invalid Unicode.")
        elif isinstance(answer, list):
            if len(answer) > 64 or any(not isinstance(item, str) for item in answer):
                _invalid("answers arrays must contain at most 64 strings.")
            if any(len(item) > 120_000 for item in answer):
                _invalid("answers string values exceed their character limit.")
            try:
                for item in answer:
                    item.encode("utf-8")
            except UnicodeEncodeError:
                _invalid("answers contain invalid Unicode.")
        elif isinstance(answer, (int, float)) and not isinstance(answer, bool):
            if (
                isinstance(answer, float)
                and not math.isfinite(answer)
                or answer < -MAX_SAFE_JSON_INTEGER
                or answer > MAX_SAFE_JSON_INTEGER
            ):
                _invalid(
                    "answers numbers must be finite and within the exact JSON integer range."
                )
        elif answer is not None and not isinstance(answer, (bool, int, float)):
            _invalid("answers contain an unsupported value.")
        normalized[normalized_key] = copy.deepcopy(answer)
    return normalized


def _json_values_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    left_number = isinstance(left, (int, float))
    right_number = isinstance(right, (int, float))
    if left_number or right_number:
        return left_number and right_number and left == right
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    if isinstance(left, list) or isinstance(right, list):
        return (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right)
            and all(_json_values_equal(a, b) for a, b in zip(left, right))
        )
    if isinstance(left, dict) or isinstance(right, dict):
        return (
            isinstance(left, dict)
            and isinstance(right, dict)
            and left.keys() == right.keys()
            and all(_json_values_equal(left[key], right[key]) for key in left)
        )
    return False


def _canonical_user_input_answers(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        key: answer[0]
        if isinstance(answer, list)
        and len(answer) == 1
        and isinstance(answer[0], str)
        else answer
        for key, answer in value.items()
    }


def t3_thread_respond(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(
        raw_args,
        allowed={"thread_id", "request_id", "turn_id", "decision", "answers"},
        required={"thread_id", "request_id", "turn_id"},
    )
    has_decision = "decision" in args
    has_answers = "answers" in args
    if has_decision == has_answers:
        _invalid("Supply exactly one of decision or answers.")
    thread_id = normalize_string(args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS)
    request_id = normalize_string(args["request_id"], "request_id", max_chars=MAX_IDENTIFIER_CHARS)
    expected_turn_id = normalize_string(
        args["turn_id"], "turn_id", max_chars=MAX_IDENTIFIER_CHARS
    )
    decision = (
        _enum(args["decision"], "decision", APPROVAL_DECISIONS) if has_decision else None
    )
    answers = _normalize_answers(args["answers"]) if has_answers else None
    normalized = {
        "thread_id": thread_id,
        "request_id": request_id,
        "turn_id": expected_turn_id,
        "decision": decision,
        "answers": answers,
    }

    def perform(transport: T3Client) -> dict[str, Any]:
        def exact_pending(
            stored: dict[str, Any],
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            _ensure_mutable_thread(stored)
            if _active_turn_id(stored) != expected_turn_id:
                raise ConflictError(
                    "The expected request turn is not the thread's current active turn."
                )
            request_matches = [
                item
                for item in _pending_requests(stored)
                if item["request_id"] == request_id
            ]
            if len(request_matches) != 1:
                raise ConflictError(
                    "The request is stale or does not resolve uniquely on the thread."
                )
            pending = request_matches[0]
            if pending.get("turn_id") != expected_turn_id:
                raise ConflictError(
                    "The request does not belong to the expected current turn."
                )
            if (pending["kind"] == "approval") != has_decision:
                raise ConflictError(
                    "The response type does not match the exact pending request."
                )
            requested_kind = (
                "approval.requested"
                if pending["kind"] == "approval"
                else "user-input.requested"
            )
            request_activity: dict[str, Any] | None = None
            for item in stored.get("activities", []):
                payload = item.get("payload")
                if (
                    item.get("kind") != requested_kind
                    or not isinstance(payload, dict)
                    or payload.get("requestId") != request_id
                    or item.get("turnId") != expected_turn_id
                ):
                    continue
                if request_activity is None or _activity_is_after(
                    item, request_activity
                ):
                    request_activity = item
            if request_activity is None:
                raise ConflictError("The exact pending request activity is unavailable.")
            return pending, request_activity

        before = transport.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT)
        initial_pending, initial_activity = exact_pending(before["thread"])
        revalidated = transport.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT)
        stored = revalidated["thread"]
        pending, request_activity = exact_pending(stored)
        if (
            pending["kind"] != initial_pending["kind"]
            or request_activity["id"] != initial_activity["id"]
            or _activity_order(request_activity) != _activity_order(initial_activity)
        ):
            raise ConflictError(
                "The exact pending request changed during pre-dispatch revalidation."
            )
        request_turn_id = expected_turn_id
        command: dict[str, Any] = {
            "type": (
                "thread.approval.respond"
                if pending["kind"] == "approval"
                else "thread.user-input.respond"
            ),
            "commandId": transport.new_uuid4(),
            "threadId": thread_id,
            "requestId": request_id,
            "createdAt": _now_rfc3339(),
        }
        if decision is not None:
            command["decision"] = decision
        else:
            command["answers"] = answers

        terminal_kinds = frozenset(
            {
                "approval.resolved",
                "provider.approval.respond.failed",
                "user-input.resolved",
                "provider.user-input.respond.failed",
            }
        )
        baseline_terminal_ids = {
            item["id"]
            for item in stored.get("activities", [])
            if item.get("kind") in terminal_kinds
        }
        resolved_kind = (
            "approval.resolved" if pending["kind"] == "approval" else "user-input.resolved"
        )
        failed_kind = (
            "provider.approval.respond.failed"
            if pending["kind"] == "approval"
            else "provider.user-input.respond.failed"
        )
        response_field = "decision" if pending["kind"] == "approval" else "answers"
        expected_response = decision if pending["kind"] == "approval" else answers
        if pending["kind"] == "user_input":
            expected_response = _canonical_user_input_answers(expected_response)

        def new_terminals(detail: dict[str, Any]) -> list[dict[str, Any]]:
            terminals: list[dict[str, Any]] = []
            for item in detail["thread"].get("activities", []):
                payload = item.get("payload")
                if (
                    item.get("kind") not in terminal_kinds
                    or item.get("id") in baseline_terminal_ids
                    or not isinstance(payload, dict)
                    or payload.get("requestId") != request_id
                ):
                    continue
                item_time = _timestamp_value(item["createdAt"])
                command_time = _timestamp_value(command["createdAt"])
                if item.get("turnId") == request_turn_id:
                    if (
                        not _activity_is_after(item, request_activity)
                        or item_time <= command_time
                    ):
                        continue
                elif not (
                    item["kind"] == failed_kind
                    and item.get("turnId") is None
                    and item_time == command_time
                ):
                    continue
                terminals.append(item)
            return terminals

        def resolved(detail: dict[str, Any]) -> bool:
            return any(
                item["kind"] == resolved_kind
                and _json_values_equal(
                    _canonical_user_input_answers(
                        item["payload"].get(response_field)
                    )
                    if pending["kind"] == "user_input"
                    else item["payload"].get(response_field),
                    expected_response,
                )
                for item in new_terminals(detail)
            )

        def conflicting_or_failed(detail: dict[str, Any]) -> bool:
            return any(
                item["kind"] != resolved_kind
                or not _json_values_equal(
                    _canonical_user_input_answers(
                        item["payload"].get(response_field)
                    )
                    if pending["kind"] == "user_input"
                    else item["payload"].get(response_field),
                    expected_response,
                )
                for item in new_terminals(detail)
            )

        try:
            result = transport.mutate(
                thread_id,
                command,
                resolved,
                race_detector=conflicting_or_failed,
                require_accepted_sequence=True,
            )
        except T3ClientError as exc:
            raise _annotate_error(
                exc,
                expected_turn_id=expected_turn_id,
                response_scope="best_effort_current_provider_session",
                warning=RESPONSE_RACE_WARNING,
            )
        result.pop("detail", None)
        return {
            "action": (
                "pending_request_response_accepted"
                if result["verification"] == "accepted_pending_projection"
                else "pending_request_responded"
            ),
            "request_id": request_id,
            "turn_id": expected_turn_id,
            "request_kind": pending["kind"],
            "response_scope": "best_effort_current_provider_session",
            "warning": RESPONSE_RACE_WARNING,
            **result,
        }

    return _execute_operation(ctx, normalized, perform)


def t3_thread_set_mode(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(
        raw_args,
        allowed={"thread_id", "runtime_mode", "interaction_mode"},
        required={"thread_id"},
    )
    has_runtime = "runtime_mode" in args
    has_interaction = "interaction_mode" in args
    if has_runtime == has_interaction:
        _invalid("Supply exactly one of runtime_mode or interaction_mode.")
    thread_id = normalize_string(
        args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS
    )
    if has_runtime:
        field = "runtimeMode"
        public_field = "runtime_mode"
        mode = _enum(args["runtime_mode"], "runtime_mode", RUNTIME_MODES)
        command_type = "thread.runtime-mode.set"
    else:
        field = "interactionMode"
        public_field = "interaction_mode"
        mode = _enum(
            args["interaction_mode"], "interaction_mode", INTERACTION_MODES
        )
        command_type = "thread.interaction-mode.set"

    normalized = {"thread_id": thread_id, public_field: mode}

    def perform(transport: T3Client) -> dict[str, Any]:
        before = transport.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT)
        stored = before["thread"]
        _ensure_mutable_thread(stored)
        captured_turn_id = _active_turn_id(stored)

        common: dict[str, Any] = {
            "mode_field": public_field,
            "mode": mode,
        }
        warnings: list[str] = []
        if has_runtime and mode == "full-access":
            warnings.append(FULL_ACCESS_WARNING)
        if has_runtime and captured_turn_id is not None:
            common["active_turn_unchanged"] = captured_turn_id
            warnings.append(PENDING_APPROVAL_WARNING)
        if warnings:
            common["warning"] = " ".join(warnings)
            common["warnings"] = list(warnings)
        if stored[field] == mode:
            return {
                "action": "mode_already_set",
                "command_id": None,
                "thread_id": thread_id,
                "detail": before,
                **common,
            }

        command = {
            "type": command_type,
            "commandId": transport.new_uuid4(),
            "threadId": thread_id,
            field: mode,
            "createdAt": _now_rfc3339(),
        }

        def mode_observed(detail: dict[str, Any]) -> bool:
            return detail["thread"][field] == mode

        try:
            result = transport.mutate(thread_id, command, mode_observed)
        except T3ClientError as exc:
            error_details: dict[str, Any] = {}
            if has_runtime and captured_turn_id is not None:
                error_details["active_turn_unchanged"] = captured_turn_id
            if warnings:
                error_details["warning"] = " ".join(warnings)
                error_details["warnings"] = list(warnings)
            if error_details:
                _annotate_error(exc, **error_details)
            raise
        return {
            "action": "thread_mode_set",
            **result,
            **common,
        }

    return _execute_operation(ctx, normalized, perform)


def _require_plan_ready(
    detail: dict[str, Any],
    plan_id: str,
    *,
    expected_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    thread = detail["thread"]
    _ensure_mutable_thread(thread)
    current_session = thread["session"]
    if isinstance(current_session, dict) and current_session["status"] in {
        "starting",
        "running",
    }:
        raise ConflictError("The target thread has a starting or running provider session.")
    if _active_turn_id(thread) is not None:
        raise ConflictError("The target thread has an active or running turn.")
    matches = [item for item in thread["proposedPlans"] if item["id"] == plan_id]
    if len(matches) != 1:
        raise ConflictError("The requested proposed plan does not exist uniquely on the thread.")
    plan = matches[0]
    if plan["implementedAt"] is not None or plan["implementationThreadId"] is not None:
        raise ConflictError("The requested proposed plan is already implemented.")
    if expected_plan is not None and any(
        plan[field] != expected_plan[field]
        for field in ("id", "turnId", "planMarkdown", "createdAt", "updatedAt")
    ):
        raise ConflictError("The proposed plan changed during the mode transition.")
    return plan


def t3_thread_implement_plan(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(
        raw_args,
        allowed={"thread_id", "plan_id"},
        required={"thread_id", "plan_id"},
    )
    thread_id = normalize_string(
        args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS
    )
    plan_id = normalize_string(
        args["plan_id"], "plan_id", max_chars=MAX_IDENTIFIER_CHARS
    )
    normalized = {"thread_id": thread_id, "plan_id": plan_id}

    def perform(transport: T3Client) -> dict[str, Any]:
        before = transport.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT)
        plan = _require_plan_ready(before, plan_id)
        warning = (
            FULL_ACCESS_WARNING
            if before["thread"]["runtimeMode"] == "full-access"
            else None
        )
        implementation_message = normalize_message(
            PLAN_IMPLEMENT_PREFIX + plan["planMarkdown"].strip()
        )
        plan_identity = copy.deepcopy(plan)

        mode_command = {
            "type": "thread.interaction-mode.set",
            "commandId": transport.new_uuid4(),
            "threadId": thread_id,
            "interactionMode": "default",
            "createdAt": _now_rfc3339(),
        }

        def default_mode_observed(detail: dict[str, Any]) -> bool:
            return detail["thread"]["interactionMode"] == "default"

        try:
            mode_result = transport.mutate(
                thread_id,
                mode_command,
                default_mode_observed,
                require_accepted_sequence=True,
            )
        except T3ClientError as exc:
            details: dict[str, Any] = {
                "workflow_phase": "interaction_mode_transition",
                "race_semantics": PLAN_RACE_SEMANTICS,
            }
            if warning is not None:
                details["warning"] = warning
            raise _annotate_error(exc, **details)
        if mode_result["verification"] == "accepted_pending_projection":
            payload = {
                "action": "plan_mode_transition_accepted_pending_projection",
                **mode_result,
                "mode_command_id": mode_result["command_id"],
                "mode_dispatch_sequence": mode_result["dispatch_sequence"],
                "mode_dispatch_attempts": mode_result["dispatch_attempts"],
                "race_semantics": PLAN_RACE_SEMANTICS,
            }
            if warning is not None:
                payload["warning"] = warning
            return payload

        try:
            _require_plan_ready(
                mode_result["detail"], plan_id, expected_plan=plan_identity
            )
        except T3ClientError as exc:
            details = {
                "workflow_phase": "post_mode_revalidation",
                "completed_phase": "interaction_mode_set",
                "verified_mode_command_id": mode_result["command_id"],
                "race_semantics": PLAN_RACE_SEMANTICS,
            }
            if warning is not None:
                details["warning"] = warning
            raise _annotate_error(exc, **details)
        source_plan = {"threadId": thread_id, "planId": plan_id}
        try:
            turn_command, message_id = _build_turn_command(
                transport,
                mode_result["detail"]["thread"],
                implementation_message,
                source_plan=source_plan,
            )

            def implementation_observed(detail: dict[str, Any]) -> bool:
                return _turn_observed(
                    detail,
                    turn_command,
                    message_id,
                    source_plan=source_plan,
                )

            turn_result = transport.mutate(
                thread_id, turn_command, implementation_observed
            )
        except T3ClientError as exc:
            details = {
                "workflow_phase": "implementation_turn",
                "completed_phase": "interaction_mode_set",
                "verified_mode_command_id": mode_result["command_id"],
                "race_semantics": PLAN_RACE_SEMANTICS,
            }
            if warning is not None:
                details["warning"] = warning
            raise _annotate_error(exc, **details)
        if turn_result["verification"] == "accepted_pending_projection":
            payload = {
                "action": "plan_implementation_accepted_pending_projection",
                **turn_result,
                "mode_command_id": mode_result["command_id"],
                "message_id": message_id,
                "source_proposed_plan": source_plan,
                "race_semantics": PLAN_RACE_SEMANTICS,
            }
            if warning is not None:
                payload["warning"] = warning
            return payload
        observed = turn_result["detail"]["thread"]
        payload = {
            "action": "same_thread_plan_implementation_started",
            **turn_result,
            "mode_command_id": mode_result["command_id"],
            "mode_dispatch_sequence": mode_result["dispatch_sequence"],
            "mode_dispatch_attempts": mode_result["dispatch_attempts"],
            "mode_recovered_after_ambiguous_dispatch": mode_result[
                "recovered_after_ambiguous_dispatch"
            ],
            "turn_command_id": turn_result["command_id"],
            "message_id": message_id,
            "source_proposed_plan": source_plan,
            "provider_session": observed["session"],
            "latest_turn": observed["latestTurn"],
            "race_semantics": PLAN_RACE_SEMANTICS,
        }
        if warning is not None:
            payload["warning"] = warning
        return payload

    return _execute_operation(ctx, normalized, perform)


def t3_turn_interrupt(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(raw_args, allowed={"thread_id"}, required={"thread_id"})
    thread_id = normalize_string(args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS)
    normalized = {"thread_id": thread_id}

    def perform(transport: T3Client) -> dict[str, Any]:
        before = transport.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT)
        session = before["thread"]["session"]
        if (
            not isinstance(session, dict)
            or session["status"] != "running"
            or not isinstance(session["activeTurnId"], str)
            or not session["activeTurnId"]
        ):
            raise ConflictError("The thread has no running active turn to interrupt.")
        captured_turn_id = session["activeTurnId"]
        command = {
            "type": "thread.turn.interrupt",
            "commandId": transport.new_uuid4(),
            "threadId": thread_id,
            "turnId": captured_turn_id,
            "createdAt": _now_rfc3339(),
        }

        def interrupted(detail: dict[str, Any]) -> bool:
            thread = detail["thread"]
            current_session = thread["session"]
            latest = thread["latestTurn"]
            return (
                isinstance(current_session, dict)
                and current_session["activeTurnId"] is None
                and isinstance(latest, dict)
                and latest["turnId"] == captured_turn_id
                and latest["state"] in {"interrupted", "completed", "error"}
            )

        def newer_turn(detail: dict[str, Any]) -> bool:
            thread = detail["thread"]
            current_session = thread["session"]
            latest = thread["latestTurn"]
            active_id = current_session["activeTurnId"] if isinstance(current_session, dict) else None
            latest_id = latest["turnId"] if isinstance(latest, dict) else None
            if active_id is not None:
                return active_id != captured_turn_id
            return latest_id is not None and latest_id != captured_turn_id

        result = transport.mutate(
            thread_id,
            command,
            interrupted,
            race_detector=newer_turn,
        )
        return {
            "action": "best_effort_turn_interrupt",
            "captured_turn_id": captured_turn_id,
            "race_semantics": (
                "T3 has no atomic expected-turn guard and routes the interrupt to the provider "
                "session current at reactor execution."
            ),
            **result,
        }

    return _execute_operation(ctx, normalized, perform)


def t3_session_stop(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(raw_args, allowed={"thread_id"}, required={"thread_id"})
    thread_id = normalize_string(args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS)
    normalized = {"thread_id": thread_id}

    def perform(transport: T3Client) -> dict[str, Any]:
        before = transport.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT)
        session = before["thread"]["session"]
        if (
            isinstance(session, dict)
            and session["status"] == "stopped"
            and session["activeTurnId"] is None
        ):
            return {
                "action": "already_stopped",
                "command_id": None,
                "thread_id": thread_id,
                "detail": before,
            }

        command = {
            "type": "thread.session.stop",
            "commandId": transport.new_uuid4(),
            "threadId": thread_id,
            "createdAt": _now_rfc3339(),
        }

        def stopped(detail: dict[str, Any]) -> bool:
            current = detail["thread"]["session"]
            return (
                isinstance(current, dict)
                and current["status"] == "stopped"
                and current["activeTurnId"] is None
            )

        result = transport.mutate(thread_id, command, stopped)
        return {
            "action": "best_effort_session_stop",
            "race_semantics": "T3 stops the provider session current at reactor execution; no session identity guard exists.",
            **result,
        }

    return _execute_operation(ctx, normalized, perform)


OPERATIONS = {
    "t3_threads": t3_threads,
    "t3_thread_read": t3_thread_read,
    "t3_thread_create": t3_thread_create,
    "t3_thread_send": t3_thread_send,
    "t3_thread_set_mode": t3_thread_set_mode,
    "t3_thread_implement_plan": t3_thread_implement_plan,
    "t3_turn_interrupt": t3_turn_interrupt,
    "t3_session_stop": t3_session_stop,
    "t3_thread_wait": t3_thread_wait,
    "t3_thread_respond": t3_thread_respond,
}
