# Tasks: Hermes T3 Control portability qualification

- **Schema**: `sdd-tasks/v2`
- **Spec-ID**: `hermes-t3-control-portability`
- **Profile**: Strict
- **Phase**: v1.3 Phase A qualification and test readiness
- **Implementation baseline**: `a76b34d9cc1f1a5732f7f264003ad01d9eea7fe5`

## Verification catalog

### V-FAIL-CLOSED: Run native unsupported-boundary regressions

- **Command**: `python -W error -B -m unittest -v tests.test_auth tests.test_client`
- **Expected**: exits 0 with native Windows/macOS `local-cli` returning one sanitized unsupported result before session issuance or HTTP, while Linux and explicit external-token behavior remain unchanged
- **Covers**: FR-1/AC-1, FR-4/AC-2, NFR-1/AC-1
- **Stage**: focused
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `lock:python-test-runner:exclusive`
- **Provenance**: E-1, E-3, E-4, E-5, E-9

### V-DOCS: Run compatibility and contract-boundary tests

- **Command**: `python -W error -B -m unittest -v tests.test_readme`
- **Expected**: exits 0 with native local auth unsupported, native external-token compatibility unverified, ten tools unchanged, every Phase B entry input represented, and the qualification states exactly `contract_unavailable` and `external_token_fixture_unavailable`
- **Covers**: FR-1/AC-2, FR-2/AC-1, FR-2/AC-2, FR-2/AC-3, FR-2/AC-4, FR-2/AC-5, FR-2/AC-6, FR-3/AC-3, FR-3/AC-4, FR-4/AC-1, NFR-2/AC-1, NFR-2/AC-2
- **Stage**: focused
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `lock:python-test-runner:exclusive`
- **Provenance**: E-1, E-5, E-7, E-9, E-10

### V-BUILD: Build the deterministic release artifact twice

- **Command**: `python -B scripts/build_release.py --output-dir dist --verify-deterministic`
- **Expected**: on Linux, exits 0 after two internal builds in distinct clean private temporary directories produce byte-identical archives and checksums, then writes only the accepted pair to `dist`
- **Covers**: FR-3/AC-2, FR-4/AC-3
- **Stage**: focused
- **Required**: yes
- **Side-Effect-Class**: workspace-write
- **Resources**: `path:dist/:write`
- **Provenance**: E-9, E-10

### V-VERIFY: Verify the deterministic release checksum

- **Command**: `python -B scripts/verify_release.py dist/hermes-t3-control-1.2.1.tar.gz.sha256`
- **Expected**: exits 0 after verifying the existing archive bytes against the detached checksum produced by V-BUILD
- **Covers**: FR-3/AC-2, FR-4/AC-3
- **Stage**: focused
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `lock:release-verification:exclusive`, `path:dist/:read`
- **Provenance**: E-9, E-10

### V-RUNNER-UNIT: Run platform-runner and release-boundary contracts

- **Command**: `python -W error -B -m unittest -v tests.test_supported_install tests.test_release tests.test_registration`
- **Expected**: exits 0 with exact Hermes project/ref validation, zero-case and skip failure, hermetic snapshot construction, payload equivalence, non-POSIX builder-boundary assertions, ten-tool parity, and allowlisted environments passing
- **Covers**: FR-3/AC-1, FR-3/AC-2, FR-4/AC-1, FR-4/AC-3, NFR-1/AC-1
- **Stage**: focused
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `lock:python-test-runner:exclusive`
- **Provenance**: E-1, E-8, E-9, E-10, E-11

### V-INSTALL: Run unskippable pinned-Hermes install and payload-equivalence gates

