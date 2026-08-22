# Requirements: Hermes T3 Control portability qualification

- **Spec-ID**: `hermes-t3-control-portability`
- **Mode**: authoring
- **Profile**: Strict
- **Phase**: v1.3 Phase A qualification and test readiness
- **Baseline**: `a76b34d9cc1f1a5732f7f264003ad01d9eea7fe5` (`v1.2.1`)
- **Discovery checkpoint**: `READY_FOR_REQUIREMENTS`

## Problem and outcome

Hermes T3 Control v1.2.1 supports Linux/WSL2 local authentication. Its safety proof binds a short-lived session to one live T3 process through Linux `/proc`, `pidfd`, and listener ownership. Native macOS and Windows lack that proof. The current T3 session CLI opens auth state by base directory and does not bind issuance to the running server, so it is not a safe native replacement.

Phase A makes the unsupported boundary explicit, prepares portable CI, supported-install and artifact-equivalence gates, preserves the Linux disposable live gate, and records the exact missing native external-token fixture contract. It does not design or implement native `local-cli` authentication or claim native external-token evidence. A later Phase B specification may add native local auth only after primary upstream documentation supplies the complete running-runtime contract listed in FR-2. Until then, native Windows and macOS local authentication remain unsupported, native external-token compatibility remains unverified, the ten tools remain unchanged, and no v1.3 feature release is authorized.

## Evidence register

| ID | Kind | Locator | Supported fact | Snapshot |
|---|---|---|---|---|
| E-1 | user | current productization request | Native support requires a safe maintainable auth path and real CI/E2E; unsupported platforms or providers must not be claimed | 2026-08-22 conversation turn |
| E-2 | policy | `/home/jaymade/.codex/AGENTS.md` | Preserve security boundaries, use evidence, run full gates, and separate specifications from implementation authority | SHA-256 `66b4ed21e42a52ef36a10c9ffcc2598d1ab4543c601294f03bfd33fcc8bc378a` |
| E-3 | repository | `auth.py#resolve_local_runtime`, `_pinned_runtime_process`, `operation_client` | Current Linux default auth relies on `/proc`, `os.pidfd_open`, listener ownership, a five-minute issue, and finally revoke | baseline `a76b34d` |
| E-4 | repository | `client.py#T3Client` | HTTP transport enforces numeric loopback, disables proxies and redirects, bounds responses, and preflights reflected credentials | baseline `a76b34d` |
| E-5 | live T3 contract | `t3 auth session issue/list/revoke --help`; live `server.asar` source map `src/cli/auth.ts` and `src/cliAuthFormat.ts` | Current CLI opens auth state by base directory and exposes no running-process, environment, version, origin, nonce, channel-binding, or least-scope runtime lease | T3 `0.0.34-nightly.20260820.1141`, inspected 2026-08-22 |
| E-6 | installed adapter | `~/.local/share/t3code-cli/src/process.ts`, `runtime.ts`, `api.ts` | The available adapter may drift to another T3 version, follows redirects, and does not confirm revoke; it is not a safe support contract | inspected 2026-08-22 |
| E-7 | repository | `docs/compatibility.md#Operating-system-status` | v1.2.1 supports WSL2, limits Linux evidence, and does not support native Windows or macOS | baseline `a76b34d` |
| E-8 | repository | `.github/workflows/ci.yml` | Current Python, supported-install, Doctor, and package jobs run only on Ubuntu | SHA-256 `8e24a30ac7c21ec4565f2653ab58a41b59ac1d62825df60429d2f6d7fb2456c3` |
| E-9 | repository | `tests/test_auth.py`, `tests/test_supported_install.py`, `tests/test_readme.py`, `tests/test_release.py`, `scripts/build_release.py`, `scripts/verify_release.py` | Existing focused, full, supported-install, documentation, deterministic-build, and checksum gates are executable; supported install uses an immutable Git ref and the release builder is intentionally POSIX-only | baseline `a76b34d` |
| E-10 | live release | active Hermes profile and public `v1.2.1` tag | Public v1.2.1 is active from its immutable commit with ten tools and remains the rollback baseline | verified 2026-08-22 |
| E-11 | repository | `scripts/live_smoke.py` | The current external-token smoke calls `T3Client` directly and does not prove fresh Hermes/plugin consumer behavior or native credential cleanup | baseline `a76b34d` |

