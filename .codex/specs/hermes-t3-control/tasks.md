# Tasks: Hermes T3 Control 1.1 Release

- **Schema**: `sdd-tasks/v2`
- **Spec-ID**: `hermes-t3-control`

## Verification catalog

### V-CLIENT: Run client schema, transport, and mutation tests
- **Command**: `python3 -B -m unittest -v tests.test_client`
- **Expected**: exits 0 and all response-contract, provenance, transport, deadline, size, retry, sticky-ambiguity, acceptance-required retry, active-token preflight, and readback tests pass
- **Covers**: FR-2/AC-1, FR-2/AC-2, FR-10/AC-1, FR-10/AC-2, FR-10/AC-3, FR-10/AC-4, FR-10/AC-5, FR-10/AC-6, FR-11/AC-2, FR-11/AC-3, FR-12/AC-1, FR-12/AC-2, FR-12/AC-3, NFR-1/AC-4, NFR-2/AC-1, NFR-2/AC-2, NFR-2/AC-3, NFR-4/AC-2
- **Stage**: focused
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `workspace:read`, `network:loopback:exclusive`
- **Provenance**: E-2, E-7, E-9

### V-TOOLS: Run tool workflow, schema, registration, and safety tests
- **Command**: `python3 -B -m unittest -v tests.test_tools tests.test_registration`
- **Expected**: exits 0 and all eight-tool, configuration, create/turn, metadata/options, mode, acceptance-proven Plan to Build, concurrency-disclosure, interrupt/stop, and source-safety tests pass
- **Covers**: FR-1/AC-1, FR-1/AC-2, FR-3/AC-1, FR-3/AC-2, FR-3/AC-3, FR-3/AC-4, FR-3/AC-5, FR-4/AC-1, FR-4/AC-2, FR-4/AC-3, FR-4/AC-4, FR-4/AC-5, FR-5/AC-1, FR-5/AC-2, FR-5/AC-3, FR-5/AC-4, FR-6/AC-1, FR-6/AC-2, FR-6/AC-3, FR-6/AC-4, FR-7/AC-1, FR-7/AC-2, FR-7/AC-3, FR-7/AC-4, FR-7/AC-5, FR-7/AC-6, FR-7/AC-7, FR-7/AC-8, FR-8/AC-1, FR-8/AC-2, FR-9/AC-1, FR-9/AC-2, FR-11/AC-1, FR-11/AC-2, NFR-1/AC-1, NFR-1/AC-2, NFR-3/AC-1, NFR-4/AC-1, NFR-4/AC-2, NFR-4/AC-3
- **Stage**: focused
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `workspace:read`, `network:loopback:exclusive`
- **Provenance**: E-1, E-2, E-6, E-7, E-9

### V-RELEASE: Run release hygiene, artifact, CI-contract, and live-smoke script tests
- **Command**: `python3 -B -m unittest -v tests.test_release`
- **Expected**: exits 0 and version/license, tracked spec convention, byte-identical deterministic archive, symlink/hardlink/directory/FIFO target rejection with sentinel preservation, wrong-owner and group/world-writable output rejection before staging/publication, private same-filesystem staging with inode-rebinding rejection, verified final hashes, portable checksum verifier, pinned CI graph, and exact-read smoke-script assertions pass
- **Covers**: NFR-3/AC-2, NFR-4/AC-4, NFR-5/AC-1, NFR-5/AC-2, NFR-5/AC-3, NFR-5/AC-4, NFR-5/AC-6, NFR-5/AC-7
- **Stage**: focused
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `workspace:read`, `lock:release-tests:exclusive`
- **Provenance**: E-1, E-5, E-10

### V-README: Run operator documentation contract tests
- **Command**: `python3 -B -m unittest -v tests.test_readme`
- **Expected**: exits 0 and installation, least privilege, full-access warning, non-retroactivity, concurrency limits, Plan to Build, all tools, and repository-relative verification commands are documented
- **Covers**: NFR-1/AC-3, NFR-5/AC-5
- **Stage**: focused
- **Required**: yes
- **Side-Effect-Class**: read-only
- **Resources**: `workspace:read`
- **Provenance**: E-1, E-5, E-7

### V-UNIT: Run the complete unit and mocked integration suite
- **Command**: `python3 -B -m unittest discover -s tests -v`
- **Expected**: exits 0 and every repository test passes
- **Covers**: all
- **Stage**: final
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `workspace:read`, `network:loopback:exclusive`
- **Provenance**: E-1, E-9

### V-DOCTOR: Run Hermes Plugin Doctor
- **Command**: `env PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor . --ci`
- **Expected**: exits 0 with eight declared/registered tools and no manifest, import, registration, or network-on-registration error
- **Covers**: FR-1/AC-1, FR-1/AC-2, NFR-4/AC-3
- **Stage**: final
- **Required**: yes
- **Side-Effect-Class**: local-runtime
- **Resources**: `workspace:read`, `lock:hermes-plugin-doctor:exclusive`
- **Provenance**: E-5, E-6, E-9

### V-PACKAGE: Build the deterministic release archive and checksum
- **Command**: `python3 -B scripts/build_release.py --output-dir dist`
- **Expected**: exits 0 and writes only the versioned allowlisted archive and detached checksum under ignored `dist/`
- **Covers**: NFR-5/AC-1, NFR-5/AC-4, NFR-5/AC-6, NFR-5/AC-7
- **Stage**: final
- **Required**: yes
- **Side-Effect-Class**: workspace-write
- **Resources**: `path:dist/:write`
- **Provenance**: E-1, E-10