- **Command**: `python -B scripts/run_supported_install_matrix.py --require-executed --hermes-0-20-4-project .ci/hermes-0.20.4 --hermes-0-20-4-commit e624e9fde561e1add9388384012b295fde669ade --hermes-0-20-5-project .ci/hermes-0.20.5 --hermes-0-20-5-commit fcbd1076a93841fa88855acce810e342a5b78101 --artifact dist/hermes-t3-control-1.2.1.tar.gz --checksum dist/hermes-t3-control-1.2.1.tar.gz.sha256`
- **Expected**: after CI has provisioned both declared trees with pinned uv `0.12.0` and `uv sync --frozen`, exits 0 only when the runner validates each HEAD, lockfile, and project-local interpreter and both environments execute substantive scanner-on hermetic-snapshot disabled install, disabled Doctor, no-override enable, fresh-process ten-tool calls, removal, and byte equality with the verified artifact; missing inputs, zero cases, or any skip exits nonzero
- **Covers**: FR-3/AC-2, FR-4/AC-1, FR-4/AC-2, FR-4/AC-3, NFR-1/AC-1
- **Stage**: both
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `lock:hermes-supported-install:exclusive`, `path:dist/:read`
- **Provenance**: E-1, E-8, E-9, E-10

### V-CROSS-PLATFORM: Run the complete warnings-as-errors suite

- **Command**: `python -W error -B -m unittest discover -s tests -v`
- **Expected**: exits 0 on Ubuntu, native macOS, and native Windows for Python 3.11, 3.12, and 3.13; non-POSIX cells execute an assertion of the intentional builder rejection rather than skipping release coverage
- **Covers**: FR-3/AC-1, FR-4/AC-1, FR-4/AC-2, NFR-1/AC-1
- **Stage**: both
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `lock:python-test-runner:exclusive`
- **Provenance**: E-1, E-8, E-9

### V-LIVE-LINUX: Run the disposable Linux local-auth regression

- **Command**: `python -B scripts/live_portability_e2e.py --platform linux --auth-mode local-cli --fixture-manifest .ci/t3-linux-disposable-fixture.json --require-dedicated --operator-authorized-send`
- **Expected**: with current mutation authority and a non-secret operator-provided dedicated fixture manifest, exits 0 after fresh Hermes resolves exactly one thread with the manifest project/workspace selectors and `require_one`, verifies project/workspace/thread/title identities and idle state, performs exact read plus one unique harmless send and wait, confirms exact message/provider-completion readback without duplicate dispatch and operation-session cleanup, removes only run-local temporary files, and performs no T3 project/thread lifecycle mutation
- **Covers**: FR-3/AC-5, FR-4/AC-1, FR-4/AC-2, NFR-1/AC-1
- **Stage**: both
- **Required**: conditional
- **Condition**: required whenever `auth.py` changes or before a Phase A release; requires current disposable live-mutation authority
- **Side-Effect-Class**: external-write
- **Resources**: `lock:t3-linux-fixture:exclusive`, `path:.ci/t3-linux-disposable-fixture.json:read`, `network:t3-linux-live:exclusive`, `capacity:provider-completion-linux:exclusive`
- **Provenance**: E-1, E-3, E-4, E-9, E-10

### V-DIFF: Check the integrated Phase A patch

- **Command**: `git diff --check`
- **Expected**: exits 0 with no whitespace errors
- **Covers**: all
- **Stage**: final
- **Required**: yes
- **Side-Effect-Class**: read-only
- **Resources**: `workspace:read`
- **Provenance**: E-2, E-9

## Implementation DAG

### T-1: Lock the unsupported boundary and Phase B entry contract test-first

Add the smallest handler and documentation regressions first. Prove native Windows/macOS `local-cli` reaches zero session and HTTP side effects, native external-token compatibility remains unverified with `external_token_fixture_unavailable`, and the complete FR-2 entry checklist is present. If current production behavior already passes the new handler test, do not change `auth.py`; otherwise make only the fail-closed correction. Do not add a native adapter, external-token issuer, or hypothetical protocol constants.

- **AC**: FR-1/AC-1, FR-1/AC-2, FR-2/AC-1, FR-2/AC-2, FR-2/AC-3, FR-2/AC-4, FR-2/AC-5, FR-2/AC-6, FR-3/AC-3, FR-3/AC-4, FR-4/AC-2, NFR-2/AC-1, NFR-2/AC-2
- **Design**: D-1, D-2, D-4, D-5
- **Writes**: `auth.py`, `tests/test_auth.py`, `tests/test_readme.py`, `README.md`, `after-install.md`, `docs/compatibility.md`, `docs/security.md`, `docs/operations.md`
- **Depends**: -
- **Verify**: V-FAIL-CLOSED, V-DOCS