## Canonical terminology

| Canonical term | Definition | Do not use for this concept |
|---|---|---|
| upstream contract evidence | Primary, versioned T3 documentation plus executable conformance evidence for every FR-2 input | proposed protocol, source-code guess |
| platform packaging gate | Cross-platform Python, supported-install, fresh-process, and release-payload equivalence evidence without a native local-auth claim | native support |
| platform acceptance gate | Platform packaging evidence plus a fresh-Hermes disposable live authentication/provider completion and cleanup proof | unit support, probable compatibility |
| external-token mode | Explicit operator-provisioned authentication without automatic local runtime binding or plugin-owned session lifecycle | native local support |

## Binding requirements

### FR-1: Native local-auth claims fail closed

- **Source**: E-1, E-3, E-5, E-7, E-10

Phase A MUST preserve native Windows and macOS `local-cli` as unsupported.

#### FR-1/AC-1 Unsupported native runtime

If `local-cli` runs on native Windows or macOS without a qualified upstream contract, the handler MUST return one actionable sanitized configuration error before session issuance or HTTP.

#### FR-1/AC-2 Compatibility copy

Documentation and after-install guidance MUST keep native Windows and macOS local authentication in `Not supported yet` state and MUST NOT present `external-token` evidence as equivalent.

### FR-2: Qualify the upstream contract before Phase B

- **Source**: E-1, E-3, E-4, E-5, E-6

A Phase B native-auth specification MUST NOT be frozen until primary upstream T3 documentation and executable conformance evidence provide every input below for each target OS.

#### FR-2/AC-1 Launcher identity

The Phase A checklist MUST require primary evidence to define the supported installation locator, one exact directly executable launcher type, minimum server and CLI versions, and version/capability negotiation behavior without `PATH`, package-cache, app-bundle, or `npx latest` discovery.

#### FR-2/AC-2 Runtime channel binding

The Phase A checklist MUST require primary evidence to define an owner-only live-server control channel, OS peer-identity checks, remote-user rejection, runtime identity binding, and the behavior when the server or channel endpoint changes.

#### FR-2/AC-3 Lease lifecycle

The Phase A checklist MUST require primary evidence to define complete begin, success, validation-error, revoke, confirmed-absence, timeout, EOF, and process-crash schemas; failure-atomic issuance; channel-close auto-revoke; and a maximum lease of five minutes.

#### FR-2/AC-4 Pre-Authorization transport binding

The Phase A checklist MUST require primary evidence to define how the plugin cryptographically binds the numeric-loopback orchestration connection to the attested live runtime before writing a bearer, and how the authenticated environment descriptor proves the same runtime afterward.

#### FR-2/AC-5 Exact scopes

The Phase A checklist MUST require primary evidence to publish the exact minimum ordered scope tuple required by the ten orchestration tools, without unrelated administrative scopes.

#### FR-2/AC-6 Missing or ambiguous input

If any FR-2 input is absent, contradictory, or cannot pass a native conformance probe, Phase A MUST record the sanitized dependency blocker and MUST NOT add a native auth adapter, support claim, or feature version.

### FR-3: Build honest cross-platform evidence

- **Source**: E-1, E-7, E-8, E-9, E-11

Phase A MUST separate packaging evidence, external-token consumer evidence, and native local-auth evidence.

#### FR-3/AC-1 Cross-platform suite

Python 3.11, 3.12, and 3.13 tests MUST pass with warnings as errors on Ubuntu, native macOS, and native Windows; non-POSIX cells MUST assert the intentional Linux-only builder boundary instead of skipping or requiring a successful archive build.

#### FR-3/AC-2 Supported install and payload equivalence

Hermes 0.20.4 and 0.20.5 frozen-lock gates MUST fail if the substantive test is skipped and MUST prove scanner-on disabled install from a hermetic immutable snapshot commit of the exact integrated Phase A tree, Doctor before enable, explicit no-override enable, a fresh process with ten callable tools, and byte equality between every installed runtime payload and the checksum-verified deterministic archive built on Linux.

