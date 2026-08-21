# Hermes T3 Control 1.1.1

Hermes T3 Control is a synchronous native directory plugin that lets Hermes operate T3-owned Codex threads through T3's authenticated numeric-loopback orchestration API.

The mental model is small: use `t3_threads` to discover projects and threads, use `t3_thread_read` to inspect one exact thread, and use the six mutation tools only against an ID returned by T3. Mutations return a verified exact-thread readback; the plugin is not a raw dispatcher or a general T3 client.

Compatibility: Hermes 0.20.4 and 0.20.5, Python 3.11-3.13, and T3 server contract `0.0.34-nightly.20260820.1141`. Manifest version 1 is deliberate because it is the newest version accepted by both supported Hermes installers. Released under the [MIT License](LICENSE).

## Secure quick start

Work in the intended Hermes profile. These commands show which profile files will receive configuration and make the install-time scanner prerequisite explicit:

```bash
hermes config path
hermes config env-path
hermes config set plugins.scan_on_install true
hermes config get plugins.scan_on_install --json
```

The final command must print `true` before installation.

Set the audited v1.1.1 release commit, then install that immutable revision disabled. Replace the placeholder with the published 40-character commit SHA; do not use a branch name or a moving tag.

```bash
read -r -p 'v1.1.1 release commit SHA: ' HERMES_T3_CONTROL_REF
hermes plugins install thetasigmaio/hermes-t3-control --ref "$HERMES_T3_CONTROL_REF" --no-enable
```

The supported path does not need `--force` for a fresh install. At the masked `requires_env` prompt, enter the operator-provisioned `T3_ORCHESTRATION_TOKEN`. Prefer a short-lived token limited to `orchestration:read` and `orchestration:operate`.

Configure the non-secret loopback origin and the conservative create default:

```bash
hermes config set plugins.entries.hermes-t3-control.settings.base_url http://127.0.0.1:9137
hermes config set plugins.entries.hermes-t3-control.settings.default_runtime_mode approval-required
hermes config get plugins.entries.hermes-t3-control.settings --json
```

`base_url` must be an `http` or `https` root origin with a numeric loopback address. Hostnames such as `localhost`, credentials in the URL, paths, queries, fragments, redirects, proxies, and cross-origin requests are rejected.

Validate the installed copy, then enable it without tool-override permission:

```bash
hermes plugins doctor hermes-t3-control --ci
hermes plugins enable hermes-t3-control --no-allow-tool-override
hermes plugins show hermes-t3-control
```

Doctor must report `registrations: 8 tool(s), 0 hook(s)`. This is the first read-only verification; it checks the installed manifest and registration without contacting T3.

Start a new Hermes CLI session so it reloads plugins. For a messaging gateway, run `hermes gateway restart`. A Desktop/T3 backend based on `hermes serve` must be fully stopped and restarted through its normal owner; reconnecting to the same process is insufficient. For a manually owned backend, inspect its status first:

```bash
hermes serve --status
```

Confirm the process and profile owner, stop that backend through the same terminal or process manager that launched it, and relaunch it with the same configuration (`hermes serve` only when that was its original launch command). Do not use `hermes serve --stop` while another backend may be running: it stops every Hermes web-server process, including unrelated `serve` and `dashboard` instances.

For the first live T3 check, use an operator-supervised Hermes session and invoke exactly `t3_threads` with `{}` before any mutation. Do not use one-shot `-z` as a read-only safety boundary: it exposes all eight tools and automatically bypasses Hermes approvals.

## Create and continue one thread

Call `t3_thread_create` with a project ID returned by `t3_threads`. Omitting `instance_id` and `model` uses the project's default model selection; if either is supplied, both are required.

```json
{
  "project_id": "project-id-from-t3_threads",
  "title": "Audit the release candidate",
  "runtime_mode": "approval-required",
  "interaction_mode": "default",
  "initial_message": "Inspect the release candidate and report findings."
}
```

The result action is `thread_created_with_initial_turn` and includes the new `thread_id`, separate create/turn command IDs, the message ID, provider session, latest turn, exact `detail`, and `race_semantics`. Without `initial_message`, the action is `thread_created` and no turn starts.

Continue the same thread with its returned ID:

```json
{
  "thread_id": "thread-id-returned-by-t3_thread_create",
  "message": "Now summarize the two highest-risk findings."
}
```

`t3_thread_send` pre-reads the stored model, runtime, and interaction modes and starts a fresh turn on that same thread, including after `t3_session_stop`. It never creates a replacement thread or re-resolves the project's creation default.

Optional `branch` and `worktree_path` values on create are T3 metadata only. The plugin does not inspect Git, resolve branches, or create worktrees. Explicit `model_options` replaces options for an explicit `instance_id`/`model` pair; an omitted option list preserves canonical options only when that pair matches the project default.

## Strict Plan to Build on the same thread

Use the stored server plan, never caller-authored plan prose:

