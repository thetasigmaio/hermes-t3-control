# Requirements: Hermes T3 Control 1.1 Release

- **Spec-ID**: `hermes-t3-control`
- **Profile**: Strict
- **Mode**: authoring
- **Discovery status**: `READY_FOR_REQUIREMENTS`

## Problem and outcome

The existing native Hermes plugin has a verified transport baseline but its first real use exposed configuration and workflow gaps. A thread created in `approval-required` mode requested approval before the operator changed it to `full-access`; T3 correctly did not retroactively cancel that existing approval. The plugin also loses checkout metadata and project model options, cannot update modes through typed tools, cannot perform a provenance-preserving Plan to Build transition, and is not yet a clean public GitHub release.

Version 1.1.0 must preserve the existing transport and credential controls while adding the smallest typed surface for consistent creation, mode changes, and same-thread plan implementation. The repository must use `.codex/specs/`, contain reproducible CI and release packaging, and be ready for separately authorized GitHub publication after all local gates pass.

## Terminology

| Canonical term | Definition | Do not use for this concept |
|---|---|---|
| persisted thread mode | The `runtimeMode` or `interactionMode` visible on exact T3 thread readback and used by T3 when accepting a later turn | turn-command override |
| initial turn | The optional first `thread.turn.start` dispatched by `t3_thread_create` only after the new thread is read back | bootstrap turn |
| logical mutation | One immutable T3 command payload and generated `commandId`; retries reuse its byte-identical body and ID | new command |
| exact-target readback | `GET /api/orchestration/threads/:threadId` for the exact mutated thread | shell inference, database inspection |
| source proposed plan | The T3-native `{threadId, planId}` reference on an implementation turn | plan text pasted into a prompt |
| Plan to Build transition | Same-thread interaction-mode change to `default`, verified readback, then a provenance-bearing implementation turn | ordinary send, mode-only update |
| active approval | An approval already created by provider work for a started turn | persisted runtime mode |

## Evidence register

| ID | Kind | Locator | Supported fact | Snapshot |
|---|---|---|---|---|
| E-1 | user | current follow-up request | Runtime incident, requested metadata/model/mode/plan behavior, `.codex/specs/` convention, release hygiene, GitHub publication authority, and required readbacks | conversation turn on 2026-08-21 |
| E-2 | repository | `plugin.yaml`, `client.py`, `tools.py`, `schemas.py`, `tests/` | Unreleased 1.0.0 baseline has six tools, hardened transport, and 46 passing tests | file hashes observed 2026-08-21 |
| E-3 | policy | active global Codex `AGENTS.md` | Global scope, secret, Git, release, delegation, and verification requirements | SHA-256 `592c57a3cd09ef54fbea81e2c0e9bf34ee078466cf8c126471136219944433f8` |
| E-4 | policy | applicable Projects `AGENTS.md` | Projects repository placement and GitHub source-of-truth convention | SHA-256 `3ef0d02c919bd3111c71fc4c1fe8ab548c4ff5652a836a89f5baa745108a96f3` |
| E-5 | upstream | `https://hermes-agent.nousresearch.com/docs/developer-guide/plugins` and `https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.19` | Native directory plugin contract, profile-relative settings, Plugin Doctor, Hermes 0.20.5, and Python 3.11 through 3.13 support | accessed 2026-08-21; tag commit `fcbd1076a93841fa88855acce810e342a5b78101` |
| E-6 | local source | installed Hermes 0.20.4 sources `hermes_cli/plugins.py`, `hermes_cli/plugin_dev.py`, `agent/secret_scope.py` | Manifest v2/API v1, advisory config schema, per-plugin `ctx.get_config`, passive registration, Doctor, and profile-safe secret lookup behavior | local Hermes reports 0.20.4 at `ee000768`; inspected 2026-08-21 |
| E-7 | local contract | installed T3 server `0.0.34-nightly.20260820.1141` sources `packages/contracts/src/orchestration.ts`, `apps/server/src/orchestration/http.ts`, `decider.ts`, `ProjectionPipeline.ts`, `ProviderRuntimeIngestion.ts` | Command schemas, persisted-mode turn semantics, model options, checkout metadata, plan provenance, scopes, receipts, and HTTP endpoints | bundled server/map SHA-256 `ff22a6ea4ebcd7a81c7f1c56f6159c543c3567ffe9a8c386b276b03598fe732b` and `ff4b2d4a3bfbba696ab155d843f6394add63c2021ae8ee681b3c8a029f91498c` |
| E-8 | local source | installed `t3code-cli` `src/service.ts`, `src/config.ts`; installed T3 client sources `proposedPlan.ts`, `ChatView.tsx` | Current-checkout create then turn ordering, selection-specific model options, default full-access behavior, and native same-thread Plan to Build prompt/order | t3code-cli 0.1.0 at `2a55bb2fc220679230d8eed92ce24661dfdb4ca1`; inspected 2026-08-21 |
| E-9 | command | `python3 -B -m unittest discover -s tests -v`; `hermes plugins doctor . --ci` | Current local unit/integration and real-loader gates are available | 46 tests and six-tool Doctor baseline passed 2026-08-21 |
| E-10 | external read | authenticated `gh api user`, repository lookup, official Actions repositories | Active GitHub identity is `thetasigmaio`, target repository name is available, and immutable action revisions can be pinned | read-only inspection 2026-08-21 |
| E-11 | independent audit | integrated 1.1.0 release-candidate review | Pre-request active-token rejection, ambiguity history across accepted retries, release-output link safety, and complete instruction binding are required release blockers | frozen-tree review on 2026-08-21; findings `HT3-SEC-001`, `HT3-COR-002`, `HT3-SEC-003`, `HT3-AUD-004` |
| E-12 | independent audit | final integrated release-candidate review | Plan to Build must positively prove acceptance of its mandatory mode-transition command, and release staging must bind private temporary-file identities through verified publication | frozen-tree review on 2026-08-21; one High Plan-transition finding and one Medium staging-race finding |

