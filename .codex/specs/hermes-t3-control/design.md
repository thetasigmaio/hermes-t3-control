# Design: Hermes T3 Control 1.1 Release

- **Spec-ID**: `hermes-t3-control`
- **Profile**: Strict

## Current constraints

The repository has no commits or remote. Version 1.0.0 is already installed as a copied, enabled local plugin, so this additive public release is 1.1.0 even though the GitHub repository is new. The implementation remains a synchronous native Hermes directory plugin with no Python runtime dependencies. T3's HTTP decider uses persisted thread modes for existing-thread turns; mode fields carried in `thread.turn.start` do not retroactively change the thread or an already-created approval.

## D-1: Native plugin and configuration boundary
- **Covers**: FR-1/AC-1, FR-1/AC-2, FR-3/AC-1, NFR-1/AC-2, NFR-4/AC-3

Keep root `plugin.yaml`, registration-only `__init__.py`, `schemas.py`, `client.py`, and `tools.py`. Register the exact eight schemas synchronously with `override=False`; registration constructs no client and performs no network I/O.

Manifest v2/API v1 declares version 1.1.0, MIT, the GitHub homepage, no Python dependencies, secret `T3_ORCHESTRATION_TOKEN`, required non-secret `base_url`, and optional string `default_runtime_mode` with default `approval-required`. Hermes' config schema is advisory, so every create call reads and validates the setting again. Explicit `runtime_mode` wins over configured mode. All other tools use persisted thread mode, never the creation default.

## D-2: Secret and direct transport boundary
- **Covers**: NFR-1/AC-1, NFR-1/AC-2, NFR-1/AC-3, NFR-1/AC-4, NFR-2/AC-1, NFR-2/AC-2, NFR-2/AC-3, NFR-3/AC-1

Retain per-call profile-safe secret resolution, bounded RFC 6750 credential validation, numeric-loopback origin validation, standard-library `http.client`, no DNS hostname, no proxy, no redirects, exact endpoint allowlist, canonical request serialization, cumulative monotonic deadlines, finite retries, size bounds, response closing, sanitized errors, and recursive reflected-secret rejection. Before transport, reject the active token as a substring of every normalized exact-thread path/query value and recursively anywhere in a mutation command; this check must happen before URL construction or request serialization can send or persist the credential.

The client still accepts one immutable command object per `mutate` call. It generates no public IDs, follows no redirect, stores no state, and reads no T3/Hermes/Codex internals. Existing sticky ambiguity and exact-target identity checks remain the common mutation primitive for every new command.

## D-3: Forward-compatible thread, plan, and provenance schemas
- **Covers**: FR-2/AC-1, FR-2/AC-2, FR-12/AC-1, FR-12/AC-2, FR-12/AC-3

Keep validation of shell/project/thread/model/session/message/dispatch/pagination fields already consumed. Extend latest-turn validation for optional `sourceProposedPlan` and detail validation for required `proposedPlans`. Each proposed plan has non-empty `id`, nullable turn ID, non-empty `planMarkdown`, nullable RFC 3339 `implementedAt`, nullable `implementationThreadId`, and creation/update timestamps. Require both implementation fields to be null together or non-null together. Preserve the decoded object so unrelated additive fields remain available.

Exact detail requests continue to require returned `thread.id` and any session `threadId` to match the target. Plan and provenance references must contain exact non-empty thread/plan IDs. No shell flag substitutes for detail plan identity.

## D-4: Creation settings and model resolution
- **Covers**: FR-3/AC-1, FR-3/AC-2, FR-3/AC-3, FR-3/AC-4, FR-3/AC-5, FR-4/AC-1, FR-4/AC-2, FR-4/AC-3, FR-4/AC-4, FR-4/AC-5

Extend `t3_thread_create` with optional `initial_message`, `branch`, and `worktree_path`. Normalize branch to at most 512 characters and worktree path to at most 4096; omitted values become JSON null. The plugin records metadata only and neither resolves Git nor creates a worktree.

Resolve the model selection by deep-copying the project's complete default selection. A complete explicit `instance_id`/`model` pair equal to the default retains its canonical option entries such as reasoning effort and service tier. A different pair constructs a new selection and does not inherit selection-specific options, matching the current `t3code` CLI. Optional `model_options` requires the explicit pair and replaces inherited options with at most 64 uniquely keyed entries whose IDs and non-empty string values are at most 512 characters and whose values are strings or Booleans. If the project default is null, require the complete pair; options remain optional.

