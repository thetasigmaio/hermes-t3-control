# Hermes T3 Control 1.3.0

Control T3 threads from Hermes with eleven bounded tools. Find a thread, inspect its current state, continue it, answer a pending request, or wait for completion without copying credentials or guessing thread IDs.

The basic tool set works on stock supported Hermes. An optional same-session continuation observer is experimental and requires the separate, exact-base host patch described below.

## Quick example

Find one thread:

```json
{"project":"demo-app","title_query":"release check","require_one":true,"limit":10}
```

Pass that object to `t3_threads`, then read the returned ID with:

```json
{"thread_id":"exact-thread-id","view":"material","turn_limit":20}
```

Use the second object with `t3_thread_read`. Reads are bounded. Mutations require an exact target and fail closed when identity or state is ambiguous.

## Verified quick start

Prerequisites: T3 is running, a supported Hermes version is installed, and Git can verify SSH signatures. This wrapper shows the active Hermes profile, verifies the signed `v1.3.0` tag against the pinned release signer, installs the exact commit with scanning enabled and the plugin disabled, runs Doctor, then enables without tool override.

```bash
( git clone --depth 1 https://github.com/thetasigmaio/hermes-t3-control &&
cd ./hermes-t3-control &&
SCRIPT=scripts/install-signed.sh && exec 3<"$SCRIPT" && test -f /dev/fd/3 &&
HASH=b8f8ed3d56e9611f43091fa9d394c98957d3fd2986d85de668faaf66cb8156e8 &&
printf '%s  /dev/fd/3\n' "$HASH" | sha256sum -c - && bash /dev/fd/3 )
```

Doctor must report `registrations: 11 tool(s), 0 hook(s)`. The installer does not use `--force`, change authentication settings, or restart a process.

Restart only the process that owns your Hermes session. For a managed gateway:

```bash
hermes gateway restart
hermes gateway status
```

For Hermes Desktop or `hermes serve`, run `hermes serve --status`, then fully relaunch that same owner. In a fresh session, start with this read-only prompt:

> Call only `t3_threads` with `{}`; do not call mutation tools.

Ordinary local Linux/WSL2 use needs no copied token or plugin settings. New threads default to `approval-required`. Optional create-only settings can select a default instance, model, reasoning effort, and exact aliases; they never rewrite existing threads.

## Compatibility

| Component | Status |
|---|---|
| Hermes 0.20.4 (`e624e9f`) | Verified support for all eleven basic tools. |
| Hermes 0.20.5 (`fcbd107`) | Verified support for all eleven basic tools. |
| Hermes 0.21.0 / `v2026.8.31` (`29112bef`) | Verified support for all eleven basic tools. |
| Python 3.11-3.13 | Unit-tested. |
| T3 server contract | Verified against `0.0.34-nightly.20260820.1141`; later authentication fixtures match the hashed `0.0.37` shape, but other builds remain unverified. |
| T3 on Linux/WSL2 | Local authentication requires `/proc` and `pidfd`; endpoint and capability mismatches fail closed. |
| Native Windows and macOS | Not supported; external-token mode has no native end-to-end acceptance. |
| Codex | Core lifecycle supported. Other provider lifecycles need their own acceptance gate. |

The eleven basic tools need neither the experimental host patch nor a Sunsama account. See [Compatibility evidence](docs/compatibility.md) for precise provider and runtime limits.

## Experimental same-session continuation

The observer is off by default. Stock Hermes 0.20.4, 0.20.5, 0.21.0, and current main do not expose the native gateway APIs it requires. The separately published patch applies only to clean upstream Hermes commit `63279301bcbdc185c1b07b98a9312eb0c862f26d` and is never installed automatically.

The experimental binding stores an operator-supplied task reference; it does not contact Sunsama. Its local `status` output contains private routing metadata and must not be pasted into issues or public logs. Follow the exact signature, base, test, restart, and rollback procedure in [Experimental continuation](docs/experimental-continuation.md).

## Upgrade and uninstall

Upgrade through the same signed-tag flow. Disable and remove the old copy first, install the exact new commit with `--no-enable`, run Doctor, then enable it. Full commands are in [Operations and recovery](docs/operations.md#update).

To remove the plugin from the active profile:

```bash
hermes plugins disable hermes-t3-control
hermes plugins remove hermes-t3-control
hermes config unset plugins.entries.hermes-t3-control
```

## Troubleshooting

- **No tools:** run `hermes plugins show hermes-t3-control` and `hermes plugins doctor hermes-t3-control --ci`, then restart the actual owner.
- **No T3 connection:** start the matching local T3 environment. Local discovery accepts numeric loopback only.
- **Zero or multiple matches:** narrow the project, workspace, or title selector. Never guess a thread ID.
- **Accepted but not projected:** exact-read the returned command/message identity before retrying. A duplicate mutation can create duplicate work.
- **Experimental observer unavailable:** keep it disabled on stock Hermes or remove the manual patch using its documented rollback.

## Security

Local authentication leases one short-lived in-memory T3 session per operation and revokes it on every exit path. That temporary `local-cli` credential does not enter arguments, files, settings, logs, or tool results. External-token mode uses the profile-scoped secret environment described in the security guide. Transport is numeric-loopback-only, proxy-free, redirect-free, process-pinned, size-bounded, and restricted to the documented routes.

The experimental observer adds a short-lived WebSocket ticket and an owner-only local ledger. It injects only validated identifiers and state classifications into an exact allowlisted Hermes session; provider output remains untrusted. See the [Security model](docs/security.md).

## Advanced guides

- [Tool reference](docs/tools.md)
- [Compatibility evidence](docs/compatibility.md)
- [Operations and recovery](docs/operations.md)
- [Experimental continuation](docs/experimental-continuation.md)
- [Security model](docs/security.md)
- [Community index status](docs/community-index.md)

Released under the [MIT License](LICENSE).
