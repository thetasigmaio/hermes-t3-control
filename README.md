# Hermes T3 Control 1.2.1

Control T3 work from Hermes without copying tokens or hunting for thread UUIDs. The plugin lets an agent find the right thread, see its latest result, running status, pending questions, and stored plan, continue it safely, and wait for the outcome. The core thread lifecycle is verified end-to-end with Codex.

The simple mental model is:

1. Find with `t3_threads`.
2. Inspect with `t3_thread_read`.
3. Continue or control that exact thread.
4. Monitor with `t3_thread_wait`.

Ten focused tools cover discovery, creation, continuation, modes, Plan to Build, interrupt, stop, wait, and typed responses. Outputs stay bounded, mutations fail closed on ambiguous selectors, and no mutation creates a replacement thread implicitly.

## Compatibility

Provider support is based on real completion evidence, not just a generic-looking schema.

| Provider | Status | Evidence |
|---|---|---|
| Codex | Supported | Core lifecycle passed disposable live create, read, send, wait, Plan to Build, approval, interrupt, stop, and resume on `codex_20x`; typed user input is contract-tested but was not synthesizable live. |
| OpenCode | Not yet supported | The available instance was disabled and not installed; no authenticated disposable completion gate was possible. |
| Other T3 providers | Not yet supported | Common reads and dispatch are provider-neutral, but provider-specific lifecycle behavior has not passed live acceptance. |

| OS/runtime | Status | Evidence |
|---|---|---|
| Linux | Supported with limits | Python and supported-install CI run on Ubuntu; local authentication needs `/proc` and `pidfd`. |
| WSL2 | Supported | Full published-release and active-profile live E2E. |
| Native Windows | Not supported yet | Linux-only local authentication; external-token mode is not native-Windows E2E verified. |
| macOS | Not supported yet | Linux-only local authentication; external-token mode is not macOS E2E verified. |

See [Compatibility evidence](docs/compatibility.md) for the exact provider boundaries and missing acceptance gates.

## Quick start

Prerequisites: T3 is running, Hermes 0.20.4 or 0.20.5 is available, and Git supports SSH signature verification. The block prints the active Hermes profile for confirmation, verifies the v1.2.1 tag against the pinned release-signing key, installs that exact commit disabled with the scanner on, runs Doctor, and enables without tool override.

```bash
(
  set -eu
  umask 077
  hermes config path
  read -r -p 'Install into this Hermes profile? [y/N] ' CONFIRM
  case "$CONFIRM" in y|Y) ;; *) exit 1 ;; esac
  VERIFY_DIR="$(mktemp -d)"
  trap 'rm -rf -- "$VERIFY_DIR"' EXIT
  mkdir -m 700 "$VERIFY_DIR/home" "$VERIFY_DIR/xdg" "$VERIFY_DIR/repo"
  safe_git() {
    env -i PATH="$PATH" LC_ALL=C HOME="$VERIFY_DIR/home" XDG_CONFIG_HOME="$VERIFY_DIR/xdg" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git "$@"
  }
  safe_git -C "$VERIFY_DIR/repo" init -q
  safe_git -C "$VERIFY_DIR/repo" -c protocol.file.allow=never fetch -q --no-tags https://github.com/thetasigmaio/hermes-t3-control.git 'refs/tags/v1.2.1:refs/tags/v1.2.1'
  TAG_VERIFY="$(safe_git -C "$VERIFY_DIR/repo" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=/dev/null verify-tag --raw v1.2.1 2>&1 || true)"
  if ! printf '%s\n' "$TAG_VERIFY" | grep -Fqx 'Good "git" signature with ED25519 key SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4'; then
    printf '%s\n' 'Release tag signature did not match the pinned signer.' >&2
    exit 1
  fi
  HERMES_T3_CONTROL_REF="$(safe_git -C "$VERIFY_DIR/repo" rev-parse --verify 'v1.2.1^{}')"
  printf '%s\n' "$HERMES_T3_CONTROL_REF" | grep -Eq '^[0-9a-f]{40}$'
  hermes config set plugins.scan_on_install true
  hermes plugins install thetasigmaio/hermes-t3-control --ref "$HERMES_T3_CONTROL_REF" --no-enable
  hermes plugins doctor hermes-t3-control --ci
  hermes plugins enable hermes-t3-control --no-allow-tool-override
)
```

Doctor runs while disabled and must report `registrations: 10 tool(s), 0 hook(s)`. This path does not use `--force`.

Restart only the process that owns your Hermes session. For a managed messaging gateway:

```bash
hermes gateway restart
hermes gateway status
```

For Hermes Desktop or `hermes serve`, inspect the owner first:

```bash
hermes serve --status
```

Then fully stop and relaunch that same Desktop/backend through its supported lifecycle; do not restart an unrelated messaging gateway. Ordinary Linux/WSL2 use needs no plugin settings. When no profile-scoped T3 token is configured, automatic local authentication discovers the matching live T3 CLI, leases an in-memory session for one operation, and revokes it. A valid profile token selects explicit external-token behavior; invalid token configuration fails closed. New threads default to `approval-required`.

## First safe check

Start a fresh Hermes process or session, then use this literal non-thread-mutating operator prompt:

> Call only `t3_threads` with `{}`; do not call mutation tools.

You should receive a bounded project/thread summary. If the tools are absent, the owning Hermes process has not rebuilt its catalog; see [Troubleshooting](docs/operations.md#troubleshooting).

## Everyday workflow

Find exactly one thread by a human selector:

`t3_threads {"project":"demo-app","title_query":"release check","require_one":true,"limit":10}`

Read its current material state:

`t3_thread_read {"thread_id":"id-from-t3_threads","view":"material","turn_limit":20}`

Continue the same thread only when immediate start or queuing is acceptable:

`t3_thread_send {"thread_id":"exact-thread-id","message":"Continue the assigned goal and report material progress.","busy_policy":"queue"}`

Then wait without dumping a full snapshot:

`t3_thread_wait {"thread_id":"exact-thread-id","until":"terminal","timeout_seconds":30}`

Zero or multiple matches require a narrower selector. A send never creates a replacement thread. If a mutation returns `accepted_pending_projection`, do not send it again; read back the exact command/message identity as described in [Operations and recovery](docs/operations.md#accepted-but-not-yet-projected).

`full-access` lets the selected provider execute commands and modify or delete files without approval. Use it only for a trusted provider and checkout.

For creation, Plan to Build, approvals/user input, mode changes, and all ten tools, see the [Tool reference](docs/tools.md).

## More detail

- [Tool reference](docs/tools.md) — all ten tools, limits, and workflows.
- [Compatibility evidence](docs/compatibility.md) — provider and OS claims plus acceptance gates.
- [Security model](docs/security.md) — credential lifecycle, transport bounds, and race boundaries.
- [Operations and recovery](docs/operations.md) — restart, update, rollback, troubleshooting, and release verification.
- [Community index status](docs/community-index.md) — prepared metadata and the current upstream blocker.

Compatibility baseline: Hermes 0.20.4/0.20.5, Python 3.11-3.13, manifest version 1, and T3 server contract `0.0.34-nightly.20260820.1141`. Released under the [MIT License](LICENSE).
