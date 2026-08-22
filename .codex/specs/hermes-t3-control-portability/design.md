# Design: Hermes T3 Control portability qualification

- **Spec-ID**: `hermes-t3-control-portability`
- **Profile**: Strict
- **Phase**: v1.3 Phase A qualification and test readiness
- **Baseline**: `a76b34d9cc1f1a5732f7f264003ad01d9eea7fe5`

## Scope boundary

`auth.operation_client` already owns mode selection, public-argument preflight, environment validation, and cleanup. `T3Client` already owns the bounded numeric-loopback HTTP boundary and accepted-mutation reconciliation. The Linux implementation has a process/listener binding that current native Windows and macOS T3 interfaces do not provide.

Phase A does not invent that missing interface. It hardens the unsupported result and constructs evidence gates that distinguish packaging, external-token consumer behavior, and native local authentication. The exact native adapter architecture is a Phase B decision that requires the primary upstream contract evidence in FR-2 and a new frozen audit candidate.

## Architecture

### D-1: Explicit fail-closed platform selector

- **Covers**: FR-1/AC-1, FR-1/AC-2, FR-2/AC-6, FR-4/AC-2, NFR-2/AC-2

Keep `operation_client` as the single auth selector:

1. Explicit `external-token` selects the existing external client.
2. Linux `local-cli` selects the existing `/proc` and `pidfd` implementation.
3. Native `darwin` or `win32` `local-cli` returns one sanitized unsupported configuration result before issuing a session or constructing `T3Client`.
4. No native branch falls back to a profile token, direct database issuance, process scraping, `PATH`, `npx`, a proxy, or an unverified adapter.

Tests patch the platform boundary and instrument session issuance plus HTTP construction so zero side effects are observable. Documentation tests parse the compatibility matrix and after-install text and require the same unsupported state.

### D-2: Upstream contract qualification record

- **Covers**: FR-2/AC-1, FR-2/AC-2, FR-2/AC-3, FR-2/AC-4, FR-2/AC-5, FR-2/AC-6, NFR-2/AC-1

Phase A records the current result as `contract_unavailable`. A future qualification pass must collect primary upstream documentation, exact released versions, and native conformance output for all FR-2 inputs. The qualification record is evidence about the upstream contract, not a plugin protocol design. It contains:

- documentation locators and content hashes;
- the exact supported per-OS launcher locator and executable type;
- version and capability negotiation plus every request, success, error, cleanup, and absence schema;
- owner and peer-identity rules for the live-server control channel;
- issuance atomicity, channel-close/process-crash revocation, and maximum TTL behavior;
- the pre-Authorization runtime/connection binding and post-authentication environment proof;
- the exact least-privilege scope tuple; and
- native conformance commands and sanitized PASS/FAIL results.

Missing, inferred, source-map-only, or contradictory values produce `contract_unavailable`; they are never filled with a proposed Unix socket, named pipe, frame format, certificate scheme, field name, locator, or version. After a complete PASS, the author must re-run discovery and create a new Strict requirements/design/tasks candidate bound to those exact bytes. Phase A's receipt cannot authorize Phase B.

### D-3: Portable CI and supported-install topology

- **Covers**: FR-3/AC-1, FR-3/AC-2, FR-4/AC-1, FR-4/AC-2, FR-4/AC-3, NFR-1/AC-1

CI separates the Linux-built deterministic artifact from native packaging checks:

1. Ubuntu invokes one deterministic-build verification mode that builds into two clean private temporary directories, compares archive and checksum bytes, and copies only the accepted pair into `dist` for later jobs.
2. Ubuntu, macOS, and Windows run Python 3.11-3.13 with warnings as errors. On non-POSIX hosts, release tests assert the documented builder rejection and still validate the allowlist, metadata contract, manifest, and documentation without attempting a successful archive build.
3. Before install verification, each OS job checks out Hermes 0.20.4 at `e624e9fde561e1add9388384012b295fde669ade` and 0.20.5 at `fcbd1076a93841fa88855acce810e342a5b78101` into distinct declared `.ci` paths, uses `astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9` with uv `0.12.0`, and runs `uv sync --frozen` in each tree. No gate resolves a Hermes dependency graph itself.
4. A platform-neutral runner receives both canonical Hermes project paths and expected commits explicitly, validates each HEAD, lockfile, and project-local interpreter, creates a mode-private HOME/Hermes/XDG/Git environment, sets the substantive supported-install opt-in itself, and treats zero executed install cases or any skip as failure.
5. The runner creates a hermetic bare Git remote and immutable snapshot commit from the exact integrated Phase A tree, configures only the child Git URL rewrite needed for the supported owner/repository pinned-ref install, and never pushes the snapshot. It keeps scanner-on install disabled, runs Doctor, enables with no override, starts a fresh process, invokes all ten tools with sanitized invalid inputs, and removes the plugin.
6. The runner receives the Linux artifact by immutable workflow digest, verifies its detached checksum before extraction, and byte-compares every release allowlist payload against the installed snapshot. A mismatch fails the gate, so native execution covers exactly the same runtime bytes even though the supported installer consumes Git.

