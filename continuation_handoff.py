"""Host-owned completion registration at the existing send boundary."""
from __future__ import annotations

try:
    from .client import ConflictError, InvalidInputError
    from . import continuation_notifications
    from .continuation_state import ContinuationStore, ContinuationStateError, utc_now
except ImportError:
    from client import ConflictError, InvalidInputError
    import continuation_notifications
    from continuation_state import ContinuationStore, ContinuationStateError, utc_now

FIELDS = frozenset({"binding_id", "owner_id", "environment_id",
                    "source_identity", "followup_scope", "max_continuations", "expected_turn_id"})


def prepare_handoff(ctx, value, thread_id, before, transport, message_id):
    if value is None:
        return prepare_automatic_handoff(ctx, thread_id, before, transport, message_id)
    if not isinstance(value, dict) or not FIELDS <= set(value) or set(value) - FIELDS - {"sunsama_task_id", "notify_telegram", "replaces", "desktop_upgrade"}:
        raise InvalidInputError("continuation requires complete explicit mission authority")
    if "desktop_upgrade" in value and not isinstance(value["desktop_upgrade"], bool):
        raise InvalidInputError("desktop_upgrade must be a boolean")
    try:
        from .continuation import binding_enabled
    except ImportError:
        from continuation import binding_enabled
    if not binding_enabled(ctx, value["binding_id"]):
        raise ConflictError("required continuation binding is excluded by current configuration")
    current = getattr(ctx, "current_desktop_destination", None)
    probe = getattr(ctx, "desktop_continuation_readiness", None)
    if not callable(current) or not callable(probe):
        raise ConflictError("required continuation has no supported native Desktop consumer")
    destination = current()
    if not destination or probe(destination).get("ready") is not True:
        raise ConflictError("required continuation has no ready trusted Desktop destination")
    if ctx.get_config("continuation_enabled", False) is not True:
        raise ConflictError("required continuation observer is disabled")
    notification_target = continuation_notifications.resolve_requested_target(ctx, value.get("notify_telegram", False))
    latest = before["thread"].get("latestTurn")
    turn_id = latest.get("turnId") if isinstance(latest, dict) else None
    if value["expected_turn_id"] != turn_id:
        raise ConflictError("required continuation source turn changed")
    session = before["thread"].get("session") or {}
    if (isinstance(latest, dict) and latest.get("state") == "running") or session.get("status") in {"starting", "running"}:
        raise ConflictError("required continuation cannot register against a busy source")
    if transport.get_environment_descriptor()["environmentId"] != value["environment_id"]:
        raise ConflictError("required continuation source environment changed")
    values = dict(binding_id=value["binding_id"], profile_name=destination["profile_name"],
        t3_thread_id=thread_id, t3_owner_id=value["owner_id"], t3_environment_id=value["environment_id"],
        hermes_session_key=destination["session_id"], hermes_session_id=destination["session_id"],
        platform="desktop", user_id="", chat_id="", topic_id="",
        sunsama_task_id=value.get("sunsama_task_id", ""), source_identity=value["source_identity"],
        followup_scope=value["followup_scope"], max_continuations=value["max_continuations"])
    store = ContinuationStore(ctx.state.data_dir)
    store.initialize()
    baseline = (before["snapshotSequence"], utc_now())
    if "replaces" in value:
        binding = store.renew(value["replaces"], baseline=baseline,
                              desktop_upgrade=value.get("desktop_upgrade", False), **values)
    else:
        if value.get("desktop_upgrade"):
            raise InvalidInputError("desktop_upgrade requires an explicit predecessor")
        binding = store.bind(baseline=baseline, **values)
    continuation_notifications.register_target(store, binding, notification_target)
    store.reserve_source(binding, message_id,
        excluded_turn_ids=[turn_id, session.get("activeTurnId")])
    if binding.state != "active" or not binding.baseline_captured:
        raise ConflictError("required continuation binding is not armed")
    ticket = probe(destination)
    if ticket.get("ready") is not True or current() != destination:
        raise ConflictError("required continuation destination changed before dispatch")
    return {"binding_id": binding.binding_id, "source_message_id": message_id, "armed": True, "consumer_ready": True,
            "destination": destination, "notification_target": notification_target, "delivery_guarantee": "bounded; verify terminal receipt"}



