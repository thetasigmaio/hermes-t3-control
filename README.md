# Hermes T3 Control 1.2.0

Hermes T3 Control is a synchronous native directory plugin that lets a Hermes agent discover, inspect, continue, and monitor T3-owned Codex threads through T3's authenticated orchestration API.

The mental model is ten tools in four steps: find a thread with `t3_threads`, inspect it with `t3_thread_read`, continue or control that exact thread, then monitor it with `t3_thread_wait`. Ordinary local use needs no copied token, port, or UUID. Every mutation remains exact-thread scoped and never silently chooses a fuzzy match.

Compatibility: Hermes 0.20.4 and 0.20.5, Python 3.11-3.13, and T3 server contract `0.0.34-nightly.20260820.1141`. Manifest version 1 is deliberate because it is the newest manifest accepted by both supported Hermes installers. Released under the [MIT License](LICENSE).

### v1.2 migration

v1.2 intentionally replaces model-hostile legacy defaults with bounded agent views. A caller that still needs the v1.0/v1.1 shell result must request `t3_threads {"view":"raw"}`; a caller that needs the legacy exact detail must request `t3_thread_read {"thread_id":"...","view":"raw"}`. The tool names and legacy raw payloads remain available, but omitted `view` now means compact/material. Sending also becomes fail-closed: default `reject` is observation-only, while an explicit `queue` acknowledges that T3 may start or queue the exact message.

## Quick setup

Work in the intended Hermes profile and keep the default scanner enabled:

```bash
hermes config path
hermes config env-path
hermes config set plugins.scan_on_install true
hermes config get plugins.scan_on_install --json
```

The last command must print `true`. Install one audited, immutable v1.2.0 commit while the plugin is disabled:

```bash
read -r -p 'Audited 40-character commit SHA: ' HERMES_T3_CONTROL_REF
hermes plugins install thetasigmaio/hermes-t3-control --ref "$HERMES_T3_CONTROL_REF" --no-enable
```

This supported path does not need `--force`. Configure local CLI authentication and the conservative creation default:

```bash
hermes config set plugins.entries.hermes-t3-control.settings.auth_mode local-cli
hermes config set plugins.entries.hermes-t3-control.settings.default_runtime_mode approval-required
hermes config get plugins.entries.hermes-t3-control.settings --json
```

T3 must already be running for tool calls. The plugin discovers that same-user live T3 environment and matching upstream CLI, creates a five-minute in-memory session for one bounded operation, and revokes it in `finally`.

Validate before activation, then enable without permission to replace built-in tools:

```bash
hermes plugins doctor hermes-t3-control --ci
hermes plugins enable hermes-t3-control --no-allow-tool-override
hermes plugins show hermes-t3-control
```

Doctor runs while the plugin is still disabled and must report `registrations: 10 tool(s), 0 hook(s)`.

Restart the process that constructs the Hermes tool catalog. For an installed messaging gateway:

```bash
hermes gateway restart
hermes gateway status
```

For a Desktop/T3 backend, first identify the supported owner and lifecycle:

```bash
hermes serve --status
```

Fully stop and relaunch that backend through the same owner or process manager. A fresh Hermes process or session is required; reconnecting to the same process is insufficient.

Use this literal non-thread-mutating first check operator prompt. Local authentication still issues and revokes its bounded administrative session:

> Call only `t3_threads` with `{}`; do not call mutation tools.

## Find and read the right thread

`t3_threads` returns compact project summaries—including projects with no threads—and compact thread summaries by default. Filters are optional and combined. A human selector such as project `solarsim` plus a title query can require exactly one thread result; zero or multiple matches fail closed, and the plugin never silently chooses by fuzzy title.

Example for `t3_threads`:

```json
{
  "project": "solarsim",
  "title_query": "release smoke",
  "require_one": true,
  "limit": 10
}
```

Use the returned ID for an exact material read. Histories stay bounded and paginated.

Example for `t3_thread_read`:

```json
{
  "thread_id": "thread-id-from-t3_threads",
  "view": "material",
  "turn_limit": 20
}
```

The material summary includes project/workspace, title, model and modes, lifecycle, canonical provider liveness and background liveness, latest turn/session, pending approval or user-input requests, last error, latest user and assistant updates, canonical plan progress, timestamps, and sequence cursors. Non-null T3 background liveness (`working` or `monitoring`) remains running and cannot satisfy a terminal wait; a cleared canonical `planProgress` stays cleared. An explicit `raw` view preserves the bounded legacy projection when diagnostics require it.

## Continue and monitor one exact thread

Sending continues the same stored thread. It preserves the stored model selection, model options, runtime mode, interaction mode, project, branch, and worktree, and it never creates a replacement thread. `busy_policy` defaults to `reject`, which is an observation-only check and never dispatches: pinned T3 has no atomic idle guard, so a pre-read cannot promise that an idle thread stays idle. An explicit `queue` acknowledges that T3 may start or queue the exact message and is required for every actual send.