The runner accepts no ambient secret-bearing environment. It uses Python APIs for platform path, permission, subprocess, and temporary-directory behavior; it does not embed `/bin/false`, `/usr/bin/git`, POSIX-only chmod assumptions, or shell environment syntax.

### D-4: Linux live acceptance and native external-token boundary

- **Covers**: FR-3/AC-3, FR-3/AC-4, FR-3/AC-5, FR-4/AC-1, FR-4/AC-2, NFR-1/AC-1

The executable Phase A live wrapper supports only the existing Linux `local-cli` path. It requires an operator-provided, non-secret fixture manifest containing the canonical project, workspace, thread ID, expected neutral title marker, and an assertion that the target is a dedicated non-production fixture. The command also requires current explicit authority to send one harmless message. The wrapper refuses a missing field, symlinked manifest, known production name, selector/ID mismatch, zero or multiple matches, or a target with pending approval/input or an active turn.

Fresh Hermes calls `t3_threads` with the exact manifest project and workspace selectors plus `require_one`, then compares the returned project, workspace, thread ID, and title marker before exact read or mutation. The gate creates an unpredictable run ID only for its message identity, exercises bounded list, exact read, one harmless send and wait, verifies exact message/provider-completion readback without duplicate dispatch, and confirms operation-session cleanup. It removes only its local temporary HOME/Hermes/XDG files; it does not create, archive, delete, stop, or otherwise clean up the pre-provisioned T3 project/thread because current public contracts do not support safe project lifecycle reconciliation. This external-write gate is conditional on current live-mutation authority and is required whenever `auth.py` changes or before a release.

Phase A does not implement or execute native external-token acceptance. The current qualification state is `external_token_fixture_unavailable` because no primary evidence defines a protected least-scope issuer with exact session ID/token/expiry output, exact revoke/list confirmation, and run-owned project/thread creation and deletion. Documentation tests require that state and keep native external-token compatibility unverified. Once such an interface exists, its exact version, locator, permitted invocation/API, scopes, lifecycle schemas, credential confinement, and disposable resource contract require a newly frozen acceptance design; implementers may not infer them from the current base-directory CLI.

### D-5: Product, review, and release boundary

- **Covers**: FR-1/AC-2, FR-3/AC-4, FR-4/AC-1, FR-4/AC-2, FR-4/AC-3, NFR-2/AC-1, NFR-2/AC-2

Implementation starts on a new branch from the public baseline and consumes this detached specification by absolute path or emitted manifest. The implementation branch never receives `.codex/specs` or `.claude/specs`. Phase A keeps version 1.2.1, ten tools, provider claims, Linux behavior, and native unsupported copy unchanged unless evidence finds an existing inaccuracy.

An independent reviewer inspects the exact integrated Phase A commit for security, correctness, portability, public-contract stability, support-claim accuracy, and needless complexity. A platform whose conditional live gate is unavailable remains unsupported; an unavailable gate is not converted to PASS. A future native feature version, release, or rollout requires the new Phase B receipt, its implementation gates, and current user authority. The immutable public v1.2.1 commit and artifact remain the rollback baseline.

## Failure contracts

| Condition | Phase A result | Side effect |
|---|---|---|
| native `local-cli` selected | sanitized unsupported configuration result | no session, no HTTP |
| incomplete upstream evidence | `contract_unavailable` record | no adapter, no claim, no version change |
| supported-install test skipped or runs zero cases | gate failure | no support claim |
| artifact payload differs from installed immutable ref | gate failure | no support claim |
| Linux fixture manifest is missing or invalid before Hermes starts | gate failure | no session, no HTTP, no project/thread mutation |
| Linux runtime identity or idle-state check fails | gate failure | read-only HTTP only, operation session confirmed revoked, no project/thread mutation |
| native external-token fixture is unavailable | `external_token_fixture_unavailable` | no session, no live call, no claim |

## Rejected alternatives

- **Specify a hypothetical Phase B protocol now**: rejected because current T3 publishes no exact launcher locator, live-runtime channel, lifecycle schema, connection binding, or least-scope tuple.
- **Current base-directory session commands**: rejected because they do not bind issuance to the live process, origin, environment, or server version.
- **Native process/listener scraping or direct auth database access**: rejected as fragile duplication of T3 internals.
- **`t3code-cli`, inherited `PATH`, or `npx ...@latest`**: rejected because version drift, redirects, and unconfirmed cleanup violate the trust boundary.
- **Treat external-token proof as local-auth proof**: rejected because the credential lifecycle and operator responsibility differ.

## Phase B entry boundary

Phase B does not begin when a plausible design is available. It begins only after D-2 has complete primary evidence and native conformance results, followed by a new frozen Strict audit candidate. Until then, the correct finished result is an honest unsupported native local-auth state with executable packaging gates, an authority-gated Linux fixture check, and an explicit `external_token_fixture_unavailable` boundary.
