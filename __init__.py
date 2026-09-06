"""Hermes native plugin registration boundary."""

from __future__ import annotations

try:
    from .continuation import register_continuation_lifecycle
    from .continuation_cli import make_continuation_cli_handler, setup_continuation_cli
    from .schemas import SCHEMAS, TOOL_NAMES
    from .tools import OPERATIONS, TOOLSET, bind_handler, check_t3_available
except ImportError:  # Direct repository import used by unit tests.
    from continuation import register_continuation_lifecycle
    from continuation_cli import make_continuation_cli_handler, setup_continuation_cli
    from schemas import SCHEMAS, TOOL_NAMES
    from tools import OPERATIONS, TOOLSET, bind_handler, check_t3_available


def register(ctx) -> None:
    """Register tools/operator CLI; the observer stays inert unless explicitly enabled."""
    for name in TOOL_NAMES:
        schema = SCHEMAS[name]
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=bind_handler(ctx, OPERATIONS[name]),
            check_fn=check_t3_available,
            requires_env=[],
            is_async=False,
            description=schema["description"],
            override=False,
        )
    register_cli = getattr(ctx, "register_cli_command", None)
    if callable(register_cli):
        register_cli(
            name="t3-continuation",
            help="Manage allowlisted T3-to-Hermes continuation bindings",
            setup_fn=setup_continuation_cli,
            handler_fn=make_continuation_cli_handler(ctx),
            description="Bind, inspect, pause, cancel, or acknowledge durable continuation events.",
        )
    register_continuation_lifecycle(ctx)