## Functional requirements

### FR-1: Typed native tool surface
- **Source**: E-1, E-5, E-7

The plugin MUST register exactly eight non-overriding tools: `t3_threads`, `t3_thread_read`, `t3_thread_create`, `t3_thread_send`, `t3_thread_set_mode`, `t3_thread_implement_plan`, `t3_turn_interrupt`, and `t3_session_stop`.

#### FR-1/AC-1: Manifest discovery, import, and registration expose exactly the eight declared tools and no raw-dispatch tool

#### FR-1/AC-2: Import and `register(ctx)` perform no network request or mutation

### FR-2: Thread list and exact detail reads
- **Source**: E-1, E-7

Read tools MUST use only the supported shell and exact-thread HTTP endpoints and MUST validate the response fields they consume.

#### FR-2/AC-1: `t3_threads` returns a validated shell snapshot with thread statuses

#### FR-2/AC-2: `t3_thread_read` returns the exact requested thread, bounded pagination, proposed plans, and turn provenance

### FR-3: Consistent creation and initial turn
- **Source**: E-1, E-7, E-8

`t3_thread_create` MUST resolve one runtime mode before dispatching `thread.create`: explicit tool input, otherwise validated plugin setting `default_runtime_mode`, otherwise the conservative fallback `approval-required`.

#### FR-3/AC-1: The manifest exposes non-secret `default_runtime_mode` and handlers reject unsupported configured values before HTTP

#### FR-3/AC-2: Create readback exactly matches the resolved runtime mode and interaction mode before any optional initial turn is dispatched

#### FR-3/AC-3: If `initial_message` is supplied, the plugin constructs the first-turn command from the same read-back model/runtime/interaction values and succeeds only after its exact message and final observed settings appear on the same thread; provider acceptance under those values is best-effort because T3 has no atomic expected-mode guard

#### FR-3/AC-4: A `full-access` create result explicitly warns that trusted provider work may execute, modify, or delete without approval

#### FR-3/AC-5: The initial-turn result discloses that T3 has no atomic expected-mode guard and that a concurrent external mode change between readback and turn acceptance cannot be excluded

### FR-4: Model and checkout metadata preservation
- **Source**: E-1, E-7, E-8

Creation MUST preserve T3-visible model options and caller-supplied local checkout metadata.

#### FR-4/AC-1: When `model_options` is omitted, a complete explicit instance/model pair equal to the project default preserves that selection's canonical `options`

#### FR-4/AC-2: If no project default exists, creation requires a complete explicit instance/model pair

#### FR-4/AC-3: `branch` and `worktree_path` are bounded optional inputs mapped exactly to `branch` and `worktreePath`; omission maps to null

