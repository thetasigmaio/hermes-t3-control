# Changelog

All notable changes to Hermes T3 Control are documented in this file.

## [1.1.1] - 2026-08-21

### Fixed

- Restored installation on declared Hermes 0.20.4 and 0.20.5 by using their supported native manifest version.
- Removed historical development specifications that the default install-time security scanner rejects; they remain available in the v1.1.0 tag and repository history.

### Changed

- Added a real disabled pinned-ref install regression with scanning enabled and exact eight-tool Plugin Doctor verification for both supported Hermes revisions.
- Streamlined the operator guide around safe installation, common workflows, recovery, and rollback.

## [1.1.0] - 2026-08-21

### Added

- Typed thread-mode updates and a provenance-preserving same-thread Plan to Build workflow.
- Creation-time runtime defaults, initial turns, checkout metadata, and model option preservation.
- Reproducible Python 3.11-3.13 CI, pinned Hermes Plugin Doctor compatibility checks, and deterministic release archives.

### Security

- Retained numeric-loopback-only transport, profile-safe credential resolution, exact-target mutation readback, and sanitized errors.
- Rejects active-token reflection in request targets or mutation data before any HTTP request.
- Publishes release artifacts through owner-private, inode-bound no-follow staging and rejects unsafe directories, links, or special files.

[1.1.1]: https://github.com/thetasigmaio/hermes-t3-control/releases/tag/v1.1.1
[1.1.0]: https://github.com/thetasigmaio/hermes-t3-control/releases/tag/v1.1.0
