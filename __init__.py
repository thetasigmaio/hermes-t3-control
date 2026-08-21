"""Hermes native plugin registration boundary."""

from __future__ import annotations

try:
    from .schemas import SCHEMAS, TOOL_NAMES
    from .tools import OPERATIONS, TOKEN_ENV, TOOLSET, bind_handler, check_t3_available
except ImportError:  # Direct repository import used by unit tests.
    from schemas import SCHEMAS, TOOL_NAMES
    from tools import OPERATIONS, TOKEN_ENV, TOOLSET, bind_handler, check_t3_available


def register(ctx) -> None:
    """Register eight synchronous tools without constructing a client or doing I/O."""
    for name in TOOL_NAMES:
        schema = SCHEMAS[name]
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=bind_handler(ctx, OPERATIONS[name]),
            check_fn=check_t3_available,
            requires_env=[TOKEN_ENV],
            is_async=False,
            description=schema["description"],
            override=False,
        )