def prepare_automatic_handoff(ctx, thread_id, before, transport, message_id):
    """Register one report for this send from the current authenticated host turn."""
    import uuid
    try:
        from .continuation import binding_enabled
    except ImportError:
        from continuation import binding_enabled
    if ctx.get_config("continuation_enabled", False) is not True:
        raise ConflictError("required continuation observer is disabled")
    destination = None
    for surface in ("desktop", "gateway"):
        current = getattr(ctx, f"current_{surface}_destination", None)
        probe = getattr(ctx, f"{surface}_continuation_readiness", None)
        if callable(current) and callable(probe):
            destination = current()
            if destination:
                break
    if not destination or probe(destination).get("ready") is not True:
        raise ConflictError("required continuation has no ready trusted native consumer")
    binding_id = "auto-" + uuid.uuid4().hex
    if not binding_enabled(ctx, binding_id):
        raise ConflictError("automatic continuation is excluded by current configuration")
    latest = before["thread"].get("latestTurn") or {}
    session = before["thread"].get("session") or {}
    if latest.get("state") == "running" or session.get("status") in {"starting", "running"}:
        raise ConflictError("required continuation cannot register against a busy source")
    environment_id = transport.get_environment_descriptor()["environmentId"]
    route = {"platform": "desktop", "user_id": "", "chat_id": "", "topic_id": ""} if surface == "desktop" else {key: destination[key] for key in ("platform", "user_id", "chat_id", "topic_id")}
    values = dict(binding_id=binding_id, profile_name=destination["profile_name"],
        t3_thread_id=thread_id, t3_owner_id=thread_id, t3_environment_id=environment_id,
        hermes_session_key=destination.get("session_key", destination["session_id"]),
        hermes_session_id=destination["session_id"], **route,
        sunsama_task_id="", source_identity="auto-" + uuid.uuid4().hex,
        followup_scope="Read only this T3 result and its artifacts; report the result once in this conversation. Do not edit source, product data, or Sunsama, or start further work.",
        max_continuations=1)
    store = ContinuationStore(ctx.state.data_dir)
    try:
        store.initialize()
    except ContinuationStateError:
        raise ConflictError("required continuation state is unavailable or incompatible; no source dispatch occurred") from None
    try:
        binding = store.bind_automatic(baseline=(before["snapshotSequence"], utc_now()),
            message_id=message_id, excluded_turn_ids=[latest.get("turnId"), session.get("activeTurnId")], **values)
    except ContinuationStateError:
        raise ConflictError("required continuation conflicts with existing paused, pending, or active mission authority; no source dispatch occurred") from None
    try:
        if probe(destination).get("ready") is not True or current() != destination:
            raise ConflictError("required continuation destination changed before dispatch")
    except BaseException:
        store.cancel_undispatched(binding.binding_id, message_id)
        raise
    return {"binding_id": binding.binding_id, "source_message_id": message_id, "armed": True,
        "consumer_ready": True, "destination": destination, "notification_target": None,
        "delivery_guarantee": "bounded; verify terminal receipt"}


def cancel_undispatched_handoff(ctx, handoff):
    """Retire only the fresh registration whose source command was never attempted."""
    store = ContinuationStore(ctx.state.data_dir)
    store.cancel_undispatched(handoff["binding_id"], handoff["source_message_id"])
    handoff.update(armed=False, consumer_ready=False)


def recheck_handoff(ctx, handoff):
    """Revalidate the pinned consumer immediately before the source HTTP request."""
    try:
        from .continuation import binding_enabled
    except ImportError:
        from continuation import binding_enabled
    try:
        store = ContinuationStore(ctx.state.data_dir)
        binding = store.get_binding(handoff["binding_id"])
        authorized = (ctx.get_config("continuation_enabled", False) is True
            and binding_enabled(ctx, binding.binding_id) and binding.state == "active"
            and store.reserved_source(binding) == handoff["source_message_id"])
        expected_destination = {"profile_name": binding.profile_name, "session_id": binding.hermes_session_id}
        if binding.platform != "desktop":
            expected_destination.update(session_key=binding.hermes_session_key, platform=binding.platform,
                user_id=binding.user_id, chat_id=binding.chat_id, topic_id=binding.topic_id)
        authorized = authorized and expected_destination == handoff["destination"]
    except ContinuationStateError:
        authorized = False
    if not authorized:
        raise ConflictError("required continuation authority was withdrawn or changed; source dispatch refused")
    destination = handoff["destination"]
    surface = "gateway" if "platform" in destination else "desktop"
    current = getattr(ctx, f"current_{surface}_destination", None)
    probe = getattr(ctx, f"{surface}_continuation_readiness", None)
    try:
        ready = callable(current) and callable(probe) and current() == destination and probe(destination).get("ready") is True
    except Exception:
        ready = False
    if not ready:
        raise ConflictError("required continuation destination is no longer ready; source dispatch refused")