#### FR-3/AC-3 External-token fixture dependency

Until primary evidence identifies one protected fixture interface that can issue a bounded read-only session, return its session ID/token/expiry, revoke that exact session, confirm absence, and create/delete run-owned T3 resources, Phase A MUST record `external_token_fixture_unavailable` and MUST keep native external-token compatibility unverified.

#### FR-3/AC-4 Claim separation

Passing FR-3/AC-1 and FR-3/AC-2 MAY qualify only packaging. Neither the unavailable external-token fixture nor packaging evidence qualifies native authentication; native `local-cli` and native `external-token` MUST remain unsupported or unverified until their own later acceptance gate passes.

#### FR-3/AC-5 Dedicated target isolation

Every live gate MUST require a non-secret operator-provided fixture manifest for one dedicated non-production project/workspace/thread, explicit current mutation authority, and one platform-specific fixture lock. It MUST reject missing, ambiguous, production-named, or identity-mismatched targets; restrict every list/read to the exact manifest selectors with `require_one`; use an unpredictable run ID in the harmless message; and remove only local temporary resources created by that run. It MUST NOT create or delete T3 projects or threads without a separately supported lifecycle contract.

### FR-4: Preserve the released product contract

- **Source**: E-1, E-3, E-7, E-9, E-10

Phase A MUST keep the public v1.2.1 plugin behavior unchanged.

#### FR-4/AC-1 Ten-tool parity

Every fresh-process gate MUST register exactly the existing ten synchronous non-overriding tools with unchanged names, schemas, defaults, and result shapes.

#### FR-4/AC-2 Linux regression

The Linux `/proc` and `pidfd` path MUST continue to pass its existing auth, supported-install, scanner, and disposable live gates without selecting any Phase A native adapter.

#### FR-4/AC-3 Scanner-safe implementation tree

Any Phase A implementation branch and deterministic artifact MUST contain no `.codex/specs` or `.claude/specs` path and MUST pass the default Hermes scanner without `--force` or exclusions.

### NFR-1: Keep the trust boundary small

- **Source**: E-1, E-2, E-3, E-4, E-6

Phase A MUST add no proxy, shell invocation, auth-database access, platform process scraper, credential cache, background daemon, dependency, or unbounded output.

#### NFR-1/AC-1 Bounded child processes

Tests MUST prove every new runner uses an allowlisted environment, bounded output, a finite timeout, process-tree termination, sanitized errors, and no inherited unrelated credentials.

### NFR-2: Keep Phase A, Phase B, and release authority distinct

- **Source**: E-1, E-2, E-5, E-9, E-10

Phase A MUST NOT imply that the missing native-auth design is decided.

#### NFR-2/AC-1 Phase B re-specification

After all FR-2 evidence exists, requirements, design, tasks, and a detached audit receipt MUST be newly frozen and independently audited against the exact upstream contract before native-auth implementation begins.

#### NFR-2/AC-2 Release boundary

Phase A MUST NOT change the feature version or a native support claim solely because contract or packaging tests exist; release, publishing, and rollout require current authority and the applicable evidence-backed acceptance gates.

## Dependencies, exclusions, and decisions

- **Unavailable dependency**: Current T3 `0.0.34-nightly.20260820.1141` does not supply FR-2 upstream contract evidence. Phase A is executable; Phase B is intentionally not specified or authorized.
- **Unavailable dependency**: No evidence-backed protected native fixture currently supplies the exact least-scope issue/revoke/list and run-owned project/thread lifecycle interface required by FR-3/AC-3; native external-token acceptance remains unexecuted and unclaimed.
- **Exclusion**: Phase A does not implement native local authentication, native external-token acceptance, add tools, change provider claims, or qualify a non-Codex provider.
- **Exclusion**: This specification remains on its dedicated branch and never enters a scanner-visible release commit.
- **Exclusion**: This specification does not authorize native live runs, external credential issuance, upstream changes, publishing, or rollout; those actions require current runtime authority.
- **Open decisions**: None within Phase A. Phase B security inputs are explicitly unavailable and require a new frozen specification rather than an assumption in this one.