Resolve runtime once from explicit input, configured default, or `approval-required`; resolve interaction once from explicit input or `default`. Dispatch `thread.create` with fresh command/thread IDs and verify project, title, full model selection, both modes, branch, and worktree path. Return a prominent warning whenever the resolved runtime is `full-access`.

If `initial_message` is present, use the verified create readback as the sole source for the first turn's model/runtime/interaction values. Dispatch a fresh `thread.turn.start` only after create readback, then require the generated message, unchanged settings, and a latest turn on the same thread. If the second phase fails, enrich the existing typed error with `workflow_phase`, verified create command ID, and created thread ID; do not delete or roll back the thread.

The create/read/turn sequence ensures the selected runtime is persisted before the first turn and copied into that turn command. T3 has no atomic expected-mode precondition, so success cannot prove that another client did not transiently change a persisted mode between readback and provider acceptance. Return this limitation as `race_semantics`; post-readback verifies observed final state only.

## D-5: Shared same-thread turn builder
- **Covers**: FR-5/AC-1, FR-5/AC-2, FR-5/AC-3, FR-5/AC-4, FR-7/AC-3, FR-7/AC-4, FR-7/AC-5, FR-11/AC-1, FR-11/AC-3

Factor a private turn builder that receives a validated exact thread snapshot, normalized message, and optional source-plan reference. It creates fresh command/message UUIDv4 values; copies stored model selection, title, runtime, and interaction; uses empty attachments; and never sends bootstrap. Generic `t3_thread_send` never accepts plan provenance and always pre-reads its target.

The standard send predicate requires the exact message and unchanged persisted model/runtime/interaction settings. The plan predicate additionally requires interaction `default`, exact `latestTurn.sourceProposedPlan`, and the referenced plan marked implemented on the same thread.

Both predicates verify only the final read model. Each send result states that a transient external mode change before provider acceptance cannot be excluded without an upstream atomic expected-mode guard.

## D-6: Focused mode tool and non-retroactive warning
- **Covers**: FR-6/AC-1, FR-6/AC-2, FR-6/AC-3, FR-6/AC-4

Add `t3_thread_set_mode` with `thread_id` and exactly one of `runtime_mode` or `interaction_mode`. Pre-read and reject deleted/archived targets. Return a verified no-op when the persisted value already matches. Otherwise dispatch the corresponding dedicated command with a fresh ID and require exact persisted-field readback at or beyond the accepted sequence.

Capture the pre-read active turn ID. A runtime change while a turn is active is permitted as a persisted future setting, but the result includes `active_turn_unchanged` and a warning that it neither cancels nor retroactively authorizes an approval already created by that turn. Do not claim the session runtime changed merely because thread metadata did.

## D-7: Strict Plan to Build state machine
- **Covers**: FR-7/AC-1, FR-7/AC-2, FR-7/AC-3, FR-7/AC-4, FR-7/AC-5, FR-7/AC-6, FR-7/AC-7, FR-7/AC-8

`t3_thread_implement_plan` accepts only `thread_id` and `plan_id`. Pre-read up to the existing maximum detail window and require a non-deleted/non-archived thread, no running/starting session or latest turn, and an exact plan whose implementation fields are both null. Build and validate the native UI prompt from stored `planMarkdown` before any mutation.

Always dispatch a fresh `thread.interaction-mode.set` to `default`, even if the pre-read mode is already default, because this command is the explicit transition boundary. Invoke `mutate` in acceptance-required mode: after any ambiguous attempt it still reads the exact thread before byte-identical retry, but predicate-only `default` readback never completes this phase. The phase requires a returned accepted sequence followed by readback at or beyond that sequence; exhausting retries without acceptance is `mutation_ambiguous` and stops before the implementation turn. Test this when the pre-state is both `default` and `plan`. Revalidate the same plan ID, content/update identity, unimplemented state, and idle state from the returned detail.

Then dispatch a fresh `thread.turn.start` on the same thread using the shared builder, persisted model/runtime, interaction `default`, and `sourceProposedPlan: {threadId: thread_id, planId: plan_id}`. Success requires the exact message, exact latest-turn provenance, default interaction mode, and the plan's non-null `implementedAt` with `implementationThreadId == thread_id`. Return separate mode and turn command IDs. On second-phase error, attach the verified mode command/phase and do not roll back, because rollback could overwrite a concurrent client change.