Example for `t3_thread_send`:

```json
{
  "thread_id": "exact-thread-id",
  "message": "Continue the assigned goal and report only material progress.",
  "busy_policy": "queue"
}
```

Observed command states distinguish `started`, `queued`, `completed`, `blocked`, `error`, and `accepted_pending_projection`. A `reject` result is unambiguous and performs no dispatch even after an idle observation. A full-access send repeats the file-modification/deletion warning at the point of action.

Wait without dumping snapshots. `timeout_seconds` 0-30 is bounded; `after_thread_sequence` makes progress checks incremental. Provider liveness and work progress are separate fields.

Example for `t3_thread_wait`:

```json
{
  "thread_id": "exact-thread-id",
  "after_thread_sequence": 42,
  "until": "terminal",
  "timeout_seconds": 30
}
```

The result reports progress, action required, settlement, or timeout with a compact material delta and one non-duplicated latest assistant update; it never embeds the full thread snapshot. A timeout says nothing was observed in the bounded window; it does not declare provider failure.

Pending actions are typed and scoped by exact request ID and expected turn ID. Supply the pending request's exact `turn_id`; the handler checks it against the current active turn twice before dispatch. Pinned T3 has no atomic expected-turn guard in its response command, so this remains a disclosed best-effort current-session response with a narrow residual same-user race. A stale, mismatched, missing, changed, or ambiguous request is rejected before dispatch. Use `decision` for an approval request or `answers` for a user-input request.

Approval decisions have distinct scope: `accept` answers this request once; `acceptForSession` also authorizes matching requests for the current provider session; `decline` refuses once; `cancel` requests cancellation where the provider supports it. For an approval, replace `answers` below with `"decision": "accept"` or `"decision": "decline"`.

The response is a compact receipt containing the exact request, thread, command, and verification/reconciliation IDs. It never embeds the raw thread detail.

Example for `t3_thread_respond`:

```json
{
  "thread_id": "exact-thread-id",
  "request_id": "pending-user-input-request-id",
  "turn_id": "pending-request-turn-id",
  "answers": {
    "scope": "Focused"
  }
}
```

## Create and plan

Create is explicit and never happens as a fallback from send. Omit `instance_id` and `model` together to use the project's model default. If either is present, both are required.

Example for `t3_thread_create`:

```json
{
  "project_id": "project-id-from-t3_threads",
  "title": "Disposable release acceptance",
  "runtime_mode": "approval-required",
  "interaction_mode": "default",
  "initial_message": "Inspect the disposable fixture and report one harmless finding."
}
```

Optional branch and worktree values are T3 metadata; the plugin does not create or inspect a checkout.

### Strict Plan to Build

Plan execution uses the stored server plan, never caller-authored plan prose:

1. Create or set the exact thread to interaction mode `plan`, then send the planning goal.
2. Wait until its provider is no longer running.
3. Read the exact thread and select one unimplemented proposed-plan ID.
4. Call the implementation tool with only the exact thread and stored plan IDs.

Example for `t3_thread_implement_plan`:

```json
{
  "thread_id": "same-thread-id",
  "plan_id": "server-plan-id-from-t3_thread_read"
}
```

The tool verifies the native transition to interaction mode `default`, revalidates the unchanged plan, and starts the same-thread turn with `sourceProposedPlan`. If the mode transition is only accepted pending projection, it stops before dispatching the implementation turn and returns a safe reconciliation path. A full-access Plan implementation repeats the file-modification/deletion warning at the point of action.

Live background work must clear before Plan implementation. Canonical `backgroundLiveness` remains authoritative even if the main provider session is stopped or errored, so re-read or wait rather than starting concurrent work in the same checkout.

## Ten-tool reference

Every handler rejects unknown fields and returns sanitized JSON. Results contain `ok`; errors also contain a stable code, retryability, ambiguity, and safe recovery metadata.

| Tool | Purpose and important defaults |
|---|---|
| `t3_threads` | Filtered compact summaries by default; bounded explicit `raw` view. |
| `t3_thread_read` | Exact bounded material summary by default; optional raw page and cursor. |
| `t3_thread_create` | Create one native thread, optionally with one verified initial turn. |
| `t3_thread_send` | `reject` is observation-only; explicit `queue` acknowledges start-or-queue. |
| `t3_thread_set_mode` | Set exactly one stored runtime or interaction mode. |
| `t3_thread_implement_plan` | Strict same-thread stored-plan transition and implementation. |
| `t3_turn_interrupt` | Best-effort interrupt of the provider session current for the exact thread. |
| `t3_session_stop` | Best-effort stop without deleting or replacing the thread. |
| `t3_thread_wait` | Wait for change/running/blocked/terminal/error with `timeout_seconds` 0-30. |
| `t3_thread_respond` | Best-effort current-session response by request and expected turn IDs. |

