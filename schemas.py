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
    "List the validated T3 orchestration shell snapshot and thread statuses.",
    {},
    [],
)

T3_THREAD_READ_SCHEMA = _schema(
    "t3_thread_read",
    "Read one exact T3 thread with bounded turn pagination.",
    {
        "thread_id": dict(_ID),
        "turn_limit": {"type": "integer", "minimum": 1, "maximum": 150, "default": 20},
        "before_cursor": {"type": "string", "minLength": 1, "maxLength": 4096},
    },
    ["thread_id"],
)

T3_THREAD_CREATE_SCHEMA = _schema(
    "t3_thread_create",
    "Create one T3-native thread, optionally start its first turn after verified readback, and warn when full-access allows trusted provider work to mutate without approval.",
    {
        "project_id": dict(_ID),
        "title": {"type": "string", "minLength": 1, "maxLength": 512},
        "instance_id": dict(_ID),
        "model": dict(_ID),
        "model_options": {
            "type": "array",
            "items": _MODEL_OPTION,
            "maxItems": 64,
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
    },
    ["thread_id", "message"],
)

T3_THREAD_SET_MODE_SCHEMA = _schema(
    "t3_thread_set_mode",
    "Set exactly one persisted thread mode with exact readback; a runtime change cannot cancel or retroactively authorize an approval already pending for a started turn.",
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
    )
}

TOOL_NAMES = tuple(SCHEMAS)