#### FR-4/AC-4: Create succeeds only after exact readback matches full model selection, branch, and worktree path

#### FR-4/AC-5: A different explicit instance/model pair never inherits selection-specific options; optional explicit `model_options` are bounded, typed, uniquely keyed, and mapped exactly

### FR-5: Same-thread send and resume
- **Source**: E-1, E-7, E-8

`t3_thread_send` MUST start a new turn on the supplied existing thread, construct the command from that thread's pre-read persisted model/runtime/interaction settings, and verify the final observed settings. Actual provider acceptance under the pre-read modes is best-effort because T3 exposes no atomic expected-mode guard.

#### FR-5/AC-1: Each send has fresh command/message UUIDv4 identities and never re-resolves the plugin creation default

#### FR-5/AC-2: Send succeeds only after the exact message and unchanged persisted settings are read back

#### FR-5/AC-3: Send after provider-session stop remains a new turn on the same T3 thread

#### FR-5/AC-4: Send reports that its pre-read and post-read checks cannot rule out a transient concurrent mode change before provider acceptance because T3 exposes no atomic expected-mode precondition

### FR-6: Verified mode updates and non-retroactivity
- **Source**: E-1, E-7

`t3_thread_set_mode` MUST accept exactly one typed runtime or interaction mode change and MUST dispatch the matching dedicated T3 command.

#### FR-6/AC-1: Runtime changes use `thread.runtime-mode.set`; interaction changes use `thread.interaction-mode.set`; unsupported or dual changes fail before dispatch

#### FR-6/AC-2: A mode change succeeds only after the exact persisted thread field matches readback at or beyond the accepted sequence

#### FR-6/AC-3: A verified no-op returns without dispatch when the requested mode already matches

#### FR-6/AC-4: If a turn is active, the result states that changing runtime does not cancel or retroactively authorize an approval already created for that turn

### FR-7: Strict same-thread Plan to Build
- **Source**: E-1, E-7, E-8, E-12

`t3_thread_implement_plan` MUST implement an existing unimplemented T3 proposed plan on the same thread without accepting caller-authored plan text or exposing raw dispatch.

#### FR-7/AC-1: Before mutation, the tool requires an exact matching unimplemented `plan_id`, a non-deleted/non-archived thread, and no active or running turn

#### FR-7/AC-2: The tool always dispatches `thread.interaction-mode.set` with `default` and observes exact readback before starting implementation

#### FR-7/AC-3: The implementation message is the native `PLEASE IMPLEMENT THIS PLAN:\n` prefix plus the stored trimmed `planMarkdown`

#### FR-7/AC-4: The implementation turn carries `sourceProposedPlan: {threadId, planId}`, fresh identities, the stored model/runtime settings, and interaction mode `default`

#### FR-7/AC-5: Success requires exact message readback, interaction mode `default`, exact `latestTurn.sourceProposedPlan`, and source-plan `implementedAt` plus same-thread `implementationThreadId`

#### FR-7/AC-6: If mode transition succeeds but turn start fails, the error identifies the completed phase and mode command without attempting rollback

#### FR-7/AC-7: The result states that preconditions and readbacks are non-atomic, so success proves observed ordering and provenance but not at-most-once implementation or absence of a transient concurrent mode change

#### FR-7/AC-8: The mandatory mode phase succeeds only with a returned accepted dispatch sequence; after an ambiguous attempt, readback showing either a pre-existing or newly observed `default` mode cannot by itself authorize the implementation turn

### FR-8: Active-turn interruption
- **Source**: E-2, E-7

The existing best-effort interrupt behavior MUST remain unchanged and MUST disclose the lack of an atomic expected-turn guard.

#### FR-8/AC-1: Missing running active turn fails without dispatch

#### FR-8/AC-2: Success requires the captured turn terminal and no active turn; a newer turn produces `concurrent_state_change`

### FR-9: Provider-session stop
- **Source**: E-2, E-7

The existing best-effort provider-session stop behavior MUST remain unchanged and MUST not delete or replace the thread.

#### FR-9/AC-1: Already-stopped inactive state returns verified success without dispatch

#### FR-9/AC-2: A stop mutation succeeds only after exact readback shows stopped and inactive

### FR-10: Mutation verification and ambiguous recovery
- **Source**: E-1, E-2, E-7, E-12

Every dispatched mutation MUST retain the hardened exact-target readback and sticky ambiguity semantics.

