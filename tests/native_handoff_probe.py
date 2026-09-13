"""Explicit isolated native discovery/dispatch probe; run with the patched host Python.

The T3 HTTP peer and final model execution are deterministic fixtures. No live
profile, gateway, provider, or messaging service is accessed.
"""
from __future__ import annotations
import argparse
import asyncio
import contextvars
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest import mock


def projected_message(*, message_id, role, text, turn_id, created_at):
    return {"id": message_id, "role": role, "text": text, "turnId": turn_id,
        "streaming": False, "createdAt": created_at, "updatedAt": created_at}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--surface", choices=("desktop", "gateway"), required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(Path(args.host).resolve()))
    from support import LoopbackServer, Response, detail_snapshot, latest_turn, session
    with tempfile.TemporaryDirectory() as temporary:
        home = Path(temporary)
        os.environ["HERMES_HOME"] = str(home)
        os.environ.pop("HERMES_SAFE_MODE", None)
        installed = home / "plugins" / "hermes-t3-control"
        installed.mkdir(parents=True)
        for path in root.glob("*.py"):
            shutil.copy2(path, installed / path.name)
        shutil.copy2(root / "plugin.yaml", installed / "plugin.yaml")
        commands = []
        before = detail_snapshot(sequence=3, turn=latest_turn(turn_id="old", state="completed"), current_session=session(status="ready"))
        def dispatch(request):
            commands.append(json.loads(request["body"]))
            return Response(value={"sequence": 4})
        def projected(request):
            command = commands[-1]
            return Response(value=detail_snapshot(sequence=4, messages=[projected_message(message_id=command["message"]["messageId"], role="user", text="Scoped task", turn_id=None, created_at=command["createdAt"])], turn=latest_turn(turn_id="old", state="completed"), current_session=session(status="ready")))
        with LoopbackServer([Response(value=before), Response(value={"environmentId": "environment-1", "serverVersion": "test"}), dispatch, projected, Response(value=before), Response(value=before)]) as peer:
            import yaml
            (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {"enabled": ["hermes-t3-control"], "entries": {"hermes-t3-control": {"enabled": True, "allow_gateway_injection": True, "settings": {"auth_mode": "external-token", "base_url": peer.base_url, "continuation_enabled": True}}}}}))
            from hermes_cli.plugins import PluginManager
            from tools.thread_context import propagate_context_to_thread
            from agent.delegation_context import delegated_child_context
            manager = PluginManager(scope_key=str(home))
            manager.discover_and_load()
            loaded = manager._plugins["hermes-t3-control"]
            assert loaded.enabled and loaded.error is None, loaded.error
            ctx = manager._desktop_contexts["hermes-t3-control"]
            package = loaded.module.__name__
            plugin_tools = importlib.import_module(package + ".tools")
            plugin_auth = importlib.import_module(package + ".auth")
            continuation = importlib.import_module(package + ".continuation")
            Store = importlib.import_module(package + ".continuation_state").ContinuationStore
            current = contextvars.ContextVar("probe-current", default=None)
            agent = SimpleNamespace(session_id="physical-1", api_mode="codex_responses", provider="openai-codex")
            row = {"session_key": "agent:main:telegram:dm:42", "agent": agent, "transport": object(), "history": [], "history_lock": threading.RLock(), "profile_home": home, "running": False}
            receipts = []
            def model_boundary(rid, sid, row, content, **kwargs):
                event = kwargs["gateway_system_event"]
                assert event.eligibility_check()
                receipts.append(event)
                kwargs["terminal_callback"]({"status": "settled"})
                row["running"] = False
                return True
            from tui_gateway.plugin_events import DesktopPluginConsumer, desktop_plugin_turn
            server = SimpleNamespace(_sessions={"ui-1": row}, _sessions_lock=threading.RLock(), _current_runtime_session_record=current, _session_has_live_transport=lambda r: r.get("transport") is not None, _run_prompt_submit=model_boundary)
            consumer = DesktopPluginConsumer(manager, server)
            manager._desktop_consumer = consumer
            ctx._desktop_observer_ready = True
            manager._desktop_tasks = {"hermes-t3-control:probe": SimpleNamespace(done=lambda: False)}
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever)
            thread.start()
            try:
                if args.surface == "desktop":
                    native_scope = lambda internal=False: desktop_plugin_turn(row, internal=internal)
                else:
                    from gateway.run import GatewayRunner
                    from gateway.session import SessionSource
                    from gateway.config import Platform
                    from gateway.session_context import plugin_gateway_turn
                    from gateway.internal_events import resolve_gateway_event_receipt
                    owner = object.__new__(GatewayRunner)
                    source = SessionSource(platform=Platform.TELEGRAM, user_id="42", chat_id="42", chat_type="dm")
                    entry = SimpleNamespace(origin=source, session_id="physical-1", suspended=False, resume_pending=False)
                    owner._gateway_loop, owner._running, owner._draining = loop, True, False
                    owner.session_store = SimpleNamespace()
                    owner._async_session_store = SimpleNamespace(_store=owner.session_store, lookup_by_session_key=mock.AsyncMock(return_value=entry), typed_event_recovery_owner_state=mock.AsyncMock(return_value="none"))
                    owner._session_db = SimpleNamespace(get_session=lambda sid: {"ended_at": None})
                    owner._session_key_for_source = lambda source: row["session_key"]
                    owner._is_user_authorized = lambda *a, **k: True
                    owner._adapter_for_source = lambda source: object()
                    turn = SimpleNamespace(source=source, session_id="physical-1", session_key=row["session_key"], gateway_system_event=None)
                    def native_scope(internal=False):
                        turn.gateway_system_event = object() if internal else None
                        return plugin_gateway_turn(owner, turn, agent)
                    def admit_model(*, content, system_event, receipt):
                        assert system_event.eligibility_check()
                        receipts.append(system_event)
                        resolve_gateway_event_receipt(receipt, "completed", event=system_event)
                        return receipt
                    manager.set_gateway_message_injector(owner, admit_model)
                    manager._gateway_tasks = {("hermes-t3-control", "probe"): SimpleNamespace(done=lambda: False)}
                    ctx._gateway_observer_ready = True
                with mock.patch("hermes_cli.plugins.get_plugin_manager", return_value=manager), mock.patch.object(plugin_tools, "_profile_secret", return_value=peer.token), mock.patch.object(plugin_auth, "_profile_secret", return_value=peer.token):
                    current.set(row)
                    with native_scope():
                        result = asyncio.run(asyncio.to_thread(propagate_context_to_thread(lambda: json.loads(ctx.dispatch_tool("t3_thread_send", {"thread_id": "thread-1", "message": "Scoped task", "busy_policy": "queue"})))) )
                    assert result["ok"] and result["continuation"]["armed"], result
                    store = Store(ctx.state.data_dir)
                    binding = store.get_binding(result["continuation"]["binding_id"])
                    assert store.reserved_source(binding) == commands[0]["message"]["messageId"]
                    assert binding.platform == ("desktop" if args.surface == "desktop" else "telegram")
                    assert binding.hermes_session_id == "physical-1" and binding.max_continuations == 1
                    # Native nullable-message start frames map the exact reserved source.
                    captured = json.loads((root / "tests/fixtures/native_subscribe_nullable_turn.json").read_text())
                    for item in captured:
                        if item["kind"] != "event":
                            continue
                        raw = json.dumps(item["event"]).replace("fixture-id-37", "thread-1").replace("fixture-id-7", store.reserved_source(binding)).replace("2026-09-09T", "2099-09-09T")
                        event = json.loads(raw)
                        store.ingest(binding, cursor_sequence=event["sequence"], event=None, native_event=event)
                    assert store.reserved_turn(binding) == "fixture-id-1"
                    event = continuation._event_base(binding, sequence=884806, event_id="completed", turn_id="fixture-id-1", event_kind="external_tool_completed", occurred_at="2099-09-09T21:02:00Z")
                    event = continuation.reserved_event(store, binding, event, {"thread": {"messages": [{"id": store.reserved_source(binding), "role": "user", "turnId": None}]}})
                    assert event is not None and store.ingest(binding, cursor_sequence=884806, event=event)
                    assert asyncio.run(continuation.dispatch_one(ctx, store, binding))
                    assert len(receipts) == 1
                    assert not asyncio.run(continuation.dispatch_one(ctx, store, binding))
                    # Internal turns and delegate_task children cannot mint another mission.
                    for internal, delegated in ((True, False), (False, True)):
                        with native_scope(internal):
                            with delegated_child_context() if delegated else mock.patch.object(agent, "session_id", "physical-1"):
                                target = getattr(ctx, "current_" + args.surface + "_destination")()
                                assert target is None
                                refused = asyncio.run(asyncio.to_thread(propagate_context_to_thread(lambda: json.loads(ctx.dispatch_tool("t3_thread_send", {"thread_id": "thread-1", "message": "Scoped task", "busy_policy": "queue"})))))
                                assert refused["error_code"] == "conflict", refused
                                assert len(commands) == 1
                print(json.dumps({"surface": args.surface, "source_dispatches": len(commands), "typed_receipts": len(receipts), "discovery": True, "worker_context": True, "nullable_source_correlation": True, "live_acceptance": False}))
            finally:
                loop.call_soon_threadsafe(loop.stop)
                thread.join()
                loop.close()


if __name__ == "__main__":
    main()
