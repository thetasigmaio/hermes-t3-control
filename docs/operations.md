# Operations and recovery

## Fresh-process activation

Plugin tools are registered when Hermes constructs its catalog. For a managed gateway:

```bash
hermes gateway restart
hermes gateway status
```

For another Hermes owner, inspect it first—for example `hermes serve --status`—then fully stop and relaunch that same backend through its supported lifecycle. A fresh process or session is required; reconnecting to the old process is insufficient.

Run the literal first non-thread-mutating check after restart. Default local authentication still creates and revokes one bounded in-memory session:

> Call only `t3_threads` with `{}`; do not call mutation tools.

## Allowlisted same-session continuation

Continuation is an experimental, default-profile opt-in. It is unavailable on unmodified stock Hermes and requires the exact clean-base manual host patch. The eleven basic tools remain independent.

Follow [Experimental continuation](experimental-continuation.md) for authenticated patch download, exact-base checks, affected tests, binding, renewal, restart, and rollback. The Sunsama-named field is only an opaque operator task reference; the plugin does not contact Sunsama. Binding commands and local status output contain private route/session metadata and must not be published.

## Troubleshooting

- **No tools:** run `hermes plugins show hermes-t3-control` and `hermes plugins doctor hermes-t3-control --ci`, then restart the actual owning process.
- **No T3 connection:** start the matching local T3 environment. Default auth intentionally does not discover remote or hostname-based endpoints.
- **Zero/multiple selector matches:** narrow project, workspace, and title filters; do not guess a UUID.
- **Busy target:** `reject` is non-mutating. Use `queue` only when start-or-queue is acceptable.
- **Busy model switch:** `model_switch_busy` made zero POSTs. Call `t3_turn_interrupt`, wait with `t3_thread_wait` until the thread is `ready`, then retry the override. Never use `t3_session_stop` as switch recovery.
- **Pending approval/input:** exact-read the request and current turn, then answer that exact pair.
- **Unsupported model switch:** stopped, interrupted, or unrestorable error-state threads fail non-retryably. A missing target, different driver, or incompatible continuation is authoritative only after T3 projects that turn-start failure; preflight cannot prove it.
- **Provider limit:** `provider_limit_exhausted` is a sanitized, command-correlated projection of a quota or usage-limit failure. Exact-read the failed thread before choosing another operator-approved override or waiting for quota recovery. There is no honest remaining-quota preflight and no raw provider error text in the receipt.
- **Settlement conflict:** wait for starting/running work and any recent queued turn to finish, or answer the pending approval/input, then exact-read before retrying `t3_thread_settle`. An already-settled thread succeeds.
- **Provider limitation:** check [Compatibility evidence](compatibility.md); a generic model selection is not proof of support, and the allowed orchestration HTTP surface exposes no provider catalog.

## Accepted but not yet projected

`accepted_pending_projection` is not a failure. It includes command/message identity and a bounded raw-read reconciliation recipe with `required_snapshot_sequence` and, for turns, `expected_message_id`. Repeat that exact read until the sequence catches up. Do not send a new command: that can create duplicate work.

`mutation_ambiguous` means the transport failed before acceptance could be established; exact-read before considering a byte-identical same-command retry. `network_error` is a read failure. `conflict` means the target is busy/stale/archived/ambiguous or not in the required state. `auth_cleanup: failed` means the accepted operation remains authoritative while session revocation could not be confirmed.

## Rehome an incompatible continuation

Rehome is an explicit operator workflow, never an automatic fallback from `t3_thread_send`. After the operator approves the target and summary, create a **new Plan-mode** thread on that target with `t3_thread_create`. The initial message may contain only the approved summary of the goal, acceptance criteria, and original thread ID.

Never auto-copy conversation history, tokens, environment or credential paths, logs or raw events, branch, or worktree. Rehome does not further mutate, archive, or delete the original thread; any metadata or turn command already accepted during the failed switch remains authoritative and must be reconciled. Do not dispatch through SQLite, JSONL/event logs, provider-event internals, or another raw/provider-specific path.

## Update

Verify the release tag against the pinned signer, confirm the active profile, then use the same disabled-install boundary:

