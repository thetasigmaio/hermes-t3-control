# Experimental same-session continuation

Read/control tools do not need this feature. An ordinary `t3_thread_send` defaults to required automatic completion; an intentional unwatched send must set `completion_policy: "none"`. The continuation observer is disabled by default and requires native Hermes gateway and Desktop APIs absent from the stock revisions listed in our compatibility matrix. Installing the basic plugin does not add these APIs.

The matching v1.5.0 experimental host patch adds the required automatic registration APIs. The upstream PR is not merged. Install the plugin and host patch from the same release. The host patch requires this exact clean upstream base:

```text
ad03f20dd61919ca2135d6904e787a94284aacaf
```

Do not apply it to another revision, a dirty checkout, or an installed Hermes tree containing local patches. There is no automatic patcher or fallback injection path.

## Authenticate the release asset

The detached checksum detects corruption but is not an authenticity proof by itself. This flow verifies the signed Hermes T3 Control release tag against the pinned signer, reads the checksum from that signed source, downloads the patch over HTTPS, and requires both the checksum file and patch bytes to match the signed tag.

```bash
(
  set -eu
  umask 077
  RELEASE_DIR="$(mktemp -d)"
  trap 'rm -rf -- "$RELEASE_DIR"' EXIT
  mkdir -m 700 "$RELEASE_DIR/home" "$RELEASE_DIR/xdg" "$RELEASE_DIR/repo"
  safe_git() {
    env -i PATH="$PATH" LC_ALL=C HOME="$RELEASE_DIR/home" \
      XDG_CONFIG_HOME="$RELEASE_DIR/xdg" GIT_CONFIG_NOSYSTEM=1 \
      GIT_CONFIG_GLOBAL=/dev/null git "$@"
  }
  safe_git -C "$RELEASE_DIR/repo" init -q
  safe_git -C "$RELEASE_DIR/repo" -c protocol.file.allow=never fetch -q \
    --no-tags https://github.com/thetasigmaio/hermes-t3-control.git \
    'refs/tags/v1.5.0:refs/tags/v1.5.0'
  test "$(safe_git -C "$RELEASE_DIR/repo" cat-file -t refs/tags/v1.5.0)" = tag
  test "$(safe_git -C "$RELEASE_DIR/repo" for-each-ref --format='%(tag)' refs/tags/v1.5.0)" = v1.5.0
  TAG_VERIFY="$(safe_git -C "$RELEASE_DIR/repo" -c gpg.format=ssh \
    -c gpg.ssh.allowedSignersFile=/dev/null verify-tag --raw v1.5.0 2>&1 || true)"
  if ! printf '%s\n' "$TAG_VERIFY" | grep -Fqx 'Good "git" signature with ED25519 key SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4'; then
    printf '%s\n' 'Release tag signature did not match the pinned signer.' >&2
    exit 1
  fi
  RELEASE_REF="$(safe_git -C "$RELEASE_DIR/repo" rev-parse --verify 'v1.5.0^{commit}')"
  printf '%s\n' "$RELEASE_REF" | grep -Eq '^[0-9a-f]{40}$'
  safe_git -C "$RELEASE_DIR/repo" checkout -q --detach "$RELEASE_REF"
  safe_git -C "$RELEASE_DIR/repo" show \
    "$RELEASE_REF:release/v1.5.0.sha256" > "$RELEASE_DIR/authenticated.sha256"
  curl --fail --silent --show-error --location --proto '=https' \
    --proto-redir '=https' -o "$RELEASE_DIR/hermes-gateway-continuation-1.5.0.patch" \
    https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.5.0/hermes-gateway-continuation-1.5.0.patch
  curl --fail --silent --show-error --location --proto '=https' \
    --proto-redir '=https' -o "$RELEASE_DIR/hermes-gateway-continuation-1.5.0.patch.sha256" \
    https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.5.0/hermes-gateway-continuation-1.5.0.patch.sha256
  grep '  hermes-gateway-continuation-1.5.0.patch$' \
    "$RELEASE_DIR/authenticated.sha256" > "$RELEASE_DIR/expected.sha256"
  cmp -- "$RELEASE_DIR/expected.sha256" "$RELEASE_DIR/hermes-gateway-continuation-1.5.0.patch.sha256"
  (cd "$RELEASE_DIR" && sha256sum -c expected.sha256)
  safe_git -C "$RELEASE_DIR/repo" show \
    "$RELEASE_REF:patches/hermes-gateway-continuation-ad03f20d.patch" \
    | cmp - "$RELEASE_DIR/hermes-gateway-continuation-1.5.0.patch"
  cp "$RELEASE_DIR/hermes-gateway-continuation-1.5.0.patch" ./hermes-gateway-continuation-1.5.0.patch
)
```

