# Hermes T3 Control 1.1.0

Hermes T3 Control is a synchronous native directory plugin that gives Hermes a small, typed control surface for T3-owned Codex threads. It uses only T3's authenticated numeric-loopback orchestration HTTP API; it is not a general T3 client, a raw command dispatcher, or a Codex/Hermes core extension.

The compatibility targets for this release are Hermes 0.20.4 and 0.20.5 on Python 3.11-3.13, and the T3 server contract in `0.0.34-nightly.20260820.1141`.

Released under the [MIT License](LICENSE).

## Install and configure

The public source is `thetasigmaio/hermes-t3-control`. Install an audited release commit, not a moving branch, and keep it disabled while you review configuration and provision its secret:

```bash
hermes plugins install thetasigmaio/hermes-t3-control --ref <40-character-release-commit-sha> --no-enable
```

Hermes Git installation is disabled by default with `--no-enable`. During an interactive install, paste `T3_ORCHESTRATION_TOKEN` only into Hermes' masked `requires_env` prompt for the selected Hermes profile. Never put the token in argv, shell history, plugin settings, source files, logs, examples, or tool inputs. The externally provisioned, preferably short-lived token needs only `orchestration:read` and `orchestration:operate`.

Set non-secret values under `plugins.entries.hermes-t3-control.settings` in the same profile:

```yaml
plugins:
  entries:
    hermes-t3-control:
      settings:
        base_url: http://127.0.0.1:9137
        default_runtime_mode: full-access
```

`base_url` must be an `http` or `https` root origin with a numeric loopback address. Hostnames such as `localhost`, URL credentials, paths, queries, fragments, redirects, and cross-origin requests are rejected.

**Warning:** `full-access` permits trusted provider work to execute commands and modify or delete files without approval. The example is an explicit operator choice for a trusted local provider; it is not the release default. If neither a create call nor the profile supplies `default_runtime_mode`, the conservative fallback is `approval-required`. A create call's typed `runtime_mode` overrides the configured default.

After checking the active profile, secret, URL, and runtime policy, enable the plugin explicitly:

```bash
hermes plugins enable hermes-t3-control
```

## Exact tool surface

The plugin registers exactly eight non-overriding tools. Every tool returns sanitized JSON with `ok`; successful mutations also return their verified exact-thread readback and command metadata.

| Tool | Inputs | Result and purpose |
|---|---|---|
| `t3_threads` | none (`{}`) | Validated shell snapshot, projects, and thread statuses. |
| `t3_thread_read` | required `thread_id`; optional `turn_limit` (1-150, default 20) and `before_cursor` (requires an explicit limit) | Exact thread detail, bounded messages/turns, proposed plans, and turn provenance. |
| `t3_thread_create` | required `project_id`, `title`; optional paired `instance_id` and `model`, `model_options`, `runtime_mode`, `interaction_mode`, `initial_message`, `branch`, `worktree_path` | Creates and verifies one thread; optionally starts its initial turn only after create readback. |
| `t3_thread_send` | required `thread_id`, `message` | Starts a fresh turn on that same existing thread, including after provider-session stop. |
| `t3_thread_set_mode` | required `thread_id` and exactly one of `runtime_mode` or `interaction_mode` | Sets one persisted mode with exact readback, or returns a verified no-op. |
| `t3_thread_implement_plan` | required `thread_id`, `plan_id` | Performs the native, provenance-preserving same-thread Plan to Build transition for one stored unimplemented plan. |
| `t3_turn_interrupt` | required `thread_id` | Best-effort interruption of the captured running active turn. |
| `t3_session_stop` | required `thread_id` | Best-effort stop of the current provider session without deleting or replacing the thread. |

Runtime modes are `approval-required`, `auto-accept-edits`, `auto`, and `full-access`; interaction modes are `default` and `plan`. Unknown fields and unsupported or over-limit values fail before HTTP.

## Create, initial turn, and resume

A complete create call can make the checkout and provider selection explicit:

```json
{
  "project_id": "project-from-t3-shell",
  "title": "Audit the release candidate",
  "instance_id": "provider-instance-from-t3",
  "model": "gpt-5.6-sol",
  "model_options": [
    {"id": "reasoning_effort", "value": "ultra"},
    {"id": "service_tier", "value": "priority"}
  ],
  "runtime_mode": "full-access",
  "interaction_mode": "default",
  "initial_message": "Inspect the release candidate and report findings.",
  "branch": "release/1.1.0",
  "worktree_path": "<operator-selected-local-worktree>"
}
```

Call this object with `t3_thread_create`. `instance_id` and `model` must be supplied together. If both match the project's default selection and `model_options` is omitted, its canonical options are preserved. A different explicit pair never inherits selection-specific options; explicit `model_options` replaces them. Without a project default, the pair is required.

Option IDs and values must come from the selected T3 model instance; the example deliberately preserves GPT-5.6-Sol's Ultra reasoning and priority service tier. `branch` and `worktree_path` are metadata only: the plugin records and verifies their T3 fields but does not inspect Git, resolve a branch, or create a worktree. Omission records null values.

With `initial_message`, create is a two-command workflow: exact create readback first, then a fresh `thread.turn.start` built from that readback's same model selection, runtime mode, and interaction mode. The result exposes separate create and turn command IDs plus `race_semantics`. T3 has no atomic expected-mode guard, so final readback proves the observed settings and message, not that a concurrent client could not transiently change a mode before provider acceptance.

Resume by calling `t3_thread_send` with the returned `thread_id` and a new `message`. It pre-reads persisted model/runtime/interaction settings and starts a new turn on the same T3 thread; it never re-resolves the creation default and never creates a replacement thread.

## Native same-thread Plan to Build