There is no atomic expected-mode, unimplemented-plan, idle-state, or at-most-once guard. Post-dispatch predicates prove the required state was eventually observed but cannot detect every transient mode flip or prevent two concurrent callers from starting duplicate implementation work. Return and document this residual race; do not represent successful provenance as an at-most-once guarantee.

## D-8: Interrupt, stop, mutation, and error invariants
- **Covers**: FR-8/AC-1, FR-8/AC-2, FR-9/AC-1, FR-9/AC-2, FR-10/AC-1, FR-10/AC-2, FR-10/AC-3, FR-10/AC-4, FR-10/AC-5, FR-10/AC-6, FR-11/AC-1, FR-11/AC-2, FR-12/AC-3

Preserve interrupt and stop command shapes, preconditions, exact predicates, and race disclosures. Preserve canonical bytes, UUIDv4 validation, per-command fresh IDs, readback-before-retry, accepted-sequence polling, typed outcome flags, and bounded sanitized error details. Ambiguity history remains sticky across a later accepted retry: predicate verification reports recovered success with the accepted sequence and complete attempt count; poll expiry and later transport, HTTP, size, schema, readback, or terminal-dispatch failures remain `mutation_ambiguous` rather than downgrading to `verification_failed`. A proven newer-turn interrupt race remains `concurrent_state_change` even after earlier ambiguity.

Expose acceptance-required behavior only as an internal `mutate` option. It changes ambiguous predicate recovery, not public identity or retry controls: readback still occurs before retry, the byte-identical command and ID are retained, a returned accepted sequence is mandatory, and exhaustion stays ambiguous. Ordinary mutations retain predicate-based recovery.

For multi-command workflows, each `mutate` call owns one independent command identity. Completed-phase metadata is additive error detail only; it never changes the current command's ambiguity or retry contract.

## D-9: Tests, static safety, and conditional live read
- **Covers**: NFR-3/AC-1, NFR-3/AC-2, NFR-4/AC-1, NFR-4/AC-2, NFR-4/AC-3, NFR-4/AC-4

Extend `unittest` loopback fixtures with proposed plans and provenance. Add deterministic tests for config precedence/failure, full-access warning, create plus initial turn ordering and shared settings, branch/worktree metadata, option preservation, both mode commands/no-op/invalid dual input, active approval non-retroactivity, Plan to Build command ordering/native prompt/provenance, phase failures, plan preconditions, and distinct IDs. Retain all transport, schema, secret, retry, timeout, race, and registration tests.

Static tests require `.codex/specs/`, reject `.claude/specs/`, forbid secret-like literals and forbidden persistence/process modules, and assert the exact eight-tool surface. Plugin Doctor remains the real loader/registration gate.

Add `scripts/live_smoke.py` as a standard-library, read-only optional gate. It requires `T3_SMOKE_ISOLATED=1`, `T3_SMOKE_THREAD_ID`, `T3_ORCHESTRATION_BASE_URL`, and `T3_ORCHESTRATION_TOKEN` from the environment, calls only the exact-thread GET through `T3Client`, and prints a sanitized summary without messages, activity, plan text, plugin settings, base URL, or credentials. The summary may include the persisted thread runtime and interaction modes required by the gate. It performs no shell read or dispatch. Run it only when the operator has designated that exact non-SolarSim test thread; otherwise record N/A.

## D-10: Public repository, CI, and deterministic artifact
- **Covers**: NFR-5/AC-1, NFR-5/AC-2, NFR-5/AC-3, NFR-5/AC-4, NFR-5/AC-6, NFR-5/AC-7

Add MIT `LICENSE`, versioned `CHANGELOG.md`, manifest license/homepage, and a narrow `.gitignore`. Keep `.codex/specs/` tracked; ignore caches, coverage, virtual environments, editor noise, and `dist/`.

Add `.github/workflows/ci.yml`, triggered for pushes to `main` and pull requests, with `contents: read`. Pin `actions/checkout` to `3d3c42e5aac5ba805825da76410c181273ba90b1`, `actions/setup-python` to `5fda3b95a4ea91299a34e894583c3862153e4b97`, and `actions/upload-artifact` to `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`. Run units on Python 3.11/3.12/3.13 and Plugin Doctor on Python 3.11 against Hermes 0.20.4 commit `e624e9fde561e1add9388384012b295fde669ade` and 0.20.5 commit `fcbd1076a93841fa88855acce810e342a5b78101`. The package job declares `needs: [unit, doctor]`, rebuilds and verifies the artifact, then uploads only the archive and checksum.

