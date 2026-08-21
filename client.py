"""Bounded, loopback-only HTTP client for the T3 orchestration API."""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit


MAX_BASE_URL_CHARS = 2_048
MAX_TOKEN_CHARS = 16_384
MAX_IDENTIFIER_CHARS = 512
MAX_TITLE_CHARS = 512
MAX_CURSOR_CHARS = 4_096
MAX_MESSAGE_UTF16_UNITS = 120_000
MAX_REQUEST_BODY_BYTES = 1 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_TURN_LIMIT = 150
DEFAULT_TURN_LIMIT = 20
REQUEST_TIMEOUT_SECONDS = 10.0
MUTATION_TIMEOUT_SECONDS = 30.0
MUTATION_POLL_SECONDS = 5.0
MAX_DISPATCH_ATTEMPTS = 3
READ_CHUNK_BYTES = 64 * 1024
ERROR_VALUE_CHARS = 512

RUNTIME_MODES = frozenset(
    {"approval-required", "auto-accept-edits", "auto", "full-access"}
)
INTERACTION_MODES = frozenset({"default", "plan"})
MESSAGE_ROLES = frozenset({"user", "assistant", "system"})
SESSION_STATUSES = frozenset(
    {"idle", "starting", "running", "ready", "interrupted", "stopped", "error"}
)
LATEST_TURN_STATES = frozenset({"running", "interrupted", "completed", "error"})

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_BEARER_CREDENTIAL_RE = re.compile(r"[A-Za-z0-9\-._~+/]+={0,}", re.ASCII)
_RFC3339_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)
_DETAIL_PREFIX = "/api/orchestration/threads/"
_KNOWN_SERVER_FIELDS = ("code", "reason", "requiredScope", "traceId")


class T3ClientError(Exception):
    """Base typed, sanitized client failure."""

    error_code = "internal_error"
    retryable = False
    outcome_ambiguous = False
    default_message = "The local client failed safely."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
        command_id: str | None = None,
        thread_id: str | None = None,
        cause_code: str | None = None,
    ) -> None:
        self.safe_message = _sanitize_text(message or self.default_message)
        self.details = dict(details or {})
        self.command_id = command_id
        self.thread_id = thread_id
        self.cause_code = cause_code
        super().__init__(self.safe_message)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "error_code": self.error_code,
            "retryable": self.retryable,
            "outcome_ambiguous": self.outcome_ambiguous,
            "error": self.safe_message,
        }
        if self.command_id is not None:
            result["command_id"] = self.command_id
        if self.thread_id is not None:
            result["thread_id"] = self.thread_id
        if self.cause_code is not None:
            result["cause_code"] = self.cause_code
        if self.details:
            result["details"] = dict(self.details)
        return result


class InvalidInputError(T3ClientError):
    error_code = "invalid_input"
    default_message = "The supplied input is invalid."


class ConfigurationError(T3ClientError):
    error_code = "configuration_error"
    default_message = "The T3 client configuration is missing or unsafe."


class ConflictError(T3ClientError):
    error_code = "conflict"
    default_message = "The requested mutation conflicts with the current thread state."


class NetworkError(T3ClientError):
    error_code = "network_error"
    retryable = True
    default_message = "The loopback T3 service could not be reached within the deadline."


class ResponseTooLargeError(T3ClientError):
    error_code = "response_too_large"
    default_message = "The T3 response exceeded the configured byte limit."


class ResponseSchemaError(T3ClientError):
    error_code = "response_schema_error"
    default_message = "The successful T3 response did not match the required schema."