### T-2: Make release and supported-install gates platform-safe

Add RED tests for the current POSIX assumptions and skip-success behavior, then implement one platform-neutral supported-install runner. It owns the substantive-test opt-in and fails on zero cases or any skip. Adapt non-POSIX release tests to assert the Linux-only builder boundary while retaining all manifest, allowlist, archive-safety, and documentation checks. Add one deterministic-build mode that internally builds in two clean private directories, compares archive and checksum bytes, and places only the accepted pair in `dist`. Make the install runner validate explicit pre-provisioned Hermes project paths, exact commits, frozen lockfiles, project-local interpreters, a hermetic exact-tree snapshot, and installed-payload equality. Keep the allowlisted environment, private temporary homes, neutral Git configuration, scanner, disabled Doctor, explicit enable, fresh-process calls, and removal contract.

- **AC**: FR-3/AC-1, FR-3/AC-2, FR-4/AC-1, FR-4/AC-3, NFR-1/AC-1
- **Design**: D-3, D-5
- **Writes**: `scripts/run_supported_install_matrix.py`, `scripts/build_release.py`, `scripts/verify_release.py`, `tests/test_supported_install.py`, `tests/test_release.py`, `tests/test_registration.py`
- **Depends**: T-1
- **Verify**: V-RUNNER-UNIT
- **Scope**: broad

### T-3: Add cross-platform CI and the isolated Linux live gate

Integrate Python 3.11-3.13 Ubuntu/macOS/Windows jobs. In every install cell, check out both exact Hermes commits to their declared `.ci` paths, set up the SHA-pinned uv action and uv `0.12.0`, and run `uv sync --frozen` before the runner. Add a Linux-only live wrapper that requires a non-secret operator-provided manifest for one dedicated non-production fixture and explicit current send authority. It must use fresh Hermes `local-cli`, resolve with exact manifest project/workspace selectors plus `require_one`, verify project/workspace/thread/title identity and idle state, exercise one unique harmless list/read/send/wait, confirm exact message identity and auth cleanup, and remove only local temporary resources. It must not create or delete T3 projects/threads. Do not add a native external-token issuer or gate while its fixture contract is unavailable.

- **AC**: FR-3/AC-1, FR-3/AC-2, FR-3/AC-3, FR-3/AC-4, FR-3/AC-5, FR-4/AC-1, FR-4/AC-2, FR-4/AC-3, NFR-1/AC-1, NFR-2/AC-2
- **Design**: D-3, D-4, D-5
- **Writes**: `.github/workflows/ci.yml`, `scripts/live_portability_e2e.py`, `tests/test_portability_e2e.py`
- **Depends**: T-2
- **Verify**: V-DOCS, V-BUILD, V-VERIFY, V-INSTALL, V-CROSS-PLATFORM, V-LIVE-LINUX
- **Scope**: broad

### T-REVIEW: Independently review Phase A and enforce the boundary

Review the exact integrated Phase A diff for security, correctness, portability, public-contract stability, support-claim accuracy, task scope, and needless complexity. Run every non-conditional gate in its declared environment. Run V-LIVE-LINUX only with current authority and a protected disposable fixture; otherwise record it unavailable, not PASS. Reject any native adapter, native authentication claim, feature-version change, specification path in the release tree, or unresolved Critical/High finding.

- **AC**: all
- **Design**: all
- **Writes**: -
- **Depends**: T-3
- **Verify**: V-FAIL-CLOSED, V-DOCS, V-RUNNER-UNIT, V-BUILD, V-VERIFY, V-INSTALL, V-CROSS-PLATFORM, V-LIVE-LINUX, V-DIFF
- **Scope**: broad
- **Kind**: review-gate