### V-CHECKSUM: Verify the generated release checksum
- **Command**: `python3 -B scripts/verify_release.py dist/hermes-t3-control-1.1.0.tar.gz.sha256`
- **Expected**: exits 0 and reports the release archive checksum valid
- **Covers**: NFR-5/AC-1, NFR-5/AC-4
- **Stage**: final
- **Required**: yes
- **Side-Effect-Class**: read-only
- **Resources**: `path:dist/:read`
- **Provenance**: E-1, E-10

### V-LIVE: Read one explicitly isolated live T3 thread
- **Command**: `python3 -B scripts/live_smoke.py`
- **Expected**: exits 0 after one exact-thread GET and prints only thread ID, snapshot sequence, runtime mode, interaction mode, and a success marker
- **Covers**: NFR-4/AC-4
- **Stage**: final
- **Required**: conditional
- **Condition**: the operator has set the required environment values for one explicitly designated isolated non-SolarSim test thread and current runtime authority permits its exact read
- **Side-Effect-Class**: external-read
- **Resources**: `network:t3-loopback:read`
- **Provenance**: E-1, E-2, E-7

## Implementation DAG

### T-1: Extend response contracts without weakening transport
Add proposed-plan and latest-turn provenance validation, retain forward compatibility and every existing transport/identity/secret/ambiguity control, reject active-token occurrences before all exact-thread reads and dispatches, preserve ambiguity history across accepted retries, and extend loopback fixtures and client regression tests.
- **AC**: FR-2/AC-1, FR-2/AC-2, FR-10/AC-1, FR-10/AC-2, FR-10/AC-3, FR-10/AC-4, FR-10/AC-5, FR-10/AC-6, FR-11/AC-2, FR-11/AC-3, FR-12/AC-1, FR-12/AC-2, FR-12/AC-3, NFR-1/AC-4, NFR-2/AC-1, NFR-2/AC-2, NFR-2/AC-3, NFR-4/AC-2
- **Design**: D-2, D-3, D-5, D-8, D-9
- **Writes**: `client.py`, `tests/support.py`, `tests/test_client.py`
- **Depends**: -
- **Verify**: V-CLIENT

### T-2: Implement consistent create, typed modes, and Plan to Build
Update manifest, schemas, registration, handlers, shared turn construction, configuration resolution, model/metadata preservation, composite workflow phase reporting, and focused tests for the exact eight-tool surface.
- **AC**: FR-1/AC-1, FR-1/AC-2, FR-3/AC-1, FR-3/AC-2, FR-3/AC-3, FR-3/AC-4, FR-3/AC-5, FR-4/AC-1, FR-4/AC-2, FR-4/AC-3, FR-4/AC-4, FR-4/AC-5, FR-5/AC-1, FR-5/AC-2, FR-5/AC-3, FR-5/AC-4, FR-6/AC-1, FR-6/AC-2, FR-6/AC-3, FR-6/AC-4, FR-7/AC-1, FR-7/AC-2, FR-7/AC-3, FR-7/AC-4, FR-7/AC-5, FR-7/AC-6, FR-7/AC-7, FR-7/AC-8, FR-8/AC-1, FR-8/AC-2, FR-9/AC-1, FR-9/AC-2, FR-11/AC-1, FR-11/AC-2, NFR-1/AC-1, NFR-1/AC-2, NFR-3/AC-1, NFR-4/AC-1, NFR-4/AC-2, NFR-4/AC-3
- **Design**: D-1, D-2, D-4, D-5, D-6, D-7, D-8, D-9
- **Writes**: `plugin.yaml`, `__init__.py`, `schemas.py`, `tools.py`, `tests/test_tools.py`, `tests/test_registration.py`
- **Depends**: T-1
- **Verify**: V-CLIENT, V-TOOLS

### T-3: Add release hygiene, CI, and deterministic packaging
Add ignore rules, MIT license, changelog, full-SHA-pinned least-privilege CI matrices, deterministic allowlisted archive builder with owner/mode validation and private no-follow output staging, and release/CI contract tests including unsafe-directory, link, special-file, and name-rebinding sentinels.
- **AC**: NFR-3/AC-2, NFR-4/AC-4, NFR-5/AC-1, NFR-5/AC-2, NFR-5/AC-3, NFR-5/AC-4, NFR-5/AC-6, NFR-5/AC-7
- **Design**: D-9, D-10
- **Writes**: `.gitignore`, `LICENSE`, `CHANGELOG.md`, `.github/workflows/ci.yml`, `scripts/build_release.py`, `scripts/verify_release.py`, `scripts/live_smoke.py`, `tests/test_release.py`
- **Depends**: T-2
- **Verify**: V-RELEASE

### T-4: Document installation and strict operator workflows
Rewrite README for portable installation/configuration, exact eight tools, explicit full-access risk, create-plus-first-turn behavior, native same-thread Plan to Build, non-retroactive approval and race limitations, and local/release verification.
- **AC**: NFR-1/AC-3, NFR-5/AC-5
- **Design**: D-11
- **Writes**: `README.md`, `tests/test_readme.py`
- **Depends**: T-2
- **Verify**: V-README

### T-REVIEW: Review the integrated 1.1.0 release candidate
Perform independent correctness, security, release-artifact, and integration review, then run every final verification on one frozen checkpoint. Evaluate the conditional isolated live-smoke precondition without mutating unrelated or SolarSim state.
- **AC**: all
- **Design**: all
- **Writes**: -
- **Depends**: T-3, T-4
- **Verify**: V-UNIT, V-DOCTOR, V-PACKAGE, V-CHECKSUM, V-LIVE
- **Scope**: broad
- **Kind**: review-gate