The final `cp` writes the authenticated patch to the current directory. Keep it private if your local diff or test output may contain operational metadata.

## Check, apply, and test

Use a clean clone of the official Hermes repository. These commands stop before any running Hermes process is changed.

```bash
git clone https://github.com/NousResearch/hermes-agent.git hermes-continuation
cd hermes-continuation
git checkout --detach ad03f20dd61919ca2135d6904e787a94284aacaf
test "$(git rev-parse HEAD)" = ad03f20dd61919ca2135d6904e787a94284aacaf
test -z "$(git status --porcelain)"
git apply --check ../hermes-gateway-continuation-1.5.0.patch
git apply ../hermes-gateway-continuation-1.5.0.patch
git diff --check
uv sync --frozen --python 3.11 --extra dev --extra messaging
uv run --frozen --extra dev --extra messaging bash scripts/run_tests.sh -j 2 \
  tests/agent/test_typed_event_turn_boundary.py \
  tests/gateway/test_plugin_message_injection.py \
  tests/hermes_cli/test_plugin_message_injection.py \
  tests/hermes_cli/test_plugin_notifications.py \
  tests/tui_gateway/test_plugin_events.py
```

Only after the exact base, clean-tree check, patch check, and affected tests pass should an operator apply the same authenticated patch to the exact clean checkout used by their Hermes installation. Stop and restart only that checkout's owning process. Do not copy individual files or apply the patch over another local modification.

The locked `messaging` extra supplies the Telegram SDK required for notices and
the notification contract tests. It is not installed by the basic plugin installer.

The patch contains the gateway lifecycle, typed event admission, exact-session
Desktop consumer, and notification adapter together. The release CI reconstructs
this clean base and runs the affected host tests. These checks do not claim a
fully green Hermes baseline: the earlier wider local run retained an unrelated
WSL voice-test failure. No upstream acceptance is claimed.

## Enable and check readiness

Continuation is opt-in and supports only the default profile. The operator must
explicitly enable the worker and grant this plugin typed event injection:

```bash
hermes config set plugins.entries.hermes-t3-control.settings.continuation_enabled true
hermes config set plugins.entries.hermes-t3-control.allow_gateway_injection true
```

Coordinate a reload of the processes that actually own the gateway and Desktop
serve after applying the patch. Never restart an active agent turn or its children.
Reconnect Desktop normally to the original physical conversation. Readiness
requires that authenticated connection, its exact physical session, a live
consumer, and the injection grant. A shared database or matching topic is not
proof of continuity. Missing readiness rejects a required handoff before dispatch.

## Automatic return to the current conversation

With the matching patched host and observer ready, an ordinary send needs only its task:

```json
{"thread_id": "EXACT_THREAD", "message": "Perform the authorized task", "busy_policy": "queue"}
```

The host resolves the current authenticated Desktop or gateway conversation,
including a Telegram-origin conversation reopened in Desktop. Registration pins
its exact physical session and route, the authenticated T3 environment, and the
new source message before dispatch. No token, URL, binding ID, ledger, or callback
argument is required. A missing or unsupported consumer fails before dispatch.
`completion_policy: "required"` may be stated explicitly; `none` deliberately
requests an unwatched send and reports `armed: false`.

Automatic scope permits one result/artifact read and one report in that same
conversation. It authorizes no source/product/Sunsama writes or further work.
The finite budget and source correlation are enforced by the plugin; the scope
is an instruction to the receiving agent, not a filesystem sandbox. The optional
`continuation` object remains for explicitly authorized extended Desktop missions;
it is not part of the ordinary send workflow.

Registration and `consumer_ready` prove pre-dispatch readiness, not delivery.
Native contract tests exercise real PluginContext and worker context propagation;
live Desktop and Telegram acceptance still requires coordinated activation and an
authenticated client. Never treat an ambiguous source dispatch or receipt as
permission to resend the task.

For operator registration before a new source turn, use `bind` with an explicit
future `--source-message-id`; that exact message must then be dispatched. To attach
an already authorized, currently running turn, replace that flag with
`--attach-current-turn EXACT_RUNNING_TURN_ID`. Completed turns cannot be attached
or replayed. For example:

```bash
hermes t3-continuation bind \
  --binding-id NEW_MISSION_ID \
  --thread-id EXACT_THREAD --owner-id EXACT_OWNER \
  --environment-id EXACT_ENVIRONMENT \
  --platform desktop --session-id EXACT_PHYSICAL_SESSION \
  --session-key EXACT_PHYSICAL_SESSION --user-id '' --chat-id '' --topic-id '' \
  --source-identity OPERATOR_AUTHORITY \
  --followup-scope 'Read the result and report once; no further writes.' \
  --max-continuations 1 --attach-current-turn EXACT_RUNNING_TURN_ID \
  --desktop-ws-url ws://127.0.0.1:9119/api/ws
```

