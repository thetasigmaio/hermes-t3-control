"""Synchronous Hermes handlers for bounded T3 thread orchestration."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import datetime, timezone
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


def _profile_secret(name: str) -> str | None:
    from agent.secret_scope import get_secret

    return get_secret(name)


def check_t3_available() -> bool:
    """Passive, profile-safe credential availability probe."""
    try:
        value = _profile_secret(TOKEN_ENV)
    except Exception:
        return False
    return is_valid_bearer_credential(value)


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
        except Exception:
            return _json_result(T3ClientError().to_dict())

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


def _normalize_optional_string(value: Any, name: str, maximum: int) -> str | None:
    if value is None:
        return None
    return normalize_string(value, name, max_chars=maximum)


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


def t3_threads(ctx: Any, raw_args: Any) -> dict[str, Any]:
    _args(raw_args, allowed=set(), required=set())
    transport = _make_client(ctx)
    return {"shell": transport.get_shell()}


def t3_thread_read(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(
        raw_args,
        allowed={"thread_id", "turn_limit", "before_cursor"},
        required={"thread_id"},
    )
    thread_id = normalize_string(args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS)
    turn_limit = validate_turn_limit(args.get("turn_limit", 20))
    before_cursor = args.get("before_cursor")
    if before_cursor is not None:
        if "turn_limit" not in args:
            _invalid("before_cursor requires an explicit turn_limit.")
        before_cursor = normalize_string(
            before_cursor, "before_cursor", max_chars=MAX_CURSOR_CHARS
        )
    transport = _make_client(ctx)
    return {
        "detail": transport.get_thread(
            thread_id,
            turn_limit=turn_limit,
            before_cursor=before_cursor,
        )
    }


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
    branch = _normalize_optional_string(args.get("branch"), "branch", MAX_BRANCH_CHARS)
    worktree_path = _normalize_optional_string(
        args.get("worktree_path"), "worktree_path", MAX_WORKTREE_PATH_CHARS
    )

    transport = _make_client(ctx)
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
            copy.deepcopy(default_selection) if same_as_default else explicit_selection
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


def t3_thread_send(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(
        raw_args,
        allowed={"thread_id", "message"},
        required={"thread_id", "message"},
    )
    thread_id = normalize_string(args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS)
    text = normalize_message(args["message"])
    transport = _make_client(ctx)
    before = transport.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT)
    stored = before["thread"]
    _ensure_mutable_thread(stored)
    try:
        command, message_id = _build_turn_command(transport, stored, text)

        def message_observed(detail: dict[str, Any]) -> bool:
            return _turn_observed(detail, command, message_id)

        result = transport.mutate(thread_id, command, message_observed)
    except T3ClientError as exc:
        raise _annotate_error(exc, race_semantics=MODE_RACE_SEMANTICS)
    observed = result["detail"]["thread"]
    return {
        "action": "new_turn_same_thread",
        "message_id": message_id,
        **result,
        "provider_session": observed["session"],
        "latest_turn": observed["latestTurn"],
        "race_semantics": MODE_RACE_SEMANTICS,
    }


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

    transport = _make_client(ctx)
    before = transport.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT)
    stored = before["thread"]
    _ensure_mutable_thread(stored)
    captured_turn_id = _active_turn_id(stored)

    common: dict[str, Any] = {
        "mode_field": public_field,
        "mode": mode,
    }
    if has_runtime and captured_turn_id is not None:
        common.update(
            {
                "active_turn_unchanged": captured_turn_id,
                "warning": PENDING_APPROVAL_WARNING,
            }
        )
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
        if has_runtime and captured_turn_id is not None:
            _annotate_error(
                exc,
                active_turn_unchanged=captured_turn_id,
                warning=PENDING_APPROVAL_WARNING,
            )
        raise
    return {
        "action": "thread_mode_set",
        **result,
        **common,
    }


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
    transport = _make_client(ctx)
    before = transport.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT)
    plan = _require_plan_ready(before, plan_id)
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
        raise _annotate_error(
            exc,
            workflow_phase="interaction_mode_transition",
            race_semantics=PLAN_RACE_SEMANTICS,
        )

    try:
        _require_plan_ready(
            mode_result["detail"], plan_id, expected_plan=plan_identity
        )
    except T3ClientError as exc:
        raise _annotate_error(
            exc,
            workflow_phase="post_mode_revalidation",
            completed_phase="interaction_mode_set",
            verified_mode_command_id=mode_result["command_id"],
            race_semantics=PLAN_RACE_SEMANTICS,
        )
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
        raise _annotate_error(
            exc,
            workflow_phase="implementation_turn",
            completed_phase="interaction_mode_set",
            verified_mode_command_id=mode_result["command_id"],
            race_semantics=PLAN_RACE_SEMANTICS,
        )
    observed = turn_result["detail"]["thread"]
    return {
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


def t3_turn_interrupt(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(raw_args, allowed={"thread_id"}, required={"thread_id"})
    thread_id = normalize_string(args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS)
    transport = _make_client(ctx)
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


def t3_session_stop(ctx: Any, raw_args: Any) -> dict[str, Any]:
    args = _args(raw_args, allowed={"thread_id"}, required={"thread_id"})
    thread_id = normalize_string(args["thread_id"], "thread_id", max_chars=MAX_IDENTIFIER_CHARS)
    transport = _make_client(ctx)
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


OPERATIONS = {
    "t3_threads": t3_threads,
    "t3_thread_read": t3_thread_read,
    "t3_thread_create": t3_thread_create,
    "t3_thread_send": t3_thread_send,
    "t3_thread_set_mode": t3_thread_set_mode,
    "t3_thread_implement_plan": t3_thread_implement_plan,
    "t3_turn_interrupt": t3_turn_interrupt,
    "t3_session_stop": t3_session_stop,
}