Add standard-library deterministic release and verification scripts. The builder parses the manifest version and writes `dist/hermes-t3-control-1.1.0.tar.gz` plus `.sha256`. Archive members are sorted under one `hermes-t3-control/` prefix with fixed tar timestamps, numeric ownership, stable modes, and a gzip header with `mtime=0` and no source filename. A test builds twice into separate directories and requires byte identity. The verifier parses the detached digest, hashes the named archive, and fails closed on mismatch. The allowlist is runtime Python files, `plugin.yaml`, `README.md`, `LICENSE`, and `CHANGELOG.md`; tests, specs, CI, scripts, caches, absolute paths, and local configuration never enter the archive. The builder pins a current-user-owned, non-group/world-writable real output directory; rejects pre-existing symlink, hardlink, or non-regular artifact entries; stages exclusive no-follow regular files inside a random owner-private `0700` output-directory child on the same filesystem; preserves and revalidates staged inode identities immediately before directory-relative atomic replacement; and verifies each final no-follow, single-link regular file against its expected hash. Deterministic negative tests inject wrong-owner metadata and exercise a group/world-writable directory, requiring failure before staging or publication with existing targets preserved. Further tests prove neither artifact can overwrite an external sentinel and deterministic temporary-name substitution is rejected before publication.

## D-11: Operator documentation
- **Covers**: NFR-1/AC-3, NFR-5/AC-5

README uses repository-relative `python3`/Hermes commands, lists all eight tools, and documents Git installation disabled-by-default, profile-secret provisioning without token argv, numeric loopback `base_url`, and `default_runtime_mode: full-access` as an explicit operator choice. The warning states that full-access permits trusted provider work to execute and mutate without approval.

Document create with `initial_message`, explicit branch/worktree metadata, preserved options, same-thread resume, a two-call Plan then `t3_thread_implement_plan` example using the returned server plan ID, mode-update non-retroactivity, Plan to Build race, interrupt/stop races, bounds, exclusions, local gates, checksum verification, and release URL pattern.

## D-12: Release operation after code gate
- **Covers**: NFR-5/AC-1, NFR-5/AC-3, NFR-5/AC-4, NFR-5/AC-6

1. After the immutable implementation DAG passes, confirm current user authority still applies and re-establish the exact `gh api user` identity.
2. Commit explicit paths on `main`.
3. If `thetasigmaio/hermes-t3-control` is absent, create it public. If it unexpectedly exists, fail closed unless readback proves the same owner, expected visibility, and an empty repository or this exact already-pushed project.
4. Push `main` and wait for the exact commit's CI conclusion.
5. On CI failure, inspect the exact run, make only scoped repairs, rerun the full local gate on the new checkpoint, commit, push, and wait again. No tag or release is allowed until a specific commit is green.
6. Download that successful run's CI-produced archive/checksum and verify them locally.
7. Create annotated tag `v1.1.0` only at the green commit, push that exact tag object, and read back the remote tag object plus peeled commit before release creation.
8. Publish a GitHub release from the verified remote tag with the exact CI assets.
9. Read back repository visibility/default branch, commit, workflow, tag object and target, release, asset URLs/sizes, and downloaded checksum before reporting success.

These are separately authorized operational actions, not implementation-DAG tasks. A failed gate stops publication. No live Hermes installation or profile mutation is part of release.

## Alternatives and trade-offs

- A raw dispatch tool is rejected because it would bypass typed validation and make receipt/ambiguity misuse easy.
- Separate runtime and interaction tools would expand the LLM surface; one exactly-one-field mode tool remains typed and focused.
- Generic send does not accept `sourceProposedPlan`; the strict plan tool owns mandatory mode ordering and verification.
- Atomic worktree bootstrap is rejected because the installed HTTP path does not reliably execute its WebSocket-only bootstrap reactor. Explicit metadata does not create a worktree.
- The safe configuration fallback stays `approval-required`. This operator can configure `full-access` explicitly without silently weakening every installation.
- Version 1.1.0 is used because the local 1.0.0 plugin is already installed and this release adds tools/configuration, despite having no prior public Git history.

## Rollback

Code rollback is release/tag selection; no data migration exists. A future operator can install an earlier artifact or disable the plugin. Mode and turn mutations already accepted by T3 are event-sourced and are not rolled back by changing plugin files. This release does not alter the existing live installed copy.