```bash
(
  set -eu
  umask 077
  hermes config path
  read -r -p 'Update this Hermes profile? [y/N] ' CONFIRM
  case "$CONFIRM" in y|Y) ;; *) exit 1 ;; esac
  VERIFY_DIR="$(mktemp -d)"
  trap 'rm -rf -- "$VERIFY_DIR"' EXIT
  mkdir -m 700 "$VERIFY_DIR/home" "$VERIFY_DIR/xdg" "$VERIFY_DIR/repo"
  safe_git() {
    env -i PATH="$PATH" LC_ALL=C HOME="$VERIFY_DIR/home" XDG_CONFIG_HOME="$VERIFY_DIR/xdg" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git "$@"
  }
  safe_git -C "$VERIFY_DIR/repo" init -q
  safe_git -C "$VERIFY_DIR/repo" -c protocol.file.allow=never fetch -q --no-tags https://github.com/thetasigmaio/hermes-t3-control.git 'refs/tags/v1.4.0:refs/tags/v1.4.0'
  test "$(safe_git -C "$VERIFY_DIR/repo" cat-file -t refs/tags/v1.4.0)" = tag
  test "$(safe_git -C "$VERIFY_DIR/repo" for-each-ref --format='%(tag)' refs/tags/v1.4.0)" = v1.4.0
  TAG_VERIFY="$(safe_git -C "$VERIFY_DIR/repo" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=/dev/null verify-tag --raw v1.4.0 2>&1 || true)"
  if ! printf '%s\n' "$TAG_VERIFY" | grep -Fqx 'Good "git" signature with ED25519 key SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4'; then
    printf '%s\n' 'Release tag signature did not match the pinned signer.' >&2
    exit 1
  fi
  HERMES_T3_CONTROL_REF="$(safe_git -C "$VERIFY_DIR/repo" rev-parse --verify 'v1.4.0^{commit}')"
  printf '%s\n' "$HERMES_T3_CONTROL_REF" | grep -Eq '^[0-9a-f]{40}$'
  hermes plugins disable hermes-t3-control
  hermes plugins remove hermes-t3-control
  hermes plugins install thetasigmaio/hermes-t3-control --ref "$HERMES_T3_CONTROL_REF" --no-enable
  hermes plugins doctor hermes-t3-control --ci
  hermes plugins enable hermes-t3-control --no-allow-tool-override
)
```

Restart the owning Hermes process and repeat the first safe check.

## Rollback

Use the same disable/remove/install/Doctor/enable sequence with a previously audited compatible commit. Public v1.2.0 is commit `8f42cb301fa065465e7d50e04e99577c328717f0`. v1.1.0 is not installable on Hermes 0.20.4/0.20.5 because its manifest version exceeds their supported maximum.

## Uninstall

```bash
hermes plugins disable hermes-t3-control
hermes plugins remove hermes-t3-control
hermes config unset plugins.entries.hermes-t3-control
```

`remove` is the primary Hermes 0.20.4/0.20.5 command; `uninstall` is an alias.

## Published asset verification

The downloaded checksum alone is not authenticity proof. This private, HTTPS-only flow verifies the pinned tag signer, takes the expected digest from the signed source tree, checks the download, rebuilds from signed source, and requires byte identity:

```bash
(
  set -eu
  umask 077
  RELEASE_DIR="$(mktemp -d)"
  trap 'rm -rf -- "$RELEASE_DIR"' EXIT
  mkdir -m 700 "$RELEASE_DIR/home" "$RELEASE_DIR/xdg" "$RELEASE_DIR/repo" "$RELEASE_DIR/download"
  safe_git() {
    env -i PATH="$PATH" LC_ALL=C HOME="$RELEASE_DIR/home" XDG_CONFIG_HOME="$RELEASE_DIR/xdg" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git "$@"
  }
  safe_git -C "$RELEASE_DIR/repo" init -q
  safe_git -C "$RELEASE_DIR/repo" -c protocol.file.allow=never fetch -q --no-tags https://github.com/thetasigmaio/hermes-t3-control.git 'refs/tags/v1.4.0:refs/tags/v1.4.0'
  test "$(safe_git -C "$RELEASE_DIR/repo" cat-file -t refs/tags/v1.4.0)" = tag
  test "$(safe_git -C "$RELEASE_DIR/repo" for-each-ref --format='%(tag)' refs/tags/v1.4.0)" = v1.4.0
  TAG_VERIFY="$(safe_git -C "$RELEASE_DIR/repo" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=/dev/null verify-tag --raw v1.4.0 2>&1 || true)"
  if ! printf '%s\n' "$TAG_VERIFY" | grep -Fqx 'Good "git" signature with ED25519 key SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4'; then
    printf '%s\n' 'Release tag signature did not match the pinned signer.' >&2
    exit 1
  fi
  RELEASE_REF="$(safe_git -C "$RELEASE_DIR/repo" rev-parse --verify 'v1.4.0^{commit}')"
  printf '%s\n' "$RELEASE_REF" | grep -Eq '^[0-9a-f]{40}$'
  safe_git -C "$RELEASE_DIR/repo" checkout -q --detach "$RELEASE_REF"
  safe_git -C "$RELEASE_DIR/repo" show "$RELEASE_REF:release/v1.4.0.sha256" > "$RELEASE_DIR/authenticated.sha256"
  curl --fail --show-error --location --proto '=https' --proto-redir '=https' --output "$RELEASE_DIR/download/hermes-t3-control-1.4.0.tar.gz" https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.4.0/hermes-t3-control-1.4.0.tar.gz
  curl --fail --show-error --location --proto '=https' --proto-redir '=https' --output "$RELEASE_DIR/download/hermes-t3-control-1.4.0.tar.gz.sha256" https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.4.0/hermes-t3-control-1.4.0.tar.gz.sha256
  curl --fail --show-error --location --proto '=https' --proto-redir '=https' --output "$RELEASE_DIR/download/install-t3.sh" https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.4.0/install-t3.sh
  curl --fail --show-error --location --proto '=https' --proto-redir '=https' --output "$RELEASE_DIR/download/install-t3.sh.sha256" https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.4.0/install-t3.sh.sha256
  grep '  hermes-t3-control-1.4.0.tar.gz$' "$RELEASE_DIR/authenticated.sha256" > "$RELEASE_DIR/expected.sha256"
  cmp -- "$RELEASE_DIR/expected.sha256" "$RELEASE_DIR/download/hermes-t3-control-1.4.0.tar.gz.sha256"
  (cd "$RELEASE_DIR/download" && python3 -B "$RELEASE_DIR/repo/scripts/verify_release.py" hermes-t3-control-1.4.0.tar.gz.sha256)
  grep '  install-t3.sh$' "$RELEASE_DIR/authenticated.sha256" > "$RELEASE_DIR/expected-installer.sha256"
  cmp -- "$RELEASE_DIR/expected-installer.sha256" "$RELEASE_DIR/download/install-t3.sh.sha256"
  (cd "$RELEASE_DIR/download" && python3 -B "$RELEASE_DIR/repo/scripts/verify_release.py" install-t3.sh.sha256)
  safe_git -C "$RELEASE_DIR/repo" show "$RELEASE_REF:scripts/install-signed.sh" | cmp - "$RELEASE_DIR/download/install-t3.sh"
  python3 -B "$RELEASE_DIR/repo/scripts/build_release.py" --output-dir "$RELEASE_DIR/reproduced"
  cmp -- "$RELEASE_DIR/download/hermes-t3-control-1.4.0.tar.gz" "$RELEASE_DIR/reproduced/hermes-t3-control-1.4.0.tar.gz"
  bash "$RELEASE_DIR/download/install-t3.sh"
)
```

## Development verification

Run the dependency-free suite and local release gates from a clean checkout:

```bash
PYTHONWARNINGS=error python3.11 -B -m unittest discover -s tests -v
env PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor . --ci
python3 -B scripts/build_release.py --output-dir dist
python3 -B scripts/verify_release.py dist/hermes-t3-control-1.4.0.tar.gz.sha256
python3 -B scripts/verify_release.py dist/hermes-gateway-continuation-1.4.0.patch.sha256
python3 -B scripts/verify_release.py dist/install-t3.sh.sha256
```

Supported-install CI uses uv 0.12.0 and `uv sync --frozen` against these exact Hermes commits:

- 0.20.4: `e624e9fde561e1add9388384012b295fde669ade`
- 0.20.5: `fcbd1076a93841fa88855acce810e342a5b78101`
- 0.21.0: `29112bef099274229cadff79cdff7bf7b99c4b77`

The fresh-process inspector permits only its expected loopback TCP connection and asserts that the plugin is enabled, manifest v1, version 1.4.0, and exactly eleven tools are registered, discoverable, and callable.