1. Call `t3_thread_create` with `interaction_mode: "plan"` and an `initial_message` asking for a plan.
2. Wait until the turn and provider session are no longer running.
3. Call `t3_thread_read` for the same thread. Select one `detail.thread.proposedPlans[].id` whose `implementedAt` and `implementationThreadId` are both null.
4. Call `t3_thread_implement_plan` with only those server identities:

```json
{
  "thread_id": "same-thread-id",
  "plan_id": "server-plan-id-from-t3_thread_read"
}
```

The implementation tool performs and verifies the native interaction-mode transition to `default`, revalidates the unchanged stored plan, and then starts the implementation turn with `sourceProposedPlan: {threadId, planId}`. Success is `same_thread_plan_implementation_started` with mode/turn command IDs, accepted dispatch metadata, same-thread provenance, provider session, latest turn, exact readback, and `race_semantics`.

Do not run concurrent implement calls. T3 has no atomic expected-mode, idle-state, unimplemented-plan, or at-most-once guard, so success proves observed ordering and provenance but cannot exclude a transient mode change or duplicate concurrent work. If the mode phase succeeds and the turn phase fails, the error identifies the completed phase; the plugin does not roll the mode back.

## Eight-tool reference

Every tool rejects unknown fields and returns sanitized JSON. All results contain `ok`. Errors also contain `error_code`, `retryable`, `outcome_ambiguous`, and a safe `error`; target and recovery metadata appear when available.

| Tool | Inputs and defaults | Successful result |
|---|---|---|
| `t3_threads` | `{}` | `shell`: validated projects, threads, and statuses. |
| `t3_thread_read` | `thread_id`; `turn_limit` 1-150, default 20; `before_cursor` requires an explicit limit | `detail`: exact bounded thread page. |
| `t3_thread_create` | `project_id`, `title`; optional paired model selection, modes, initial message, and checkout metadata | `thread_created` or `thread_created_with_initial_turn`, command IDs, and exact readback. |
| `t3_thread_send` | `thread_id`, `message` | `new_turn_same_thread`, command/message IDs, session, latest turn, and exact readback. |
| `t3_thread_set_mode` | `thread_id` and exactly one of `runtime_mode` or `interaction_mode` | `thread_mode_set` or verified `mode_already_set`. |
| `t3_thread_implement_plan` | `thread_id`, server `plan_id` | `same_thread_plan_implementation_started` with provenance and both phases' metadata. |
| `t3_turn_interrupt` | `thread_id` | `best_effort_turn_interrupt`, captured turn ID, and exact readback. |
| `t3_session_stop` | `thread_id` | `best_effort_session_stop` or verified `already_stopped`. |

Runtime modes are `approval-required`, `auto-accept-edits`, `auto`, and `full-access`; the create fallback is `approval-required`. Interaction modes are `default` and `plan`. Messages are trimmed and limited to 120000 JavaScript UTF-16 code units. Identifiers and titles are limited to 512 characters, cursors and worktree paths to 4096, and `model_options` to 64 unique string-or-Boolean entries.

## Safety and recovery

**`full-access` permits trusted provider work to execute commands and modify or delete files without approval.** Select it only for a trusted provider and checkout. Changing a persisted runtime mode after a turn starts cannot cancel, remove, or retroactively authorize an approval already pending for that turn, and it does not prove that the running provider session changed mode.

Each logical mutation uses one fresh UUIDv4 command ID and an immutable canonical body. After an ambiguous transmission, the plugin exact-reads before retrying and reuses the byte-identical command. For `mutation_ambiguous` or `verification_failed`, reconcile with `t3_thread_read` before issuing any new mutation.

`t3_turn_interrupt` and `t3_session_stop` are best effort because T3 has no atomic expected-session guard. Interrupt detects an observed newer turn as `concurrent_state_change`; stop never deletes or replaces the thread.

One HTTP request has a 10-second absolute monotonic deadline. A logical mutation is bounded to 30 seconds, at most three dispatch attempts, and a five-second accepted-state poll. Requests are limited to 1 MiB and responses to 16 MiB. Transport is restricted to these endpoints and never follows redirects:

- `GET /api/orchestration/shell`
- `GET /api/orchestration/threads/:threadId`
- `POST /api/orchestration/dispatch`

## Troubleshooting

- `requires manifest_version 2`: the selected SHA is not v1.1.1. Recheck the 40-character release commit.
- Install-time security block mentioning historical development specs: the selected SHA is not the clean v1.1.1 repository tree. Do not bypass it with `--force` or disable scanning.
- Doctor does not report eight tools: keep the plugin disabled, confirm the active profile and installed SHA, then reinstall the audited ref.
- `configuration_error`: check `hermes config get plugins.entries.hermes-t3-control.settings --json`, the active profile's masked credential, and the numeric-loopback T3 origin.
- `authentication_error` or `authorization_error`: refresh the operator-managed token and its two orchestration scopes; never retry with the credential in a prompt or tool input.
- `network_error`: restore or verify the configured loopback T3 service; retry only when `outcome_ambiguous` is false. Otherwise reconcile the exact thread before any mutation.
- `conflict`: re-read `t3_threads` or `t3_thread_read`, resolve stale IDs or running/already-implemented state, then decide whether a new mutation is valid.
- A pinned `hermes plugins update` is refused by design. Use the pinned reinstall flow below.