The port above is an example: use the actual authenticated serve endpoint. The
operator readiness probe takes its existing boot credential from
`HERMES_DASHBOARD_SESSION_TOKEN` in the private process environment. Obtain it only
through the installation's trusted operator path; never put it in arguments,
config examples, logs, or shared status output. If that credential or the live
connection is unavailable, stop at missing readiness. There is no unauthenticated
fallback, guessed session resume, or fabricated user prompt.

`--sunsama-task-id` is **optional**. Omit it when no legitimate ledger is bound.
The plugin does not contact Sunsama or create a task. A supplied reference grants
no write authority by itself: the explicit scope must authorize any ledger write.

`--notify-telegram` (or `notify_telegram: true`) requests an additional notice to
the current configured Telegram home, snapshotted in the signed binding. It is
not Desktop/Telegram transcript synchronization. The notice is queued only after
PM verification, one acknowledgement, and a completed host receipt. Delivery is
serialized and deduplicated; ambiguous sends are marked uncertain rather than
replayed. Configure the real Telegram home through Hermes's normal setup first.

Read back `hermes t3-continuation status NEW_MISSION_ID` before relying on the
watcher. A captured baseline and `armed: true` prove registration, not completed
delivery. Desktop registration also checks the live consumer. The PM must exact-
read the bound source, verify artifacts and gates, recheck authority, and then
acknowledge once. Completion of one turn never means completion of a product.

## Renewal, pause, and recovery

Automatic sends create fresh scope and identity without renewing old authority. Terminal historical rows remain unchanged, including uncertain receipts. Paused, queued, dispatching, foreign-session, or unfinished active missions refuse automatic registration. Only a fully acknowledged active generation with completed receipts and exhausted budget can be retired. A failed second readiness check cancels its own undispatched reservation.

Explicit extended missions require fresh scope and a fresh binding ID. Exhausted bindings
remain exhausted across restart. Explicitly stop the acknowledged predecessor,
then use `renew --replaces OLD_MISSION_ID` with every destination/scope argument
supplied again. The Sunsama field may still be omitted. The predecessor must have
spent its finite budget with signed acknowledgements and completed host receipts.
Cancelled, uncertain, paused, and unverified predecessors cannot be renewed.

For an intentional gateway-to-Desktop move, `--desktop-upgrade` requires the same
physical session. A separately authorized change of physical conversation during
current-turn renewal additionally requires `--replace-session-id EXACT_OLD_SESSION`.
This is explicit new authority, never automatic inheritance from a topic label.
Old audit rows, source events, and generation lineage are retained; old events are
not replayed. Public schema v1/v2/v3 ledgers migrate transactionally to v4,
preserving empty captured baselines, authority, signed receipts, and lineage.
The one audited local Desktop v2 layout also migrates transactionally: its complete DDL and metadata signature must match, the original HMAC key and all signed rows must verify, and duplicate live generations are rejected. Existing empty baselines, envelopes, MACs, receipts, and notification state are preserved. Unknown experimental layouts remain rejected.
Do not copy rows, edit schema metadata, or downgrade a v4 ledger to older code.

The authenticated T3 stream uses durable sequence cursors. Restart recovers
queued pre-admission work. Busy/stopping admissions retry with persisted bounded
backoff; an accepted turn, crash, or ambiguous final receipt is never blindly
repeated. `pause`, `stop`, and `cancel` withdraw the relevant authority. Explicit
`continuation_excluded_bindings` settings can retire known generations without
rewriting their historical audit. Never activate all old bindings on upgrade.

An oversized parent context fails closed with a failed typed receipt. It does not
produce a false completion; automatic compaction during typed events remains
unsupported. Resolve context pressure through Hermes's supported conversation
controls before authorizing a new attempt.

## Roll back

Before first v1.5.0 initialization, stop both consumers and take a consistent private backup of the ledger database and its original HMAC key. Code rollback alone cannot undo schema migration. Restoring that old ledger backup is valid only before any new dispatch or receipt has been accepted; afterward preserve the newer audit and use a forward fix or compatible recovery. Never restore an old snapshot over newly accepted work.

Stop the owning Hermes process. From the same patched checkout, require the patch to reverse cleanly, reverse it, and confirm the exact upstream state:

```bash
git apply --reverse --check ../hermes-gateway-continuation-1.5.0.patch
git apply --reverse ../hermes-gateway-continuation-1.5.0.patch
git diff --check
test -z "$(git status --porcelain)"
test "$(git rev-parse HEAD)" = ad03f20dd61919ca2135d6904e787a94284aacaf
```

If the reverse check fails or the checkout has any third-party change, stop and restore through the installation's own reviewed backup or reinstall path. Restart only after the clean base is restored. Disabling `continuation_enabled` stops new observer work but does not remove the host patch.