#### FR-10/AC-1: Accepted dispatch is verified at or beyond its returned sequence

#### FR-10/AC-2: Ambiguous dispatch reads the exact target before byte-identical redispatch with the same ID

#### FR-10/AC-3: Once transmission is ambiguous, no later terminal response downgrades the result before predicate verification

#### FR-10/AC-4: An ambiguous attempt followed by an accepted retry and verified predicate returns recovered success with the accepted sequence and complete attempt count

#### FR-10/AC-5: After an ambiguous attempt, accepted-retry poll expiry remains mutation ambiguous; without prior ambiguity it is verification failed, while a proven newer-turn race remains concurrent state change in either case

#### FR-10/AC-6: An internal acceptance-required mutation still performs exact readback after ambiguity but retries the same immutable command until an accepted sequence is returned or stops as mutation ambiguous without reporting predicate-only recovery

### FR-11: Command identity and validation
- **Source**: E-1, E-2, E-7

Each logical mutation MUST use one fresh UUIDv4 command ID and one immutable canonical body; public schemas MUST expose no identity override.

#### FR-11/AC-1: Separate commands, including the two Plan to Build phases, use distinct fresh IDs

#### FR-11/AC-2: Unknown, malformed, over-limit, or unsafe public/configuration input fails before HTTP

#### FR-11/AC-3: Message text remains limited to 120000 JavaScript UTF-16 code units and thread IDs remain one encoded path segment

### FR-12: Response and error contract
- **Source**: E-2, E-7

The client MUST preserve additive successful fields while validating every field used for behavior, including proposed plans and provenance, and MUST return stable sanitized JSON errors.

#### FR-12/AC-1: Wrong target/session identity, malformed plans/provenance, reflected credentials, and invalid required fields fail closed

#### FR-12/AC-2: Additive non-secret fields remain preserved

#### FR-12/AC-3: Existing HTTP, network, size, timeout, schema, conflict, race, ambiguity, verification, and internal error classes remain deterministic and sanitized

## Mutation error precedence

After dispatch transmission may have begun, ambiguity context overrides the base error. Until the exact mutation predicate is verified, network, HTTP 408/429/5xx, oversized or malformed dispatch response, later terminal dispatch failure, any secondary readback failure, or an accepted retry whose bounded predicate is not observed MUST return `mutation_ambiguous`, `retryable: false`, `outcome_ambiguous: true`, and the original command ID. An accepted command whose bounded predicate is not observed returns `verification_failed` only when no earlier dispatch attempt was ambiguous. A detected newer-turn interrupt race returns `concurrent_state_change`. Eventual predicate verification after an ambiguous attempt reports recovered ambiguity accurately unless the caller marks that mutation acceptance-required; in that case readback still precedes retry, but only a returned accepted sequence can complete the mutation. Composite create/initial-turn and Plan to Build errors MUST identify completed phases without changing those top-level semantics.

## Non-functional requirements

### NFR-1: Credential and least-privilege boundary
- **Source**: E-1, E-2, E-6, E-7

The plugin MUST obtain `T3_ORCHESTRATION_TOKEN` only through Hermes' profile-safe secret resolver and MUST obtain non-secret settings only through its plugin context.

#### NFR-1/AC-1: Real or operator-provisioned credentials never appear in public inputs, settings, source, persisted state, argv, logs, errors, tests, artifacts, or tool results, and tests use only runtime-generated disposable in-memory values

#### NFR-1/AC-2: Missing or invalid credential/base URL/configured mode fails before network access

#### NFR-1/AC-3: Documentation requires only `orchestration:read` and `orchestration:operate`

#### NFR-1/AC-4: The active token is rejected before any HTTP request if it occurs in any normalized path or query value, or recursively in any mutation-command key or value, without reflecting the token in the error

### NFR-2: Bounded loopback-only transport
- **Source**: E-1, E-2, E-7

The standard-library client MUST remain numeric-loopback-only, authenticated, direct, deadline/size bounded, no-proxy, and no-redirect.

#### NFR-2/AC-1: Only `GET /api/orchestration/shell`, exact-thread GET, and `POST /api/orchestration/dispatch` are reachable

#### NFR-2/AC-2: Existing request, mutation, poll, retry, body, response, URL, identifier, cursor, and message bounds remain enforced and tested