## Update, rollback, and uninstall

`--no-enable` does not clear an existing enabled entry. Disable and remove the old copy before installing the new audited revision; plugin removal preserves its profile settings and secret:

```bash
read -r -p 'New audited 40-character commit SHA: ' HERMES_T3_CONTROL_REF
hermes plugins disable hermes-t3-control
hermes plugins remove hermes-t3-control
hermes plugins install thetasigmaio/hermes-t3-control --ref "$HERMES_T3_CONTROL_REF" --no-enable
hermes plugins doctor hermes-t3-control --ci
hermes plugins enable hermes-t3-control --no-allow-tool-override
```

Use this flow only with a separately reviewed exact commit. Do not substitute `--force`: `--force` also accepts a `caution` security-scan verdict, not only replacement, so it weakens the install gate.

Restart the long-lived Hermes host as described in quick start. Rollback uses the same commands only with an audited previous revision whose manifest version is 1. v1.1.0 is not an installable rollback target on Hermes 0.20.4 or 0.20.5 because its manifest version is 2. If no compatible known-good revision exists, keep v1.1.1 disabled or remove it; there is no moving-channel or automatic rollback command.

To remove the plugin:

```bash
hermes plugins disable hermes-t3-control
hermes plugins remove hermes-t3-control
hermes config unset plugins.entries.hermes-t3-control
```

Then revoke the T3 token and remove `T3_ORCHESTRATION_TOKEN` from the active profile path shown by `hermes config env-path`, without printing it. Plugin removal does not erase profile settings or secrets automatically. Restart any long-lived Hermes host.

`hermes plugins disable` also leaves `hermes-t3-control` in `plugins.disabled`. To remove that harmless residual entry, run `hermes config edit` and delete only that list item; preserve every other disabled plugin.

## Credential and data boundary

Enter the token only through Hermes' masked `requires_env` prompt for the selected profile. Never put it in argv, shell history, `config.yaml`, source, logs, examples, or tool inputs. The plugin resolves it through Hermes' profile-scoped secret API and does not issue or persist credentials.

The plugin does not read SQLite, T3/Codex event-log JSONL, process credentials, or internal state; modify Hermes, T3, or Codex core; expose raw dispatch or WebSocket control; create worktrees; upload attachments; answer approvals; change an existing thread's model; or archive, delete, or replace projects or threads.

## Development and release verification

```bash
python3 -B -m unittest discover -s tests -v
env HERMES_SUPPORTED_INSTALL_TEST=1 PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v tests.test_supported_install
env PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor . --ci
python3 -B scripts/build_release.py --output-dir dist
python3 -B scripts/verify_release.py dist/hermes-t3-control-1.1.1.tar.gz.sha256
```

CI runs the supported-install regression separately against pinned Hermes 0.20.4 and 0.20.5 revisions. It uses a fresh `HERMES_HOME`, explicit `plugins.scan_on_install: true`, the real `hermes plugins install` pinned-ref path without `--force`, and the exact tracked repository tree. It then enables without tool-override permission and uses a fresh network-blocked Python process plus Plugin Doctor to verify exactly eight loaded tools and their manifest, configuration, and secret metadata.

After publication, download and verify both release assets:

```bash
mkdir -p dist
curl --fail --location --output dist/hermes-t3-control-1.1.1.tar.gz https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.1.1/hermes-t3-control-1.1.1.tar.gz
curl --fail --location --output dist/hermes-t3-control-1.1.1.tar.gz.sha256 https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.1.1/hermes-t3-control-1.1.1.tar.gz.sha256
python3 -B scripts/verify_release.py dist/hermes-t3-control-1.1.1.tar.gz.sha256
```

Resolve the annotated v1.1.1 tag to the immutable commit used by `--ref`, then compare it with the release announcement before installation:

```bash
HERMES_T3_CONTROL_REF="$(git ls-remote https://github.com/thetasigmaio/hermes-t3-control.git 'refs/tags/v1.1.1^{}' | awk 'NR == 1 {print $1}')"
test "${#HERMES_T3_CONTROL_REF}" -eq 40
printf '%s\n' "$HERMES_T3_CONTROL_REF"
```

The optional live smoke is read-only but requires an operator-designated isolated non-SolarSim thread. Run `python3 -B scripts/live_smoke.py` only with `T3_SMOKE_ISOLATED=1`, `T3_SMOKE_THREAD_ID`, `T3_ORCHESTRATION_BASE_URL`, and `T3_ORCHESTRATION_TOKEN` supplied through that environment's secret facility. It performs one exact-thread GET and prints no messages, plan text, URL, settings, or credentials.
