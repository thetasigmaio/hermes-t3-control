#!/usr/bin/env python3
"""Conditionally read one operator-designated isolated T3 thread."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from client import T3Client, T3ClientError  # noqa: E402


ISOLATION_ENV = "T3_SMOKE_ISOLATED"
THREAD_ENV = "T3_SMOKE_THREAD_ID"
BASE_URL_ENV = "T3_ORCHESTRATION_BASE_URL"
TOKEN_ENV = "T3_ORCHESTRATION_TOKEN"


class SmokeConfigurationError(Exception):
    """A sanitized live-smoke precondition failure."""


def _required_environment() -> tuple[str, str, str]:
    if os.environ.get(ISOLATION_ENV) != "1":
        raise SmokeConfigurationError("The explicit isolated-thread assertion is not set.")
    missing = tuple(
        name for name in (THREAD_ENV, BASE_URL_ENV, TOKEN_ENV) if not os.environ.get(name)
    )
    if missing:
        raise SmokeConfigurationError(
            "Required live-smoke environment is incomplete: " + ", ".join(missing)
        )
    thread_id = os.environ[THREAD_ENV]
    if "solarsim" in thread_id.casefold():
        raise SmokeConfigurationError("The designated thread is not eligible for this smoke gate.")
    return thread_id, os.environ[BASE_URL_ENV], os.environ[TOKEN_ENV]


def main() -> int:
    try:
        thread_id, base_url, token = _required_environment()
        detail = T3Client(base_url, token).get_thread(thread_id, turn_limit=1)
    except SmokeConfigurationError as exc:
        print(f"Live smoke skipped: {exc}", file=sys.stderr)
        return 2
    except T3ClientError as exc:
        print(f"Live smoke failed safely: {exc.error_code}", file=sys.stderr)
        return 1
    except Exception:
        print("Live smoke failed safely: internal_error", file=sys.stderr)
        return 1

    thread = detail["thread"]
    summary = {
        "interaction_mode": thread["interactionMode"],
        "ok": True,
        "runtime_mode": thread["runtimeMode"],
        "snapshot_sequence": detail["snapshotSequence"],
        "thread_id": thread["id"],
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
