# Changelog

All notable changes to Hermes T3 Control are documented in this file.

## [1.2.0] - 2026-08-22

### Added

- Added zero-copy local authentication that discovers the active matching T3 CLI, leases one five-minute in-memory session per tool operation, and revokes it on every exit path.
- Added compact project/title/state thread discovery, material thread readback, bounded per-thread status waiting, and exact typed approval or user-input responses.
- Added explicit busy-thread `reject`/`queue` handling while preserving exact-thread model, mode, branch, worktree, and model-option continuity.

### Fixed

- Treats an accepted dispatch whose read projection is delayed as `accepted_pending_projection`, with exact command/message identities and a safe exact-read reconciliation recipe, instead of a failure that could invite a duplicate turn.
- Recognizes T3's persisted queued user message with a null turn link and never redispatches after a successful dispatch response.
- Waits within the request deadline for a delayed server-side socket accept before authentication and preserves a sanitized cleanup warning even when a handler fails unexpectedly.
- Makes release output permissions deterministic across caller umasks and isolates the supported-install harness from ambient credentials, Git configuration, hooks, and proxies.

### Changed

- Expanded the registered surface from eight to ten tools; list/read results now default to bounded compact/material views with explicit `raw` compatibility views.
- Pinned supported Hermes CI to each revision's frozen `uv.lock` dependency graph and hardened published-asset verification around a fresh private HTTPS-only download directory.

### Security

- Local T3 credentials remain in process memory and never enter argv, files, logs, or public results; CLI output, HTTP I/O, polling, and response bodies remain bounded. Credentialed connections are pinned to the discovered process, listener, and accepted socket.
- The current upstream local-session CLI grants eight administrative scopes. The plugin limits exposure with exact live-environment matching, a five-minute TTL, an orchestration-only HTTP route allowlist, and deterministic revocation; external scoped-token mode remains available for headless deployments.

## [1.1.1] - 2026-08-21

### Fixed

- Restored installation on declared Hermes 0.20.4 and 0.20.5 by using their supported native manifest version.
- Removed historical development specifications that the default install-time security scanner rejects; they remain available in the v1.1.0 tag and repository history.
- Rejected active-credential reflection across every normalized public argument before any T3 HTTP request or mutation.
- Rejected explicit JSON `null` for the non-null `before_cursor`, `branch`, and `worktree_path` string fields.

### Changed

- Added a real pinned-ref install, disabled-state, explicit-enable, fresh-process load, and exact eight-tool verification regression for both supported Hermes revisions.
- Streamlined the operator guide around safe installation, common workflows, recovery, compatible rollback, and canonical removal.

## [1.1.0] - 2026-08-21

### Added

- Typed thread-mode updates and a provenance-preserving same-thread Plan to Build workflow.
- Creation-time runtime defaults, initial turns, checkout metadata, and model option preservation.
- Reproducible Python 3.11-3.13 CI, pinned Hermes Plugin Doctor compatibility checks, and deterministic release archives.

### Security

- Retained numeric-loopback-only transport, profile-safe credential resolution, exact-target mutation readback, and sanitized errors.
- Rejects active-token reflection in request targets or mutation data before any HTTP request.
- Publishes release artifacts through owner-private, inode-bound no-follow staging and rejects unsafe directories, links, or special files.

[1.2.0]: https://github.com/thetasigmaio/hermes-t3-control/releases/tag/v1.2.0
[1.1.1]: https://github.com/thetasigmaio/hermes-t3-control/releases/tag/v1.1.1
[1.1.0]: https://github.com/thetasigmaio/hermes-t3-control/releases/tag/v1.1.0
