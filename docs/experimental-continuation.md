# Experimental same-session continuation

The eleven basic tools do not need this feature. The continuation observer is disabled by default and requires native Hermes gateway APIs that are absent from stock Hermes 0.20.4, 0.20.5, 0.21.0, and current main.

The public host patch is supported only against this exact clean upstream commit:

```text
63279301bcbdc185c1b07b98a9312eb0c862f26d
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
    'refs/tags/v1.3.1:refs/tags/v1.3.1'
  test "$(safe_git -C "$RELEASE_DIR/repo" cat-file -t refs/tags/v1.3.1)" = tag
  test "$(safe_git -C "$RELEASE_DIR/repo" for-each-ref --format='%(tag)' refs/tags/v1.3.1)" = v1.3.1
  TAG_VERIFY="$(safe_git -C "$RELEASE_DIR/repo" -c gpg.format=ssh \
    -c gpg.ssh.allowedSignersFile=/dev/null verify-tag --raw v1.3.1 2>&1 || true)"
  if ! printf '%s\n' "$TAG_VERIFY" | grep -Fqx 'Good "git" signature with ED25519 key SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4'; then
    printf '%s\n' 'Release tag signature did not match the pinned signer.' >&2
    exit 1
  fi
  RELEASE_REF="$(safe_git -C "$RELEASE_DIR/repo" rev-parse --verify 'v1.3.1^{commit}')"
  printf '%s\n' "$RELEASE_REF" | grep -Eq '^[0-9a-f]{40}$'
  safe_git -C "$RELEASE_DIR/repo" checkout -q --detach "$RELEASE_REF"
  safe_git -C "$RELEASE_DIR/repo" show \
    "$RELEASE_REF:release/v1.3.1.sha256" > "$RELEASE_DIR/authenticated.sha256"
  curl --fail --silent --show-error --location --proto '=https' \
    --proto-redir '=https' -o "$RELEASE_DIR/hermes-gateway-continuation-1.3.1.patch" \
    https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.3.1/hermes-gateway-continuation-1.3.1.patch
  curl --fail --silent --show-error --location --proto '=https' \
    --proto-redir '=https' -o "$RELEASE_DIR/hermes-gateway-continuation-1.3.1.patch.sha256" \
    https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.3.1/hermes-gateway-continuation-1.3.1.patch.sha256
  grep '  hermes-gateway-continuation-1.3.1.patch$' \
    "$RELEASE_DIR/authenticated.sha256" > "$RELEASE_DIR/expected.sha256"
  cmp -- "$RELEASE_DIR/expected.sha256" "$RELEASE_DIR/hermes-gateway-continuation-1.3.1.patch.sha256"
  (cd "$RELEASE_DIR" && sha256sum -c expected.sha256)
  safe_git -C "$RELEASE_DIR/repo" show \
    "$RELEASE_REF:patches/hermes-gateway-continuation-63279301.patch" \
    | cmp - "$RELEASE_DIR/hermes-gateway-continuation-1.3.1.patch"
  cp "$RELEASE_DIR/hermes-gateway-continuation-1.3.1.patch" ./hermes-gateway-continuation-1.3.1.patch
)
```

The final `cp` writes the authenticated patch to the current directory. Keep it private if your local diff or test output may contain operational metadata.

## Check, apply, and test

Use a clean clone of the official Hermes repository. These commands stop before any running Hermes process is changed.

```bash
git clone https://github.com/NousResearch/hermes-agent.git hermes-continuation
cd hermes-continuation
git checkout --detach 63279301bcbdc185c1b07b98a9312eb0c862f26d
test "$(git rev-parse HEAD)" = 63279301bcbdc185c1b07b98a9312eb0c862f26d
test -z "$(git status --porcelain)"
git apply --check ../hermes-gateway-continuation-1.3.1.patch
git apply ../hermes-gateway-continuation-1.3.1.patch
git diff --check
uv sync --frozen --python 3.11 --extra dev
uv run --frozen --extra dev pytest -q \
  tests/agent/test_codex_responses_adapter.py \
  tests/agent/test_turn_context.py \
  tests/gateway/test_active_turn_recovery.py \
  tests/gateway/test_conversation_scope_funnel.py \
  tests/gateway/test_plugin_message_injection.py \
  tests/gateway/test_restart_resume_pending.py \
  tests/hermes_cli/test_plugin_message_injection.py
```

Only after the exact base, clean-tree check, patch check, and affected tests pass should an operator apply the same authenticated patch to the exact clean checkout used by their Hermes installation. Stop and restart only that checkout's owning process. Do not copy individual files or apply the patch over another local modification.

The historical full Hermes suite for this host patch was not fully green: 44,866 tests passed, 402 were skipped, 40 clean-base/environment failures remained, and one unrelated run timed out. The focused affected tests are the release gate; they do not turn those baseline failures into passes.

## Create a binding

Install Hermes T3 Control first. Grant gateway injection only to this plugin, enable the observer for the default profile, and bind one exact T3 source to one exact existing Hermes session:

