"""Exact public JSON schemas for the Hermes T3 control tools."""

from __future__ import annotations

from typing import Any


_ID = {"type": "string", "minLength": 1, "maxLength": 512}
_RUNTIME_MODE = {
    "type": "string",
    "enum": ["approval-required", "auto-accept-edits", "auto", "full-access"],
}
_INTERACTION_MODE = {"type": "string", "enum": ["default", "plan"]}
_MESSAGE = {"type": "string", "minLength": 1, "maxLength": 120000}
_MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
_MODEL_OPTION = {
    "type": "object",
    "properties": {
        "id": dict(_ID),
        "value": {
            "oneOf": [
                {"type": "string", "minLength": 1, "maxLength": 512},
                {"type": "boolean"},
            ]
        },
    },
    "required": ["id", "value"],
    "additionalProperties": False,
}


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


T3_THREADS_SCHEMA = _schema(
    "t3_threads",
    "List compact, filtered T3 threads by default, or return the legacy raw shell snapshot explicitly.",
    {
        "view": {"type": "string", "enum": ["compact", "raw"], "default": "compact"},
        "project": dict(_ID),
        "workspace": {"type": "string", "minLength": 1, "maxLength": 4096},
        "title_query": {"type": "string", "minLength": 1, "maxLength": 512},
        "lifecycle": {
            "type": "string",
            "enum": [
                "idle",
                "starting",
                "running",
                "ready",
                "interrupted",
                "stopped",
                "error",
                "blocked",
            ],
        },
        "updated_within_minutes": {"type": "integer", "minimum": 1, "maximum": 10080},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        "require_one": {"type": "boolean", "default": False},
    },
    [],
)

T3_THREAD_READ_SCHEMA = _schema(
    "t3_thread_read",
    "Read one exact T3 thread with bounded turn pagination.",
    {
        "thread_id": dict(_ID),
        "view": {"type": "string", "enum": ["material", "raw"], "default": "material"},
        "turn_limit": {"type": "integer", "minimum": 1, "maximum": 150, "default": 20},
        "before_cursor": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4096,
            "description": "When supplied, requires turn_limit to be supplied explicitly.",
        },
    },
    ["thread_id"],
)

T3_THREAD_CREATE_SCHEMA = _schema(
    "t3_thread_create",
    "Create one T3-native thread, optionally start its first turn after verified readback, and warn when full-access allows trusted provider work to mutate without approval.",
    {
        "project_id": dict(_ID),
        "title": {"type": "string", "minLength": 1, "maxLength": 512},
        "instance_id": {
            **_ID,
            "description": "instance_id and model must be supplied together.",
        },
        "model": {
            **_ID,
            "description": "instance_id and model must be supplied together.",
        },
        "model_options": {
            "type": "array",
            "items": _MODEL_OPTION,
            "maxItems": 64,
            "description": "model_options requires instance_id and model.",
        },
        "runtime_mode": dict(_RUNTIME_MODE),
        "interaction_mode": dict(_INTERACTION_MODE),
        "initial_message": dict(_MESSAGE),
        "branch": {"type": "string", "minLength": 1, "maxLength": 512},
        "worktree_path": {"type": "string", "minLength": 1, "maxLength": 4096},
    },
    ["project_id", "title"],
)

T3_THREAD_SEND_SCHEMA = _schema(
    "t3_thread_send",
    "Start a new turn on the supplied existing T3 thread, including after provider-session stop.",
    {
        "thread_id": dict(_ID),
        "message": dict(_MESSAGE),
        "busy_policy": {"type": "string", "enum": ["reject", "queue"], "default": "reject"},
    },
    ["thread_id", "message"],
)