#### NFR-2/AC-3: Successful responses recursively reject reflected active credentials

### NFR-3: System and repository isolation
- **Source**: E-1, E-3, E-4

Plugin/runtime code MUST NOT inspect or modify T3 SQLite, T3/Codex JSONL, process credentials, Hermes/T3/Codex core, or another thread as a side channel.

#### NFR-3/AC-1: Static and behavioral tests detect forbidden persistence/process/credential access and external endpoint expansion

#### NFR-3/AC-2: Repository specifications live only under `.codex/specs/`; `.claude/specs/` is absent

### NFR-4: Regression and compatibility verification
- **Source**: E-1, E-5, E-7, E-9

The repository MUST use mocked loopback integration tests for every new command and real-use failure while retaining all existing safety regression coverage.

#### NFR-4/AC-1: Tests cover configured/explicit runtime precedence, full-access warning, create plus initial turn consistency, metadata/options, non-retroactive approvals, both mode commands, Plan to Build ordering/provenance/failures, and all existing transport safeguards

#### NFR-4/AC-2: Tests use only ephemeral loopback servers and runtime-generated in-memory credentials

#### NFR-4/AC-3: Plugin Doctor passes against the supported native manifest/registration surface

#### NFR-4/AC-4: A conditional read-only live-smoke command reads only one operator-designated isolated exact thread, obtains its token only from the environment, prints no messages or secrets, and is skipped unless that target and current runtime authority are explicitly established

### NFR-5: Public release hygiene and reproducibility
- **Source**: E-1, E-5, E-10, E-12

The repository MUST be a clean MIT-licensed 1.1.0 public-release source with reproducible CI and release artifacts.

#### NFR-5/AC-1: Manifest, changelog, license, homepage, README, and artifact version agree on 1.1.0 and MIT

#### NFR-5/AC-2: `.gitignore` excludes credentials, caches, virtual environments, coverage, and generated release output without hiding `.codex/specs/`

#### NFR-5/AC-3: CI uses least-privilege permissions, full-SHA-pinned actions, Python 3.11/3.12/3.13 unit tests, and Plugin Doctor against pinned Hermes 0.20.4 and 0.20.5 revisions

#### NFR-5/AC-4: A deterministic allowlisted plugin archive and detached SHA-256 contain no absolute paths, caches, tests, specs, CI metadata, or credentials

#### NFR-5/AC-5: README documents safe installation/configuration, all eight tools, a native Plan to Build example, the full-access warning, non-retroactive approval behavior, and exact local/release verification commands

#### NFR-5/AC-6: The builder validates the real output directory, rejects pre-existing symlink, multi-link regular, directory, FIFO, or other non-regular artifact targets, and uses exclusive no-follow regular temporary files in a private output-directory child on the same filesystem plus directory-relative atomic replacement without modifying an external sentinel

#### NFR-5/AC-7: The builder requires a current-user-owned output directory with no group/world write access, stages inside an owner-private directory, preserves and revalidates every temporary inode immediately before atomic publication, and verifies each final no-follow single-link regular file against its expected hash; deterministic name-rebinding tests fail without publishing substituted bytes

## Constraints and exclusions

- No T3, Codex, Hermes core, or live installed-plugin copy changes are part of implementation.
- No token issuance, token persistence, SQLite/JSONL access, process credential extraction, raw-dispatch tool, WebSocket control, worktree creation, attachment upload, approval response, or thread/project deletion tool is in scope.
- `default_runtime_mode` falls back to `approval-required`; this operator can deliberately configure `full-access`, but release code does not silently change the live Hermes profile.
- Plan to Build is same-thread only and operates on a server-returned unimplemented plan ID.
- T3 has no atomic expected-mode, expected-plan-state, idle-state, or at-most-once implementation precondition. Readbacks prove only observed state: a concurrent client can transiently change a mode, start duplicate implementation work, or change state between a check and command acceptance without every race being detectable afterward.
- Interrupt and stop retain their documented non-atomic provider-session race.
- A live smoke is conditional on an operator-designated isolated non-SolarSim thread and performs one exact read with no provider or persistence mutation; otherwise it is skipped with evidence.
- Commit, repository creation, push, CI observation, tag, and release publication occur only after the immutable implementation DAG passes, current user authority and instructions still permit them, and exact runtime targets are rechecked; they are operational release actions outside the DAG.

## Open decisions

None.