```bash
hermes t3-continuation bind \
  --binding-id MISSION_ID \
  --thread-id EXACT_T3_THREAD_ID \
  --owner-id EXACT_T3_OWNER_ID \
  --environment-id EXACT_T3_ENVIRONMENT_ID \
  --session-key EXACT_HERMES_SESSION_KEY \
  --session-id EXACT_HERMES_SESSION_ID \
  --platform PLATFORM --user-id USER_ID --chat-id CHAT_ID --topic-id TOPIC_ID \
  --sunsama-task-id EXACT_TASK_REFERENCE --source-identity TRUSTED_SOURCE_ID \
  --followup-scope none --max-continuations 1
hermes config set plugins.entries.hermes-t3-control.settings.continuation_enabled true
hermes config set plugins.entries.hermes-t3-control.allow_gateway_injection true
```

The Sunsama-named field is an opaque operator task reference. The plugin does not contact Sunsama, and the basic tools do not require an account. Command arguments and `hermes t3-continuation status` contain private route, session, and task metadata; do not publish shell history or status output.

Start or restart the owning gateway with the required native service and typed-injection APIs. A successful `bind` authenticates the exact source snapshot and durably captures its cursor before returning `baseline_captured: true` and `armed: true`, including when the cursor is zero. These fields confirm registration, not a running observer. Register before starting the authorized turn; binding an already running authorized turn also preserves its later completion if the observer starts after that completion. Events already in the registration snapshot are baseline history and do not wake Hermes. Confirm that the owning gateway actually started the observer before relying on delivery.

Use `pause`, `resume`, `stop`, `cancel`, and `ack` through the same operator command. A crash, timeout, or agent error after admission is never retried automatically. If an acknowledgement arrives before the final host receipt, its audit status stays acknowledged while the eventual success or error receipt is retained. Pre-admission `busy` and `stopping` receipts retry inside the same worker even when the source stream stays silent: delays are 1, 2, 4, 8, 16, then at most 30 seconds, with a persisted 64-attempt limit across restarts. Stop, pause, and cancel interrupt the backoff. Exhausting that retry budget is a terminal failure, not a completed continuation.

An exhausted binding reports `state: "exhausted"`, zero remaining continuations, and `armed: false`; it does not resubscribe after a restart. Explicitly stop that generation before renewing it with a new binding ID and every bind argument supplied again:

```bash
hermes t3-continuation stop OLD_MISSION_ID
hermes t3-continuation renew --replaces OLD_MISSION_ID \
  --binding-id NEW_MISSION_ID \
  --thread-id EXACT_T3_THREAD_ID \
  --owner-id EXACT_T3_OWNER_ID \
  --environment-id EXACT_T3_ENVIRONMENT_ID \
  --session-key EXACT_HERMES_SESSION_KEY \
  --session-id EXACT_HERMES_SESSION_ID \
  --platform PLATFORM --user-id USER_ID --chat-id CHAT_ID --topic-id TOPIC_ID \
  --sunsama-task-id NEW_TASK_REFERENCE --source-identity NEW_TRUSTED_SOURCE_ID \
  --followup-scope none --max-continuations 1
```

The predecessor must be explicitly stopped, exhausted, fully acknowledged, and backed by completed host receipts matching its signed authority. Active, paused, cancelled, in-flight, blocked, or uncertain predecessors are rejected. Source thread, owner, environment, and canonical Hermes route must match. The physical session ID may be a newly resolved current session on that same route; every destination field must be supplied explicitly and is revalidated by the host before admission. Task reference, source identity, follow-up scope, and budget are explicitly new. Renewal is atomic, preserves the old generation and lineage, captures a fresh registration baseline, and deduplicates old turns/events across generations. The superseded generation cannot resume. A running compatible supervisor notices the new generation without another restart.

Upgrading from older live code requires a coordinated reload or restart of the owning gateway; copying files does not upgrade an already running observer. Canonical public schema v1 and v2 ledgers migrate transactionally to v3, preserving authority, signed events, and lineage. A legacy positive cursor becomes a captured baseline; an ambiguous legacy zero remains unarmed until the worker captures its first snapshot. Experimental local ledgers that also call themselves v2 but have a different table shape are rejected without migration. Moving such state to this public plugin requires a separately reviewed and tested migration; do not edit the version or copy database rows manually. Downgrading a migrated v3 ledger to older continuation code is unsupported. Disable continuation before a stock basic-plugin rollback. These plugin changes do not add the required host APIs or expand the exact host-patch support described above.

## Roll back

Stop the owning Hermes process. From the same patched checkout, require the patch to reverse cleanly, reverse it, and confirm the exact upstream state:

```bash
git apply --reverse --check ../hermes-gateway-continuation-1.3.1.patch
git apply --reverse ../hermes-gateway-continuation-1.3.1.patch
git diff --check
test -z "$(git status --porcelain)"
test "$(git rev-parse HEAD)" = 63279301bcbdc185c1b07b98a9312eb0c862f26d
```

If the reverse check fails or the checkout has any third-party change, stop and restore through the installation's own reviewed backup or reinstall path. Restart only after the clean base is restored. Disabling `continuation_enabled` stops new observer work but does not remove the host patch.