Runtime modes are `approval-required`, `auto-accept-edits`, `auto`, and `full-access`. Interaction modes are `default` and `plan`. Messages are trimmed and limited to 120000 JavaScript UTF-16 code units. Identifiers and titles are limited to 512 characters, cursors and worktree paths to 4096, compact lists to 50, and model options to 64 unique entries. Default model-facing text is truncated with explicit metadata: latest messages and errors at 8192 UTF-16 units, actionable plan Markdown at 16384, and nested material collections at 32 items/depth 5. Each complete compact, material, or wait result also has one 262144-byte UTF-8 projection limit and reports `projection_truncated`, its original size, and its limit. Pending-response answers have a 524288-byte aggregate UTF-8 limit before command construction. Explicit bounded `raw` reads remain available for the full server text.

## Safety and recovery

`full-access` permits trusted provider work to execute commands and modify or delete files without approval. Use it only for a trusted provider and checkout. A persisted runtime-mode change cannot cancel or retroactively authorize an approval already pending for a started turn.

Each mutation uses a fresh UUIDv4 command ID and immutable canonical body. A genuinely ambiguous transport retry reuses the same command ID and byte-identical command. Never resend after a successful dispatch response merely because read projection is late.

The pinned T3 command schema has no atomic idle guard. Therefore `busy_policy: reject` never dispatches; use `queue` only when either immediate start or an exact verified queue is acceptable. Its pending-response commands also have no atomic expected-turn guard. `t3_thread_respond` performs two exact current-turn readbacks and labels every receipt best-effort; do not use it where a mutually distrusted local actor can race the same T3 thread.

After acceptance, the plugin reconciles the exact command or message identity for a 15-second projection window. `accepted_pending_projection` is not a failure: it includes the command ID, message ID where applicable, target, and an exact bounded `t3_thread_read` raw reconciliation recipe with `required_snapshot_sequence` and, for a turn, `expected_message_id`. Retrying it as a new send can create duplicate work.

Use these distinctions:

- `mutation_ambiguous`: the transport failed before acceptance could be established; exact-read before any retry.
- `network_error`: a read did not complete; check the live T3 process and loopback origin.
- `response_budget_exhausted`: one operation consumed its 64 MiB cumulative response budget; narrow the read or retry a fresh bounded observation.
- `conflict`: the target is busy, stale, archived, ambiguous, or not in the required state; re-read it.
- `auth_cleanup: failed`: the accepted operation remains authoritative, but revocation confirmation failed; wait for lease expiry and reconcile rather than repeat it.

For a network outage, restore the exact local T3 instance and read again. For zero or multiple selector matches, narrow project/workspace/title filters. For a verification lag, repeat the returned raw read until `snapshotSequence` reaches `required_snapshot_sequence`, then verify `expected_message_id` or the intended state before any new mutation. None of these recovery paths creates a replacement thread.

## Update, rollback, and uninstall

Inspect and test the new commit before replacing an installed copy:

```bash
read -r -p 'New audited 40-character commit SHA: ' HERMES_T3_CONTROL_REF
hermes plugins disable hermes-t3-control
hermes plugins remove hermes-t3-control
hermes plugins install thetasigmaio/hermes-t3-control --ref "$HERMES_T3_CONTROL_REF" --no-enable
hermes plugins doctor hermes-t3-control --ci
hermes plugins enable hermes-t3-control --no-allow-tool-override
```

Then restart the owning Hermes process and repeat the non-thread-mutating first check. Rollback uses the same disable/remove/install sequence with a previously audited compatible commit. v1.1.0 is not an installable rollback target on Hermes 0.20.4 or 0.20.5; its manifest is newer than those installers support. To uninstall and remove its non-secret entry:

```bash
hermes plugins disable hermes-t3-control
hermes plugins remove hermes-t3-control
hermes config unset plugins.entries.hermes-t3-control
```

`remove` is the primary command shown by Hermes 0.20.4/0.20.5 help; `uninstall` is only an alias.

## Security and credential handling

Local mode resolves same-user bounded runtime metadata, a numeric loopback origin, the running Node executable, the exact T3 server entrypoint and package version, and the live environment descriptor. After client construction, the normalized public-argument credential-reflection preflight runs before every T3 HTTP request. Before sending a bearer value, the plugin pins the process identity, proves ownership of the listener, and proves the connected socket belongs to that pinned process. Its temporary bearer value stays in process memory, never enters argv, never persists, is not logged, and is revoked in `finally`. HTTP permits only `GET /.well-known/t3/environment`, `GET /api/orchestration/shell`, `GET /api/orchestration/threads/:threadId`, and `POST /api/orchestration/dispatch`. All proxies and redirects are disabled; origins must be numeric loopback. Responses are bounded to 1 MiB for environment metadata and 16 MiB for orchestration projections. Each public operation also has a 64 MiB cumulative response-body budget—four maximum-sized projections—so polling cannot accumulate unbounded input.

