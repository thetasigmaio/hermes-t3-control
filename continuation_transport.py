"""Authenticated T3 subscribeThread transport for the continuation worker."""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import time
from collections.abc import AsyncIterator
from typing import Any, Callable
from urllib.parse import quote

try:
    from . import auth
    from .client import T3ClientError
except ImportError:
    import auth
    from client import T3ClientError


MAX_WS_MESSAGE_BYTES = 1024 * 1024
MAX_CHUNK_ITEMS = 256
CONNECT_TIMEOUT_SECONDS = 10.0
RPC_REQUEST_ID = 1


class ContinuationTransportError(RuntimeError):
    pass


def read_thread_snapshot(
    ctx: Any,
    *,
    thread_id: str,
    environment_id: str,
) -> dict[str, Any]:
    """Read one exact authenticated thread snapshot for hint reconciliation."""
    arguments = {
        "operation": "continuation-snapshot",
        "thread_id": thread_id,
        "environment_id": environment_id,
    }
    with auth.operation_client(ctx, arguments) as lease:
        client = lease.client
        descriptor = client.get_environment_descriptor()
        if descriptor.get("environmentId") != environment_id:
            raise ContinuationTransportError("bound T3 environment no longer matches")
        return client.get_thread(thread_id, turn_limit=1)


def _websocket_url(client: Any, ticket: str) -> str:
    scheme = "wss" if client.scheme == "https" else "ws"
    host = f"[{client.host}]" if ":" in client.host else client.host
    return f"{scheme}://{host}:{client.port}/ws?wsTicket={quote(ticket, safe='')}"


async def _pinned_socket(client: Any) -> socket.socket:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(client.host, client.port, type=socket.SOCK_STREAM)
    if not infos:
        raise ContinuationTransportError("T3 websocket address did not resolve")
    family, socktype, proto, _, sockaddr = infos[0]
    sock = socket.socket(family, socktype, proto)
    sock.setblocking(False)
    try:
        await asyncio.wait_for(loop.sock_connect(sock, sockaddr), CONNECT_TIMEOUT_SECONDS)
        validator = client.connected_socket_validator
        if validator is not None:
            validator(sock, time.monotonic() + CONNECT_TIMEOUT_SECONDS)
        return sock
    except BaseException:
        sock.close()
        raise


def _decode_message(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, (str, bytes)):
        raise ContinuationTransportError("T3 websocket returned an unsupported frame")
    if len(raw) > MAX_WS_MESSAGE_BYTES:
        raise ContinuationTransportError("T3 websocket frame exceeded its size budget")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError, RecursionError, MemoryError) as exc:
        raise ContinuationTransportError("T3 websocket returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ContinuationTransportError("T3 websocket returned a non-object message")
    return value


async def subscribe_thread(
    ctx: Any,
    *,
    thread_id: str,
    environment_id: str,
    after_sequence: int | None,
    stop_event: asyncio.Event,
    is_active: Callable[[], bool] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield typed stream items until cancelled or the RPC exits."""
    try:
        import websockets
    except ImportError as exc:
        raise ContinuationTransportError(
            "T3 continuation requires Hermes' websockets 15 runtime dependency"
        ) from exc

    arguments = {
        "operation": "continuation-subscribe",
        "thread_id": thread_id,
        "environment_id": environment_id,
    }
    with auth.operation_client(ctx, arguments) as lease:
        client = lease.client
        descriptor = client.get_environment_descriptor()
        if descriptor.get("environmentId") != environment_id:
            raise ContinuationTransportError("bound T3 environment no longer matches")
        sock = await _pinned_socket(client)
        try:
            # Prove this socket still belongs to the process-pinned runtime lease
            # before minting or putting a short-lived credential on the wire.
            ticket = client.issue_websocket_ticket()["ticket"]
            websocket_url = _websocket_url(client, ticket)
            tls_context = ssl.create_default_context() if client.scheme == "https" else None
            async with websockets.connect(
                websocket_url,
                sock=sock,
                ssl=tls_context,
                server_hostname=client.host if tls_context is not None else None,
                open_timeout=CONNECT_TIMEOUT_SECONDS,
                close_timeout=2.0,
                max_size=MAX_WS_MESSAGE_BYTES,
                max_queue=16,
                ping_interval=10.0,
                ping_timeout=10.0,
                proxy=None,
            ) as websocket:
                sock = None
                payload: dict[str, Any] = {
                    "threadId": thread_id,
                    "requestCompletionMarker": True,
                    "turnLimit": 1,
                }
                if after_sequence is not None:
                    payload["afterSequence"] = after_sequence
                request = {
                    "_tag": "Request",
                    "id": RPC_REQUEST_ID,
                    "tag": "orchestration.subscribeThread",
                    "payload": payload,
                    "headers": [],
                }
                await websocket.send(json.dumps(request, separators=(",", ":")))
                while not stop_event.is_set():
                    receive = asyncio.create_task(websocket.recv())
                    stopped = asyncio.create_task(stop_event.wait())
                    done, pending = await asyncio.wait(
                        {receive, stopped},
                        timeout=1.0,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    if not done:
                        if is_active is not None and not is_active():
                            await websocket.send(
                                json.dumps({"_tag": "Interrupt", "requestId": RPC_REQUEST_ID})
                            )
                            return
                        continue
                    if stopped in done and stopped.result():
                        receive.cancel()
                        await websocket.send(
                            json.dumps({"_tag": "Interrupt", "requestId": RPC_REQUEST_ID})
                        )
                        return
                    message = _decode_message(receive.result())
                    tag = message.get("_tag")
                    if tag == "Ping":
                        await websocket.send(json.dumps({"_tag": "Pong"}))
                        continue
                    if tag == "Chunk" and message.get("requestId") == RPC_REQUEST_ID:
                        values = message.get("values")
                        if not isinstance(values, list) or len(values) > MAX_CHUNK_ITEMS:
                            raise ContinuationTransportError("T3 stream chunk is invalid")
                        for value in values:
                            if not isinstance(value, dict):
                                raise ContinuationTransportError("T3 stream item is invalid")
                            yield value
                        await websocket.send(
                            json.dumps({"_tag": "Ack", "requestId": RPC_REQUEST_ID})
                        )
                        continue
                    if tag == "Exit" and message.get("requestId") == RPC_REQUEST_ID:
                        raise ContinuationTransportError("T3 subscription ended")
                    if tag == "Defect":
                        raise ContinuationTransportError("T3 subscription failed")
        except T3ClientError:
            raise
        except asyncio.CancelledError:
            raise
        except ContinuationTransportError:
            raise
        except Exception as exc:
            raise ContinuationTransportError("T3 websocket connection failed") from exc
        finally:
            if sock is not None:
                sock.close()