class HTTPResponseError(T3ClientError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        retryable: bool,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.error_code = error_code
        self.retryable = retryable
        self.outcome_ambiguous = False
        super().__init__(message, details=details)


class MutationAmbiguousError(T3ClientError):
    error_code = "mutation_ambiguous"
    outcome_ambiguous = True
    default_message = (
        "The mutation outcome could not be verified; read the exact target thread before any new command."
    )


class VerificationFailedError(T3ClientError):
    error_code = "verification_failed"
    outcome_ambiguous = True
    default_message = "T3 accepted the command, but its required thread state was not observed."


class ConcurrentStateChangeError(T3ClientError):
    error_code = "concurrent_state_change"
    outcome_ambiguous = True
    default_message = (
        "A newer turn was observed; newer provider work may have received the side effect."
    )


def _sanitize_text(value: Any, *, secret: str | None = None) -> str:
    text = value if isinstance(value, str) else str(value)
    if secret:
        text = text.replace(secret, "[redacted]")
    text = _CONTROL_RE.sub(" ", text)
    return text[:ERROR_VALUE_CHARS]


def is_valid_bearer_credential(value: Any) -> bool:
    """Return whether value is a bounded RFC 6750 bearer credential."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_TOKEN_CHARS
        and _BEARER_CREDENTIAL_RE.fullmatch(value) is not None
    )


def _contains_secret(value: Any, secret: str) -> bool:
    pending = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            if secret in current:
                return True
        elif isinstance(current, Mapping):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            pending.extend(current)
    return False


def _reject_active_token(value: Any, secret: str, name: str) -> None:
    if _contains_secret(value, secret):
        raise InvalidInputError(f"{name} contains the active credential.")


def normalize_string(
    value: Any,
    name: str,
    *,
    max_chars: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise InvalidInputError(f"{name} must be a string.")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise InvalidInputError(f"{name} must not be empty.")
    if len(normalized) > max_chars:
        raise InvalidInputError(f"{name} exceeds its character limit.")
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidInputError(f"{name} contains invalid Unicode.") from exc
    return normalized


def normalize_message(value: Any) -> str:
    message = normalize_string(
        value, "message", max_chars=MAX_MESSAGE_UTF16_UNITS, allow_empty=False
    )
    if len(message.encode("utf-16-le")) // 2 > MAX_MESSAGE_UTF16_UNITS:
        raise InvalidInputError("message exceeds 120000 UTF-16 code units.")
    return message


def validate_turn_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError("turn_limit must be an integer.")
    if not 1 <= value <= MAX_TURN_LIMIT:
        raise InvalidInputError("turn_limit must be between 1 and 150.")
    return value


def canonical_command_bytes(command: Mapping[str, Any]) -> bytes:
    if not isinstance(command, Mapping):
        raise InvalidInputError("command must be an object.")
    command_id = command.get("commandId")
    _validate_uuid4(command_id, "command.commandId")
    try:
        body = json.dumps(
            dict(command),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidInputError("command must be canonical JSON data.") from exc
    if len(body) > MAX_REQUEST_BODY_BYTES:
        raise InvalidInputError("command exceeds the request body byte limit.")
    return body


def _validate_uuid4(value: Any, name: str) -> str:
    text = normalize_string(value, name, max_chars=36)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise InvalidInputError(f"{name} must be a UUIDv4.") from exc
    if parsed.version != 4 or str(parsed) != text.lower():
        raise InvalidInputError(f"{name} must be a canonical UUIDv4.")
    return text.lower()


def validate_shell_snapshot(value: Any) -> dict[str, Any]:
    root = _object(value, "shell")
    _nonnegative_int(root, "snapshotSequence", "shell")
    projects = _list(root, "projects", "shell")
    threads = _list(root, "threads", "shell")
    _timestamp(root, "updatedAt", "shell")
    for index, project in enumerate(projects):
        _validate_project(project, f"shell.projects[{index}]")
    for index, thread in enumerate(threads):
        _validate_thread(thread, f"shell.threads[{index}]", detail=False)
    return root


def validate_thread_detail(value: Any) -> dict[str, Any]:
    root = _object(value, "detail")
    snapshot_sequence = _nonnegative_int(root, "snapshotSequence", "detail")
    _validate_thread(_required(root, "thread", "detail"), "detail.thread", detail=True)
    if "page" in root:
        page = _object(root["page"], "detail.page")
        _nullable_string(page, "beforeCursor", "detail.page")
        _boolean(page, "hasMore", "detail.page")
        page_sequence = _nonnegative_int(page, "snapshotSequence", "detail.page")
        if page_sequence != snapshot_sequence:
            raise ResponseSchemaError("Thread page snapshot sequence is inconsistent.")
        if "threadSequence" in page:
            _nonnegative_int(page, "threadSequence", "detail.page")
    return root


def validate_dispatch_response(value: Any) -> dict[str, Any]:
    root = _object(value, "dispatch")
    _nonnegative_int(root, "sequence", "dispatch")
    return root


def _validate_project(value: Any, path: str) -> None:
    project = _object(value, path)
    _nonempty_string(project, "id", path)
    _nonempty_string(project, "title", path)
    _nonempty_string(project, "workspaceRoot", path)
    if "defaultModelSelection" not in project:
        raise ResponseSchemaError(f"{path}.defaultModelSelection is required.")
    if project["defaultModelSelection"] is not None:
        _validate_model_selection(project["defaultModelSelection"], f"{path}.defaultModelSelection")


def _validate_model_selection(value: Any, path: str) -> None:
    selection = _object(value, path)
    _nonempty_string(selection, "instanceId", path)
    _nonempty_string(selection, "model", path)
    if "options" in selection:
        options = selection["options"]
        if not isinstance(options, list):
            raise ResponseSchemaError(f"{path}.options must be a list.")
        for index, raw_option in enumerate(options):
            option_path = f"{path}.options[{index}]"
            option = _object(raw_option, option_path)
            _nonempty_string(option, "id", option_path)
            option_value = _required(option, "value", option_path)
            if isinstance(option_value, bool):
                continue
            if not isinstance(option_value, str) or not option_value.strip():
                raise ResponseSchemaError(
                    f"{option_path}.value must be a non-empty string or Boolean."
                )


def _validate_thread(value: Any, path: str, *, detail: bool) -> None:
    thread = _object(value, path)
    thread_id = _nonempty_string(thread, "id", path)
    for field in ("projectId", "title"):
        _nonempty_string(thread, field, path)
    _validate_model_selection(_required(thread, "modelSelection", path), f"{path}.modelSelection")
    _enum(thread, "runtimeMode", RUNTIME_MODES, path)
    _enum(thread, "interactionMode", INTERACTION_MODES, path)
    _nullable_string(thread, "branch", path)
    _nullable_string(thread, "worktreePath", path)
    _nullable_timestamp(thread, "archivedAt", path)
    _timestamp(thread, "createdAt", path)
    _timestamp(thread, "updatedAt", path)
    if "latestTurn" not in thread:
        raise ResponseSchemaError(f"{path}.latestTurn is required.")
    if thread["latestTurn"] is not None:
        _validate_latest_turn(thread["latestTurn"], f"{path}.latestTurn")
    if "session" not in thread:
        raise ResponseSchemaError(f"{path}.session is required.")
    if thread["session"] is not None:
        _validate_session(thread["session"], f"{path}.session", thread_id)
    if detail:
        _nullable_timestamp(thread, "deletedAt", path)
        messages = _list(thread, "messages", path)
        for index, message in enumerate(messages):
            _validate_message(message, f"{path}.messages[{index}]")
        proposed_plans = _list(thread, "proposedPlans", path)
        for index, proposed_plan in enumerate(proposed_plans):
            _validate_proposed_plan(
                proposed_plan, f"{path}.proposedPlans[{index}]"
            )


def _validate_latest_turn(value: Any, path: str) -> None:
    turn = _object(value, path)
    _nonempty_string(turn, "turnId", path)
    _enum(turn, "state", LATEST_TURN_STATES, path)
    _timestamp(turn, "requestedAt", path)
    _nullable_timestamp(turn, "startedAt", path)
    _nullable_timestamp(turn, "completedAt", path)
    _nullable_string(turn, "assistantMessageId", path)
    if "sourceProposedPlan" in turn:
        _validate_source_proposed_plan(
            turn["sourceProposedPlan"], f"{path}.sourceProposedPlan"
        )


def _validate_source_proposed_plan(value: Any, path: str) -> None:
    source = _object(value, path)
    _nonempty_string(source, "threadId", path)
    _nonempty_string(source, "planId", path)


def _validate_proposed_plan(value: Any, path: str) -> None:
    plan = _object(value, path)
    _nonempty_string(plan, "id", path)
    _nullable_string(plan, "turnId", path)
    _nonempty_string(plan, "planMarkdown", path)
    implemented_at = _nullable_timestamp(plan, "implementedAt", path)
    implementation_thread_id = _nullable_string(
        plan, "implementationThreadId", path
    )
    if (implemented_at is None) != (implementation_thread_id is None):
        raise ResponseSchemaError(
            f"{path}.implementedAt and {path}.implementationThreadId must both be null or non-null."
        )
    _timestamp(plan, "createdAt", path)
    _timestamp(plan, "updatedAt", path)


def _validate_session(value: Any, path: str, parent_thread_id: str) -> None:
    session = _object(value, path)
    if _nonempty_string(session, "threadId", path) != parent_thread_id:
        raise ResponseSchemaError("Thread session identity is inconsistent.")
    _enum(session, "status", SESSION_STATUSES, path)
    _nullable_string(session, "providerName", path)
    _enum(session, "runtimeMode", RUNTIME_MODES, path)
    _nullable_string(session, "activeTurnId", path)
    _nullable_string(session, "lastError", path)
    _timestamp(session, "updatedAt", path)


def _validate_message(value: Any, path: str) -> None:
    message = _object(value, path)
    _nonempty_string(message, "id", path)
    _enum(message, "role", MESSAGE_ROLES, path)
    if not isinstance(_required(message, "text", path), str):
        raise ResponseSchemaError(f"{path}.text must be a string.")
    _nullable_string(message, "turnId", path)
    _boolean(message, "streaming", path)
    _timestamp(message, "createdAt", path)
    _timestamp(message, "updatedAt", path)


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ResponseSchemaError(f"{path} must be an object.")
    return value


def _required(obj: Mapping[str, Any], field: str, path: str) -> Any:
    if field not in obj:
        raise ResponseSchemaError(f"{path}.{field} is required.")
    return obj[field]


def _list(obj: Mapping[str, Any], field: str, path: str) -> list[Any]:
    value = _required(obj, field, path)
    if not isinstance(value, list):
        raise ResponseSchemaError(f"{path}.{field} must be a list.")
    return value


def _nonempty_string(obj: Mapping[str, Any], field: str, path: str) -> str:
    value = _required(obj, field, path)
    if not isinstance(value, str) or not value.strip():
        raise ResponseSchemaError(f"{path}.{field} must be a non-empty string.")
    return value


def _nullable_string(obj: Mapping[str, Any], field: str, path: str) -> str | None:
    value = _required(obj, field, path)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ResponseSchemaError(f"{path}.{field} must be null or a non-empty string.")
    return value


def _nonnegative_int(obj: Mapping[str, Any], field: str, path: str) -> int:
    value = _required(obj, field, path)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResponseSchemaError(f"{path}.{field} must be a nonnegative integer.")
    return value


def _boolean(obj: Mapping[str, Any], field: str, path: str) -> bool:
    value = _required(obj, field, path)
    if not isinstance(value, bool):
        raise ResponseSchemaError(f"{path}.{field} must be a Boolean.")
    return value


def _enum(obj: Mapping[str, Any], field: str, allowed: frozenset[str], path: str) -> str:
    value = _required(obj, field, path)
    if not isinstance(value, str) or value not in allowed:
        raise ResponseSchemaError(f"{path}.{field} has an unsupported value.")
    return value


def _timestamp(obj: Mapping[str, Any], field: str, path: str) -> str:
    value = _required(obj, field, path)
    if not isinstance(value, str) or _RFC3339_RE.fullmatch(value) is None:
        raise ResponseSchemaError(f"{path}.{field} must be an RFC 3339 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise ResponseSchemaError(f"{path}.{field} must be an RFC 3339 timestamp.") from exc
    if parsed.utcoffset() is None:
        raise ResponseSchemaError(f"{path}.{field} must be an RFC 3339 timestamp.")
    return value


def _nullable_timestamp(obj: Mapping[str, Any], field: str, path: str) -> str | None:
    value = _required(obj, field, path)
    if value is None:
        return None
    _timestamp({field: value}, field, path)
    return value


class T3Client:
    """Synchronous direct transport with operation-wide monotonic deadlines."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        request_timeout: float = REQUEST_TIMEOUT_SECONDS,
        mutation_timeout: float = MUTATION_TIMEOUT_SECONDS,
        mutation_poll_timeout: float = MUTATION_POLL_SECONDS,
        response_limit: int = MAX_RESPONSE_BYTES,
        request_body_limit: int = MAX_REQUEST_BODY_BYTES,
        dispatch_attempts: int = MAX_DISPATCH_ATTEMPTS,
        poll_interval: float = 0.05,
        retry_backoff: tuple[float, ...] = (0.05, 0.1),
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
        connection_factory: Callable[[str, str, int, float], Any] | None = None,
    ) -> None:
        self.scheme, self.host, self.port, self.origin = self._validate_origin(base_url)
        if not is_valid_bearer_credential(token):
            raise ConfigurationError("T3_ORCHESTRATION_TOKEN is missing or invalid.")
        self.token = token
        self.request_timeout = _bounded_positive(
            request_timeout, REQUEST_TIMEOUT_SECONDS, "request_timeout"
        )
        self.mutation_timeout = _bounded_positive(
            mutation_timeout, MUTATION_TIMEOUT_SECONDS, "mutation_timeout"
        )
        self.mutation_poll_timeout = _bounded_positive(
            mutation_poll_timeout, MUTATION_POLL_SECONDS, "mutation_poll_timeout"
        )
        self.response_limit = _bounded_int(response_limit, MAX_RESPONSE_BYTES, "response_limit")
        self.request_body_limit = _bounded_int(
            request_body_limit, MAX_REQUEST_BODY_BYTES, "request_body_limit"
        )
        self.dispatch_attempts = _bounded_int(
            dispatch_attempts, MAX_DISPATCH_ATTEMPTS, "dispatch_attempts"
        )
        self.poll_interval = _bounded_positive(poll_interval, 1.0, "poll_interval")
        if not isinstance(retry_backoff, tuple) or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or value > 1.0
            for value in retry_backoff
        ):
            raise ConfigurationError("retry_backoff must contain finite values from 0 to 1.")
        self.retry_backoff = retry_backoff
        self.clock = clock
        self.sleeper = sleeper
        self.uuid_factory = uuid_factory
        self.connection_factory = connection_factory

    @staticmethod
    def _validate_origin(base_url: Any) -> tuple[str, str, int, str]:
        if not isinstance(base_url, str) or not base_url or len(base_url) > MAX_BASE_URL_CHARS:
            raise ConfigurationError("base_url is missing or exceeds 2048 characters.")
        if base_url != base_url.strip() or _CONTROL_RE.search(base_url):
            raise ConfigurationError("base_url contains unsafe whitespace or control characters.")
        try:
            parsed = urlsplit(base_url)
            port = parsed.port
        except ValueError as exc:
            raise ConfigurationError("base_url is not a valid origin.") from exc
        if parsed.scheme not in {"http", "https"}:
            raise ConfigurationError("base_url must use http or https.")
        if parsed.username is not None or parsed.password is not None:
            raise ConfigurationError("base_url must not contain credentials.")
        if (
            parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or "?" in base_url
            or "#" in base_url
        ):
            raise ConfigurationError("base_url must be a root origin without query or fragment.")
        if not parsed.hostname or "%" in parsed.hostname:
            raise ConfigurationError("base_url host must be a numeric loopback address.")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ConfigurationError("base_url host must be a numeric loopback address.") from exc
        if not address.is_loopback:
            raise ConfigurationError("base_url host must be loopback.")
        if port is None:
            port = 443 if parsed.scheme == "https" else 80
        elif port == 0:
            raise ConfigurationError("base_url port must be between 1 and 65535.")
        display_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
        default_port = 443 if parsed.scheme == "https" else 80
        origin = f"{parsed.scheme}://{display_host}"
        if port != default_port:
            origin += f":{port}"
        return parsed.scheme, address.compressed, port, origin

    def new_uuid4(self) -> str:
        try:
            candidate = self.uuid_factory()
        except Exception as exc:
            raise T3ClientError() from exc
        return _validate_uuid4(str(candidate), "generated identity")

    def get_shell(self, *, deadline: float | None = None) -> dict[str, Any]:
        value = self._request_json("GET", "/api/orchestration/shell", deadline=deadline)
        return validate_shell_snapshot(value)

    def get_thread(
        self,
        thread_id: Any,
        *,
        turn_limit: Any = DEFAULT_TURN_LIMIT,
        before_cursor: Any = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        normalized_id = normalize_string(
            thread_id, "thread_id", max_chars=MAX_IDENTIFIER_CHARS
        )
        _reject_active_token(normalized_id, self.token, "thread_id")
        limit = validate_turn_limit(turn_limit)
        query: list[tuple[str, str]] = [("turnLimit", str(limit))]
        if before_cursor is not None:
            cursor = normalize_string(
                before_cursor, "before_cursor", max_chars=MAX_CURSOR_CHARS
            )
            _reject_active_token(cursor, self.token, "before_cursor")
            query.append(("beforeCursor", cursor))
        path = f"{_DETAIL_PREFIX}{quote(normalized_id, safe='')}?{urlencode(query)}"
        value = self._request_json("GET", path, deadline=deadline)
        detail = validate_thread_detail(value)
        if detail["thread"]["id"] != normalized_id:
            raise ResponseSchemaError("Thread response identity does not match the request.")
        return detail

    def mutate(
        self,
        thread_id: Any,
        command: Mapping[str, Any],
        predicate: Callable[[dict[str, Any]], bool],
        *,
        race_detector: Callable[[dict[str, Any]], bool] | None = None,
        require_accepted_sequence: bool = False,
    ) -> dict[str, Any]:
        normalized_thread_id = normalize_string(
            thread_id, "thread_id", max_chars=MAX_IDENTIFIER_CHARS
        )
        _reject_active_token(normalized_thread_id, self.token, "thread_id")
        _reject_active_token(command, self.token, "command")
        if not isinstance(require_accepted_sequence, bool):
            raise InvalidInputError("require_accepted_sequence must be a Boolean.")
        body = canonical_command_bytes(command)
        if len(body) > self.request_body_limit:
            raise InvalidInputError("command exceeds the configured request body limit.")
        command_id = _validate_uuid4(command.get("commandId"), "command.commandId")
        if command.get("threadId") != normalized_thread_id:
            raise InvalidInputError("command.threadId must match the exact readback target.")
        if not callable(predicate):
            raise InvalidInputError("mutation predicate must be callable.")
        if race_detector is not None and not callable(race_detector):
            raise InvalidInputError("race detector must be callable.")

        operation_deadline = self.clock() + self.mutation_timeout
        last_detail: dict[str, Any] | None = None
        ambiguous_cause_code: str | None = None
        for attempt in range(1, self.dispatch_attempts + 1):
            try:
                dispatch = validate_dispatch_response(
                    self._request_json(
                        "POST",
                        "/api/orchestration/dispatch",
                        body=body,
                        deadline=operation_deadline,
                    )
                )
            except T3ClientError as exc:
                if exc.error_code not in {
                    "network_error",
                    "server_error",
                    "response_too_large",
                    "response_schema_error",
                    "internal_error",
                }:
                    if ambiguous_cause_code is not None:
                        raise MutationAmbiguousError(
                            command_id=command_id,
                            thread_id=normalized_thread_id,
                            cause_code=exc.error_code,
                        ) from exc
                    raise
                if ambiguous_cause_code is None:
                    ambiguous_cause_code = exc.error_code
                last_detail = self._ambiguous_readback(
                    normalized_thread_id,
                    predicate,
                    race_detector,
                    command_id,
                    operation_deadline,
                    exc.error_code,
                )
                if last_detail is not None and not require_accepted_sequence:
                    return self._mutation_result(
                        command_id,
                        normalized_thread_id,
                        last_detail,
                        sequence=None,
                        attempts=attempt,
                        recovered=True,
                    )
                if attempt >= self.dispatch_attempts:
                    raise MutationAmbiguousError(
                        command_id=command_id,
                        thread_id=normalized_thread_id,
                        cause_code=exc.error_code,
                    ) from exc
                self._sleep_before_retry(attempt - 1, operation_deadline, command_id, normalized_thread_id)
                continue

            sequence = dispatch["sequence"]
            try:
                detail = self._poll_accepted(
                    normalized_thread_id,
                    predicate,
                    race_detector,
                    command_id,
                    sequence,
                    operation_deadline,
                )
            except VerificationFailedError as exc:
                if ambiguous_cause_code is None:
                    raise
                raise MutationAmbiguousError(
                    command_id=command_id,
                    thread_id=normalized_thread_id,
                    cause_code=exc.error_code,
                    details=exc.details,
                ) from exc
            return self._mutation_result(
                command_id,
                normalized_thread_id,
                detail,
                sequence=sequence,
                attempts=attempt,
                recovered=ambiguous_cause_code is not None,
            )

        raise MutationAmbiguousError(
            command_id=command_id,
            thread_id=normalized_thread_id,
            cause_code="internal_error",
        )

    def _ambiguous_readback(
        self,
        thread_id: str,
        predicate: Callable[[dict[str, Any]], bool],
        race_detector: Callable[[dict[str, Any]], bool] | None,
        command_id: str,
        deadline: float,
        cause_code: str,
    ) -> dict[str, Any] | None:
        try:
            detail = self.get_thread(thread_id, turn_limit=MAX_TURN_LIMIT, deadline=deadline)
            if race_detector is not None and race_detector(detail):
                raise ConcurrentStateChangeError(command_id=command_id, thread_id=thread_id)
            return detail if predicate(detail) else None
        except ConcurrentStateChangeError:
            raise
        except Exception as exc:
            secondary_code = exc.error_code if isinstance(exc, T3ClientError) else "internal_error"
            raise MutationAmbiguousError(
                command_id=command_id,
                thread_id=thread_id,
                cause_code=secondary_code or cause_code,
            ) from exc

    def _poll_accepted(
        self,
        thread_id: str,
        predicate: Callable[[dict[str, Any]], bool],
        race_detector: Callable[[dict[str, Any]], bool] | None,
        command_id: str,
        sequence: int,
        operation_deadline: float,
    ) -> dict[str, Any]:
        poll_deadline = min(operation_deadline, self.clock() + self.mutation_poll_timeout)
        latest: dict[str, Any] | None = None
        while True:
            if self.clock() >= poll_deadline:
                self._raise_verification_failed(command_id, thread_id, sequence, latest)
            try:
                latest = self.get_thread(
                    thread_id, turn_limit=MAX_TURN_LIMIT, deadline=poll_deadline
                )
                if race_detector is not None and race_detector(latest):
                    raise ConcurrentStateChangeError(command_id=command_id, thread_id=thread_id)
                if latest["snapshotSequence"] >= sequence and predicate(latest):
                    return latest
            except ConcurrentStateChangeError:
                raise
            except Exception as exc:
                cause_code = exc.error_code if isinstance(exc, T3ClientError) else "internal_error"
                raise MutationAmbiguousError(
                    command_id=command_id,
                    thread_id=thread_id,
                    cause_code=cause_code,
                ) from exc
            if self.clock() >= poll_deadline:
                self._raise_verification_failed(command_id, thread_id, sequence, latest)
            remaining = poll_deadline - self.clock()
            if remaining <= 0:
                self._raise_verification_failed(command_id, thread_id, sequence, latest)
            self.sleeper(min(self.poll_interval, remaining))

    @staticmethod
    def _raise_verification_failed(
        command_id: str,
        thread_id: str,
        sequence: int,
        latest: dict[str, Any] | None,
    ) -> None:
        details: dict[str, Any] = {"dispatch_sequence": sequence}
        if latest is not None:
            details["observed_snapshot_sequence"] = latest["snapshotSequence"]
        raise VerificationFailedError(
            command_id=command_id,
            thread_id=thread_id,
            details=details,
        )

    @staticmethod
    def _mutation_result(
        command_id: str,
        thread_id: str,
        detail: dict[str, Any],
        *,
        sequence: int | None,
        attempts: int,
        recovered: bool,
    ) -> dict[str, Any]:
        return {
            "command_id": command_id,
            "thread_id": thread_id,
            "dispatch_sequence": sequence,
            "dispatch_attempts": attempts,
            "recovered_after_ambiguous_dispatch": recovered,
            "detail": detail,
        }

    def _sleep_before_retry(
        self, index: int, deadline: float, command_id: str, thread_id: str
    ) -> None:
        duration = self.retry_backoff[min(index, len(self.retry_backoff) - 1)] if self.retry_backoff else 0
        try:
            self._sleep_clipped(duration, deadline)
        except NetworkError as exc:
            raise MutationAmbiguousError(
                command_id=command_id,
                thread_id=thread_id,
                cause_code="network_error",
            ) from exc

    def _sleep_clipped(self, duration: float, deadline: float) -> None:
        remaining = self._remaining(deadline)
        self.sleeper(min(duration, remaining))
        self._remaining(deadline)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        deadline: float | None = None,
    ) -> Any:
        self._validate_endpoint(method, path)
        if body is not None and (not isinstance(body, bytes) or len(body) > self.request_body_limit):
            raise InvalidInputError("request body exceeds its byte limit.")
        operation_deadline = deadline if deadline is not None else self.clock() + self.request_timeout
        request_deadline = min(operation_deadline, self.clock() + self.request_timeout)
        connection: Any = None
        response: Any = None
        status: int | None = None
        try:
            timeout = self._remaining(request_deadline)
            connection = self._connection(timeout)
            connection.connect()
            self._set_socket_timeout(connection, request_deadline)
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "Connection": "close",
            }
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request(method, path, body=body, headers=headers)
            self._set_socket_timeout(connection, request_deadline)
            response = connection.getresponse()
            status = response.status
            raw = self._read_bounded(response, connection, request_deadline)
        except T3ClientError:
            raise
        except (socket.timeout, TimeoutError, OSError, http.client.HTTPException) as exc:
            raise NetworkError() from exc
        except Exception as exc:
            raise T3ClientError() from exc
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

        if status is None:
            raise T3ClientError()
        if not 200 <= status < 300:
            raise self._http_error(status, raw)
        try:
            decoded = json.loads(
                raw.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError, MemoryError) as exc:
            raise ResponseSchemaError() from exc
        if _contains_secret(decoded, self.token):
            raise ResponseSchemaError()
        return decoded

    def _connection(self, timeout: float) -> Any:
        if self.connection_factory is not None:
            return self.connection_factory(self.scheme, self.host, self.port, timeout)
        if self.scheme == "https":
            return http.client.HTTPSConnection(
                self.host,
                self.port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def _read_bounded(self, response: Any, connection: Any, deadline: float) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            self._set_socket_timeout(connection, deadline)
            remaining_capacity = self.response_limit - total
            chunk = response.read1(min(READ_CHUNK_BYTES, remaining_capacity + 1))
            self._remaining(deadline)
            if not chunk:
                break
            total += len(chunk)
            if total > self.response_limit:
                raise ResponseTooLargeError()
            chunks.append(chunk)
        return b"".join(chunks)

    def _set_socket_timeout(self, connection: Any, deadline: float) -> None:
        timeout = self._remaining(deadline)
        sock = getattr(connection, "sock", None)
        if sock is not None:
            sock.settimeout(timeout)
        if hasattr(connection, "timeout"):
            connection.timeout = timeout

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self.clock()
        if not math.isfinite(remaining) or remaining <= 0:
            raise NetworkError()
        return remaining

    def _http_error(self, status: int, raw: bytes) -> HTTPResponseError:
        details = self._known_error_details(raw)
        if status == 400:
            return HTTPResponseError(
                "invalid_request", "T3 rejected the request.", retryable=False, details=details
            )
        if status == 401:
            return HTTPResponseError(
                "authentication_error",
                "T3 authentication failed; refresh the operator-managed secret before a new call.",
                retryable=True,
                details=details,
            )
        if status == 403:
            return HTTPResponseError(
                "authorization_error",
                "T3 authorization failed for the required orchestration scope.",
                retryable=False,
                details=details,
            )
        if status == 404:
            return HTTPResponseError(
                "not_found", "The requested T3 target was not found.", retryable=False, details=details
            )
        if status in {408, 429} or 500 <= status <= 599:
            return HTTPResponseError(
                "server_error", "T3 could not complete the request.", retryable=True, details=details
            )
        return HTTPResponseError(
            "http_error", "T3 returned a terminal HTTP response.", retryable=False, details=details
        )

    def _known_error_details(self, raw: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, MemoryError):
            return {}
        candidates = [decoded]
        if isinstance(decoded, dict) and isinstance(decoded.get("error"), dict):
            candidates.insert(0, decoded["error"])
        result: dict[str, Any] = {}
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for field in _KNOWN_SERVER_FIELDS:
                value = candidate.get(field)
                if isinstance(value, (str, int, float, bool)) and not isinstance(value, (dict, list)):
                    result[field] = _sanitize_text(value, secret=self.token)
        return result

    @staticmethod
    def _validate_endpoint(method: str, path: str) -> None:
        if method == "GET" and path == "/api/orchestration/shell":
            return
        if method == "POST" and path == "/api/orchestration/dispatch":
            return
        if method == "GET" and path.startswith(_DETAIL_PREFIX):
            split = urlsplit(path)
            segment = split.path[len(_DETAIL_PREFIX) :]
            if segment and "/" not in segment and not split.fragment:
                try:
                    pairs = parse_qsl(split.query, keep_blank_values=True, strict_parsing=True)
                except ValueError:
                    pairs = []
                keys = [key for key, _ in pairs]
                if keys in (["turnLimit"], ["turnLimit", "beforeCursor"]):
                    return
        raise InvalidInputError("HTTP method/path is outside the orchestration allowlist.")


def _bounded_positive(value: Any, maximum: float, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        or value > maximum
    ):
        raise ConfigurationError(f"{name} must be positive and no greater than {maximum}.")
    return float(value)


def _bounded_int(value: Any, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
        raise ConfigurationError(f"{name} must be an integer from 1 through {maximum}.")
    return value
