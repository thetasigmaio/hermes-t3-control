# Security model

## Authentication

In default Linux/WSL2 use, `local-cli` resolves same-user bounded runtime metadata, a numeric loopback origin, the running Node executable, the exact T3 server entrypoint/package version, and the live environment descriptor. It creates one five-minute in-memory bearer session per logical operation. The credential never enters argv, files, settings, logs, or public results and is revoked in `finally`.

Before any T3 HTTP request, the client scans all normalized public arguments for active-credential reflection. Before sending the Authorization header, the plugin pins the T3 process, validates its listener, and proves that the connected socket belongs to that same process.

The current upstream CLI issues eight administrative scopes, including `orchestration:read` and `orchestration:operate`; it cannot mint an orchestration-only session. The plugin limits exposure to one operation, a five-minute TTL, four HTTP routes, and deterministic revocation. This upstream scope breadth is the main residual local-auth risk.

`local-cli` requires Linux `/proc` and `pidfd` support. On WSL2 it assumes a single-user WSL trust boundary and trusts the upstream T3 bundle owned by the same Windows account. Neither auth mode turns a shared loopback listener into safe multi-user isolation; isolate the operating-system user and T3 service.

## External credential mode

`external-token` is an explicit option for an operator-isolated headless or local deployment where `local-cli` is unavailable:

```bash
hermes config set plugins.entries.hermes-t3-control.settings.auth_mode external-token
hermes config set plugins.entries.hermes-t3-control.settings.base_url http://127.0.0.1:3773
hermes config env-path
```

Provision profile-scoped `T3_ORCHESTRATION_TOKEN` through the deployment's Hermes secret facility at the printed environment path. Do not paste it into shell commands or plugin settings. Prefer a short-lived token limited to `orchestration:read` and `orchestration:operate`.

External mode retains numeric-loopback transport, no proxies and redirects, route allowlisting, response bounds, argument preflight, and sanitized errors. It is not yet consumer-E2E verified on native Windows or macOS.

## Network and data bounds

Only these T3 routes are permitted:

- `GET /.well-known/t3/environment`
- `GET /api/orchestration/shell`
- `GET /api/orchestration/threads/:threadId`
- `POST /api/orchestration/dispatch`

Origins must be numeric loopback HTTP. Proxies and redirects are disabled. Environment responses are capped at 1 MiB, orchestration projections at 16 MiB each, and each public operation at a cumulative 64 MiB response-body budget.

Messages, identifiers, model options, histories, pending responses, and agent-facing projections have independent schema and encoded-size limits. See [Tool reference](tools.md#limits-and-v12-compatibility).

## Mutation boundaries

Each mutation uses a new UUIDv4 command ID and immutable canonical body. Only a genuinely ambiguous transport retry may reuse the same command ID and byte-identical body. A command is never resent merely because accepted state has not reached the read projection.

Pinned T3 has no atomic idle guard, expected-turn guard for pending responses or interrupt, or expected-session guard for stop. The plugin therefore makes reject observation-only, requires explicit queue for sends, revalidates pending responses twice, and labels interrupt/stop correlation best-effort.

`full-access` permits a trusted provider to execute commands and modify or delete files without approval. Use it only for a trusted provider, project, and checkout.

If temporary-session revocation cannot be confirmed after an accepted operation, the authoritative result is annotated with `auth_cleanup: failed`. Do not repeat the mutation; reconcile it and wait for the short lease deadline.