Use the server's proposed plan identity, never caller-authored plan prose:

The workflow has two mutation calls: create with the initial Plan turn, then implement. The exact read between them obtains the server plan ID without adding a mutation.

1. Call `t3_thread_create` with `interaction_mode: "plan"` and an `initial_message` asking the provider to plan. Wait until the turn and provider session are no longer running.
2. Call `t3_thread_read` for that `thread_id` and take the unimplemented server `planId` from `detail.thread.proposedPlans[].id` (both `implementedAt` and `implementationThreadId` are null).
3. Call `t3_thread_implement_plan` with only the same thread and returned server plan ID:

```json
{
  "thread_id": "thread-id-returned-by-t3_thread_create",
  "plan_id": "server-planId-returned-by-t3_thread_read"
}
```

The implementation tool always sends the typed `thread.interaction-mode.set` transition to `default` and verifies it before starting the implementation turn. That boundary requires a returned accepted dispatch sequence: after an ambiguous transmission, a `default` readback alone is insufficient, so the plugin exact-reads and retries the byte-identical command with the same ID or stops as `mutation_ambiguous`. It builds the native message from the stored `planMarkdown`, attaches `sourceProposedPlan: {threadId, planId}`, and reuses the stored model and runtime on the same thread. Success requires readback of `interactionMode: default`, exact `latestTurn.sourceProposedPlan`, non-null `implementedAt`, and `implementationThreadId` equal to the source thread. The result also exposes `source_proposed_plan`, `mode_command_id`, `mode_dispatch_sequence`, and `turn_command_id`.

These preconditions and readbacks are non-atomic. They prove observed ordering and same-thread provenance, not at-most-once implementation: concurrent callers can race, start duplicate implementation work, or transiently change state. If the mode phase succeeds and the turn phase fails, the error identifies the completed phase and mode command; the plugin does not roll the mode back.

## Modes, interruption, and stop

Call `t3_thread_set_mode` with `thread_id` and exactly one typed field, for example `{"thread_id":"...","runtime_mode":"approval-required"}` or `{"thread_id":"...","interaction_mode":"plan"}`. A runtime change updates persisted thread metadata for later turns. It does **not** cancel, remove, or retroactively authorize an approval already created for an active turn, and it does not prove that the running provider session changed mode.

`t3_turn_interrupt` requires a running active turn. T3 has no atomic expected-turn guard, so the provider session current at reactor execution may receive the interrupt; observing a newer turn produces `concurrent_state_change`. `t3_session_stop` is likewise best effort because there is no expected-session identity guard. It verifies stopped/inactive state and never deletes or replaces the thread. A later `t3_thread_send` still resumes the same thread.

## Mutation recovery and bounds

Each logical mutation gets one fresh UUIDv4 command ID and an immutable canonical body; separate workflow phases get distinct IDs. An ambiguous transmission is sticky: the plugin reads the exact target before any retry and reuses the byte-identical body with that same command ID. For `mutation_ambiguous` or `verification_failed`, reconcile with `t3_thread_read`; do not start another mutation merely to obtain a new ID.

- One request has a 10-second absolute monotonic deadline; a logical mutation is bounded to 30 seconds, at most three dispatch attempts, and a five-second accepted-state poll.
- Request bodies are limited to 1 MiB and responses to 16 MiB.
- Base URLs are limited to 2048 characters; identifiers, titles, branch names, and model-option IDs/strings to 512; worktree paths and cursors to 4096.
- `model_options` allows at most 64 uniquely keyed string-or-Boolean entries. Messages are trimmed and limited to 120000 JavaScript UTF-16 code units.
- Transport is limited to `GET /api/orchestration/shell`, `GET /api/orchestration/threads/:threadId`, and `POST /api/orchestration/dispatch`; it uses no proxy and never follows redirects.

## Exclusions

The plugin does not issue or persist tokens; inspect SQLite, T3/Codex event-log JSONL, process credentials, or internal state; modify Hermes, T3, or Codex core; expose raw dispatch or WebSocket control; create worktrees; upload attachments; answer approvals; change an existing thread's model; archive, delete, or replace projects or threads; or mutate another thread as a side channel.

## Local and release verification

From the repository root, the full local gates and deterministic artifact checks are:

```bash
python3 -B -m unittest discover -s tests -v
env PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor . --ci
python3 -B scripts/build_release.py --output-dir dist
python3 -B scripts/verify_release.py dist/hermes-t3-control-1.1.0.tar.gz.sha256
```

Focused gates are:

```bash
python3 -B -m unittest -v tests.test_client
python3 -B -m unittest -v tests.test_tools tests.test_registration
python3 -B -m unittest -v tests.test_release
python3 -B -m unittest -v tests.test_readme
```

Release assets follow these URLs:

```text
https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.1.0/hermes-t3-control-1.1.0.tar.gz
https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.1.0/hermes-t3-control-1.1.0.tar.gz.sha256
```

Download both assets into `dist/`, run the same checksum verification command above, read back the release tag's peeled commit, and use that exact 40-character commit with the disabled pinned-ref install pattern. Do not install from a moving branch.

The optional live smoke has not been run or implied by these local gates. Run `python3 -B scripts/live_smoke.py` only in an isolated environment after explicitly setting `T3_SMOKE_ISOLATED=1`, `T3_SMOKE_THREAD_ID`, `T3_ORCHESTRATION_BASE_URL`, and `T3_ORCHESTRATION_TOKEN` for one operator-designated isolated non-SolarSim thread. Supply the token through that environment's secret facility, never on argv or in logs/source. The smoke performs one read-only exact-thread GET and prints no messages, plan text, URL, settings, or credentials; otherwise record it as N/A.
