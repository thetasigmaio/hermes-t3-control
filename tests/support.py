"""Ephemeral loopback fixtures for client contract tests."""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable


NOW = "2026-08-21T12:00:00Z"


@dataclass
class Response:
    status: int = 200
    value: Any = None
    body: bytes | None = None
    chunks: tuple[bytes, ...] = ()
    delay_before_body: float = 0.0
    delay_between_chunks: float = 0.0
    headers: tuple[tuple[str, str], ...] = ()


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        return


class LoopbackServer:
    def __init__(self, script: list[Response | Callable[[dict[str, Any]], Response]]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                self._serve()

            def do_POST(self) -> None:
                self._serve()

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                record = {
                    "method": self.command,
                    "path": self.path,
                    "body": body,
                    "authorization": self.headers.get("Authorization"),
                }
                with fixture.lock:
                    fixture.requests.append(record)
                    item = fixture.script.pop(0) if fixture.script else Response(status=500, value={})
                response = item(record) if callable(item) else item
                if response.body is not None:
                    payload = response.body
                elif response.chunks:
                    payload = b"".join(response.chunks)
                else:
                    payload = json.dumps(response.value).encode("utf-8")
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                for name, value in response.headers:
                    self.send_header(name, value)
                self.end_headers()
                if response.delay_before_body:
                    time.sleep(response.delay_before_body)
                chunks = response.chunks or (payload,)
                for index, chunk in enumerate(chunks):
                    if index and response.delay_between_chunks:
                        time.sleep(response.delay_between_chunks)
                    self.wfile.write(chunk)
                    self.wfile.flush()

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.httpd = QuietThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "LoopbackServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.httpd.shutdown()
        self.thread.join()
        self.httpd.server_close()


def model_selection() -> dict[str, Any]:
    return {
        "instanceId": "codex-main",
        "model": "gpt-current",
        "options": [
            {"id": "reasoning_effort", "value": "high"},
            {"id": "web_search", "value": True},
        ],
    }


def source_proposed_plan(
    *, thread_id: str = "thread-1", plan_id: str = "plan-1"
) -> dict[str, Any]:
    return {"threadId": thread_id, "planId": plan_id}


def latest_turn(
    *,
    turn_id: str = "turn-1",
    state: str = "completed",
    source_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = {
        "turnId": turn_id,
        "state": state,
        "requestedAt": NOW,
        "startedAt": NOW,
        "completedAt": NOW if state != "running" else None,
        "assistantMessageId": "assistant-1" if state != "running" else None,
    }
    if source_plan is not None:
        value["sourceProposedPlan"] = source_plan
    return value


def proposed_plan(
    *,
    plan_id: str = "plan-1",
    turn_id: str | None = "turn-1",
    plan_markdown: str = "## Proposed plan\n\nImplement the requested change.",
    implemented_at: str | None = None,
    implementation_thread_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": plan_id,
        "turnId": turn_id,
        "planMarkdown": plan_markdown,
        "implementedAt": implemented_at,
        "implementationThreadId": implementation_thread_id,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def session(
    *,
    status: str = "ready",
    active_turn_id: str | None = None,
    provider_instance_id: str | None = "codex-main",
) -> dict[str, Any]:
    value = {
        "threadId": "thread-1",
        "status": status,
        "providerName": "codex",
        "runtimeMode": "approval-required",
        "activeTurnId": active_turn_id,
        "lastError": None,
        "updatedAt": NOW,
    }
    if provider_instance_id is not None:
        value["providerInstanceId"] = provider_instance_id
    return value


def thread_core(
    *,
    thread_id: str = "thread-1",
    turn: dict[str, Any] | None = None,
    current_session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": thread_id,
        "projectId": "project-1",
        "title": "A thread",
        "modelSelection": model_selection(),
        "runtimeMode": "approval-required",
        "interactionMode": "default",
        "branch": None,
        "worktreePath": None,
        "latestTurn": turn,
        "createdAt": NOW,
        "updatedAt": NOW,
        "archivedAt": None,
        "session": current_session,
    }


def shell_snapshot(*, sequence: int = 1, extra: bool = False) -> dict[str, Any]:
    value = {
        "snapshotSequence": sequence,
        "projects": [
            {
                "id": "project-1",
                "title": "Project",
                "workspaceRoot": "/work/project",
                "defaultModelSelection": model_selection(),
                "futureProjectField": {"kept": True},
            }
        ],
        "threads": [thread_core(current_session=session())],
        "updatedAt": NOW,
    }
    if extra:
        value["futureTopLevelField"] = [1, 2, 3]
    return value


def message(
    *,
    message_id: str = "message-1",
    text: str = "hello",
    turn_id: str | None = "turn-1",
) -> dict[str, Any]:
    return {
        "id": message_id,
        "role": "user",
        "text": text,
        "turnId": turn_id,
        "streaming": False,
        "createdAt": NOW,
        "updatedAt": NOW,
    }


def detail_snapshot(
    *,
    sequence: int = 1,
    thread_id: str = "thread-1",
    messages: list[dict[str, Any]] | None = None,
    turn: dict[str, Any] | None = None,
    current_session: dict[str, Any] | None = None,
    proposed_plans: list[dict[str, Any]] | None = None,
    page: bool = True,
) -> dict[str, Any]:
    thread = thread_core(
        thread_id=thread_id,
        turn=turn,
        current_session=current_session,
    )
    thread.update(
        {
            "deletedAt": None,
            "messages": list(messages or []),
            "proposedPlans": list(proposed_plans or []),
        }
    )
    value: dict[str, Any] = {"snapshotSequence": sequence, "thread": thread}
    if page:
        value["page"] = {
            "beforeCursor": None,
            "hasMore": False,
            "snapshotSequence": sequence,
            "threadSequence": sequence,
        }
    return value
