from __future__ import annotations

import json
import asyncio
import contextlib
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import client
import continuation_transport
from tests.support import LoopbackServer, Response


class ContinuationTransportTests(unittest.TestCase):
    @staticmethod
    def expiry(minutes=5):
        return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace(
            "+00:00", "Z"
        )

    def test_ticket_uses_exact_allowlisted_endpoint(self):
        token = "t" * 40
        with LoopbackServer(
            [Response(value={"ticket": "w" * 40, "expiresAt": self.expiry()})]
        ) as server:
            transport = client.T3Client(server.base_url, token)
            ticket = transport.issue_websocket_ticket()
        self.assertEqual(ticket["ticket"], "w" * 40)
        self.assertEqual(server.requests[0]["method"], "POST")
        self.assertEqual(server.requests[0]["path"], "/api/auth/websocket-ticket")
        self.assertEqual(server.requests[0]["authorization"], f"Bearer {token}")

    def test_ticket_rejects_malformed_or_reflected_values(self):
        token = "t" * 40
        for value in (
            {"ticket": "", "expiresAt": self.expiry()},
            {"ticket": token, "expiresAt": self.expiry()},
            {"ticket": "ok", "expiresAt": ""},
            {"ticket": "ok", "expiresAt": self.expiry(minutes=-1)},
            {"ticket": "ok", "expiresAt": self.expiry(minutes=20)},
        ):
            with self.subTest(value=value), LoopbackServer([Response(value=value)]) as server:
                transport = client.T3Client(server.base_url, token)
                with self.assertRaises(client.T3ClientError):
                    transport.issue_websocket_ticket()

    def test_rpc_frames_are_bounded_objects(self):
        self.assertEqual(
            continuation_transport._decode_message(json.dumps({"_tag": "Ping"})),
            {"_tag": "Ping"},
        )
        with self.assertRaises(continuation_transport.ContinuationTransportError):
            continuation_transport._decode_message("[]")

    def test_exact_snapshot_reuses_operation_auth_and_environment_guard(self):
        calls = []
        expected = {"snapshotSequence": 7, "thread": {"id": "thread-1"}}

        class FakeClient:
            def get_environment_descriptor(self):
                calls.append("descriptor")
                return {"environmentId": "environment-1"}

            def get_thread(self, thread_id, *, turn_limit):
                calls.append((thread_id, turn_limit))
                return expected

        @contextlib.contextmanager
        def operation_client(_ctx, arguments):
            self.assertEqual(arguments["operation"], "continuation-snapshot")
            yield types.SimpleNamespace(client=FakeClient())

        with mock.patch.object(
            continuation_transport.auth,
            "operation_client",
            side_effect=operation_client,
        ):
            result = continuation_transport.read_thread_snapshot(
                object(), thread_id="thread-1", environment_id="environment-1"
            )
        self.assertIs(result, expected)
        self.assertEqual(calls, ["descriptor", ("thread-1", 1)])

    def test_subscription_pins_before_ticket_and_uses_exact_rpc_frames(self):
        order = []
        sent = []
        connect_call = {}

        class FakeClient:
            scheme = "http"
            host = "127.0.0.1"
            port = 9137

            def get_environment_descriptor(self):
                order.append("descriptor")
                return {"environmentId": "environment-1"}

            def issue_websocket_ticket(self):
                order.append("ticket")
                return {"ticket": "test-ticket"}

        class FakeSocket:
            def close(self):
                order.append("socket-close")

        class FakeWebSocket:
            def __init__(self):
                self.received = [
                    json.dumps(
                        {
                            "_tag": "Chunk",
                            "requestId": 1,
                            "values": [{"kind": "synchronized"}],
                        }
                    ),
                    json.dumps({"_tag": "Exit", "requestId": 1}),
                ]

            async def send(self, value):
                sent.append(json.loads(value))

            async def recv(self):
                return self.received.pop(0)

        websocket = FakeWebSocket()

        class ConnectContext:
            async def __aenter__(self):
                return websocket

            async def __aexit__(self, *_args):
                return False

        def connect(url, **kwargs):
            order.append("connect")
            connect_call.update({"url": url, **kwargs})
            return ConnectContext()

        async def pinned(_client):
            order.append("pin")
            return FakeSocket()

        @contextlib.contextmanager
        def operation_client(_ctx, _arguments):
            yield types.SimpleNamespace(client=FakeClient())

        async def exercise(after_sequence):
            stream = continuation_transport.subscribe_thread(
                object(),
                thread_id="thread-1",
                environment_id="environment-1",
                after_sequence=after_sequence,
                stop_event=asyncio.Event(),
            )
            self.assertEqual(await stream.__anext__(), {"kind": "synchronized"})
            with self.assertRaises(continuation_transport.ContinuationTransportError):
                await stream.__anext__()

        fake_websockets = types.SimpleNamespace(connect=connect)
        with (
            mock.patch.object(
                continuation_transport.auth,
                "operation_client",
                side_effect=operation_client,
            ),
            mock.patch.object(continuation_transport, "_pinned_socket", side_effect=pinned),
            mock.patch.dict(sys.modules, {"websockets": fake_websockets}),
        ):
            asyncio.run(exercise(41))
            websocket.received = [
                json.dumps(
                    {
                        "_tag": "Chunk",
                        "requestId": 1,
                        "values": [{"kind": "synchronized"}],
                    }
                ),
                json.dumps({"_tag": "Exit", "requestId": 1}),
            ]
            asyncio.run(exercise(None))

        self.assertEqual(order[:4], ["descriptor", "pin", "ticket", "connect"])
        self.assertEqual(connect_call["proxy"], None)
        self.assertEqual(
            sent,
            [
                {
                    "_tag": "Request",
                    "id": 1,
                    "tag": "orchestration.subscribeThread",
                    "payload": {
                        "threadId": "thread-1",
                        "afterSequence": 41,
                        "requestCompletionMarker": True,
                        "turnLimit": 1,
                    },
                    "headers": [],
                },
                {"_tag": "Ack", "requestId": 1},
                {
                    "_tag": "Request",
                    "id": 1,
                    "tag": "orchestration.subscribeThread",
                    "payload": {
                        "threadId": "thread-1",
                        "requestCompletionMarker": True,
                        "turnLimit": 1,
                    },
                    "headers": [],
                },
                {"_tag": "Ack", "requestId": 1},
            ],
        )


if __name__ == "__main__":
    unittest.main()