T3_THREAD_WAIT_SCHEMA = _schema(
    "t3_thread_wait",
    "Wait at most 30 seconds for one thread to progress, require action, or settle.",
    {
        "thread_id": dict(_ID),
        "after_thread_sequence": {
            "type": "integer",
            "minimum": 0,
            "maximum": 2**63 - 1,
        },
        "until": {
            "type": "string",
            "enum": ["change", "running", "blocked", "terminal", "error"],
            "default": "terminal",
        },
        "timeout_seconds": {"type": "integer", "minimum": 0, "maximum": 30, "default": 0},
    },
    ["thread_id"],
)

T3_THREAD_RESPOND_SCHEMA = _schema(
    "t3_thread_respond",
    "Respond to one exact pending approval or user-input request after fail-closed readback.",
    {
        "thread_id": dict(_ID),
        "request_id": dict(_ID),
        "decision": {
            "type": "string",
            "enum": ["accept", "acceptForSession", "decline", "cancel"],
        },
        "answers": {
            "type": "object",
            "minProperties": 1,
            "maxProperties": 64,
            "propertyNames": dict(_ID),
            "additionalProperties": {
                "oneOf": [
                    {"type": "string", "minLength": 0, "maxLength": 120_000},
                    {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "minLength": 0,
                            "maxLength": 120_000,
                        },
                        "maxItems": 64,
                    },
                    {
                        "type": "number",
                        "minimum": -_MAX_SAFE_JSON_INTEGER,
                        "maximum": _MAX_SAFE_JSON_INTEGER,
                    },
                    {"type": "boolean"},
                    {"type": "null"},
                ]
            },
        },
    },
    ["thread_id", "request_id"],
)
T3_THREAD_RESPOND_SCHEMA["parameters"]["oneOf"] = [
    {"required": ["decision"]},
    {"required": ["answers"]},
]

T3_THREAD_SET_MODE_SCHEMA = _schema(
    "t3_thread_set_mode",
    "Set exactly one persisted thread mode with exact readback; full-access permits trusted provider work to execute commands and modify or delete files without approval, while a runtime change cannot cancel or retroactively authorize an approval already pending for a started turn.",
    {
        "thread_id": dict(_ID),
        "runtime_mode": dict(_RUNTIME_MODE),
        "interaction_mode": dict(_INTERACTION_MODE),
    },
    ["thread_id"],
)
T3_THREAD_SET_MODE_SCHEMA["parameters"]["oneOf"] = [
    {"required": ["runtime_mode"]},
    {"required": ["interaction_mode"]},
]

T3_THREAD_IMPLEMENT_PLAN_SCHEMA = _schema(
    "t3_thread_implement_plan",
    "Verify and implement one stored unimplemented T3 proposed plan on the same thread through the native Plan-to-Build transition.",
    {"thread_id": dict(_ID), "plan_id": dict(_ID)},
    ["thread_id", "plan_id"],
)

T3_TURN_INTERRUPT_SCHEMA = _schema(
    "t3_turn_interrupt",
    "Best-effort interrupt of the provider session current for a thread; correlation is not an atomic turn guard.",
    {"thread_id": dict(_ID)},
    ["thread_id"],
)

T3_SESSION_STOP_SCHEMA = _schema(
    "t3_session_stop",
    "Best-effort stop of the provider session current for a thread without deleting or replacing the thread.",
    {"thread_id": dict(_ID)},
    ["thread_id"],
)

SCHEMAS = {
    schema["name"]: schema
    for schema in (
        T3_THREADS_SCHEMA,
        T3_THREAD_READ_SCHEMA,
        T3_THREAD_CREATE_SCHEMA,
        T3_THREAD_SEND_SCHEMA,
        T3_THREAD_SET_MODE_SCHEMA,
        T3_THREAD_IMPLEMENT_PLAN_SCHEMA,
        T3_TURN_INTERRUPT_SCHEMA,
        T3_SESSION_STOP_SCHEMA,
        T3_THREAD_WAIT_SCHEMA,
        T3_THREAD_RESPOND_SCHEMA,
    )
}

TOOL_NAMES = tuple(SCHEMAS)