`local-cli` currently requires Linux with `/proc` and `pidfd` support, including WSL2. It assumes a single-user WSL trust boundary and trusts the upstream T3 bundle owned by the current Windows account. Neither auth mode makes a plain shared loopback listener safe from mutually untrusted local users; `external-token` is not a multi-user isolation mechanism. Isolate the operating-system user and T3 service instead.

The current upstream CLI issues eight administrative scopes, including `orchestration:read` and `orchestration:operate`; it cannot yet mint a narrower orchestration-only session. The plugin limits that credential to one bounded operation and its four HTTP routes. This upstream scope breadth is the main local-auth residual risk.

For an operator-isolated headless or local deployment where `local-cli` is unavailable, configure the two non-secret settings exactly:

```bash
hermes config set plugins.entries.hermes-t3-control.settings.auth_mode external-token
hermes config set plugins.entries.hermes-t3-control.settings.base_url http://127.0.0.1:3773
hermes config env-path
```

Provision a profile-scoped `T3_ORCHESTRATION_TOKEN` at the printed environment path through the deployment's Hermes secret facility; do not paste it into the command line. Prefer a short-lived token limited to `orchestration:read` and `orchestration:operate`. Do not place credentials in plugin settings, shell history, command arguments, logs, or repository files.

## Published asset verification

From a trusted v1.2.0 source checkout containing `scripts/verify_release.py`, download into a new private directory and verify before using an artifact:

```bash
umask 077
RELEASE_DIR="$(mktemp -d)"
trap 'rm -rf -- "$RELEASE_DIR"' EXIT
curl --fail --show-error --location --proto '=https' --proto-redir '=https' --output "$RELEASE_DIR/hermes-t3-control-1.2.0.tar.gz" https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.2.0/hermes-t3-control-1.2.0.tar.gz
curl --fail --show-error --location --proto '=https' --proto-redir '=https' --output "$RELEASE_DIR/hermes-t3-control-1.2.0.tar.gz.sha256" https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.2.0/hermes-t3-control-1.2.0.tar.gz.sha256
python3 -B scripts/verify_release.py "$RELEASE_DIR/hermes-t3-control-1.2.0.tar.gz.sha256"
```

## Development verification

Run the dependency-free suite and local release gates from a clean checkout:

```bash
PYTHONWARNINGS=error python3.11 -B -m unittest discover -s tests -v
env PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor . --ci
python3 -B scripts/build_release.py --output-dir dist
python3 -B scripts/verify_release.py dist/hermes-t3-control-1.2.0.tar.gz.sha256
```

The supported-install regression must run inside each exact Hermes lockfile environment. Set `UV_BIN` to a trusted uv 0.12.0 executable, then run this copyable gate from the plugin checkout:

```bash
set -eu
umask 077
UV_BIN="${UV_BIN:-uv}"
test "$("$UV_BIN" --version)" = "uv 0.12.0"
HERMES_GATES="$(mktemp -d)"
trap 'rm -rf -- "$HERMES_GATES"' EXIT
while read -r HERMES_VERSION HERMES_COMMIT; do
  HERMES_TREE="$HERMES_GATES/hermes-$HERMES_VERSION"
  git init -q "$HERMES_TREE"
  git -C "$HERMES_TREE" remote add origin https://github.com/NousResearch/hermes-agent.git
  GIT_TERMINAL_PROMPT=0 git -C "$HERMES_TREE" fetch --depth=1 origin "$HERMES_COMMIT"
  git -C "$HERMES_TREE" checkout -q --detach FETCH_HEAD
  "$UV_BIN" sync --frozen --project "$HERMES_TREE" --python 3.11
  env HERMES_SUPPORTED_INSTALL_TEST=1 PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS=error \
    "$UV_BIN" run --frozen --project "$HERMES_TREE" python -B -m unittest -v tests.test_supported_install
done <<'EOF'
0.20.4 e624e9fde561e1add9388384012b295fde669ade
0.20.5 fcbd1076a93841fa88855acce810e342a5b78101
EOF
```

CI performs the same SHA-pinned uv setup and `uv sync --frozen` gate. The regression itself uses an allowlisted environment with private temporary HOME/XDG paths and neutral Git configuration. Its fresh-process socket guard permits only the expected loopback TCP connection to a fixture server; it is not a general no-egress sandbox.
