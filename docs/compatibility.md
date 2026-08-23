# Compatibility evidence

Support here means a disposable end-to-end provider completion or an exact platform gate—not merely that T3 accepts a model-selection string. The core thread lifecycle is verified end-to-end with Codex; typed user input remains contract-tested rather than live-demonstrated.

## Provider status

| Provider | Status | Evidence |
|---|---|---|
| Codex | Supported | Published and active-profile disposable E2E on `codex_20x`, `gpt-5.6-sol`, Ultra, and full-access. |
| OpenCode | Not yet supported | The inspected OpenCode instance was disabled and not installed. No authenticated disposable provider completion was available. |
| Claude, Grok, Cursor, other T3 providers | Not yet supported | No isolated authenticated completion gate was available in the audited T3 environment. |

Historical thread model selections for OpenCode or Grok are not support evidence. They prove only that T3 persisted those selections at some earlier time.

The Codex live gate covered list/filter, exact read, create, idle send, explicit busy queue/reject, bounded wait, mode change, strict Plan to Build provenance, approval response, interrupt, stop, resume, exact message identity, and provider completion. T3 did not expose a synthesizable live user-input request during that run; the typed user-input branch remains contract-tested but not live-provider demonstrated.

## Tool/provider boundary

| Tool | Wire-level boundary |
|---|---|
| `t3_threads` | Provider-neutral T3 shell projection. |
| `t3_thread_read` | Provider-neutral exact T3 projection; provider-specific material appears as data. |
| `t3_thread_create` | Generic model selection, but the named instance/model must exist and start successfully in T3. |
| `t3_thread_send` | Generic turn dispatch; start/queue and completion semantics depend on the provider adapter. |
| `t3_thread_set_mode` | Persisted by T3; whether a provider honors a mode is provider-specific. |
| `t3_thread_implement_plan` | Provenance is generic to T3 orchestration; producing a compatible stored proposed plan is provider-specific. |
| `t3_turn_interrupt` | Generic T3 command with provider-adapter best-effort cancellation semantics. |
| `t3_session_stop` | Generic T3 command with provider-adapter best-effort stop semantics. |
| `t3_thread_wait` | Generic canonical projection; correctness depends on provider liveness/activity mapping. |
| `t3_thread_respond` | The canonical request/response envelope is generic; native mapping and behavior are provider-specific. |

The code preserves arbitrary T3 `modelSelection` values and contains no Codex allowlist. That is necessary for portability, but it is not sufficient to claim another provider works.

## Same-thread instance switching

The compatibility claim is deliberately narrow: `codex` and `codex_20x` are compatible for same-driver Codex continuation based on evidence that they use the shared Codex home with an account-specific authentication overlay. This does not establish cross-driver continuation compatibility. Other providers and instance pairs remain unproven and must pass the provider acceptance gate below.

The orchestration HTTP surface has no provider catalog, so preflight cannot authoritatively prove that a target exists, uses the same driver, or can continue the thread. T3's projected `thread.turn.start` failure is authoritative for a missing target, different driver, or incompatible continuation. These cases are reported as non-retryable `unsupported_model_switch`; projected quota or usage-limit failures are separately sanitized as `provider_limit_exhausted`.

## Provider acceptance gate

A provider can move to Supported only after one disposable, non-production project proves:

1. T3 reports the instance installed, enabled, and authenticated without exposing credentials.
2. Exact create/list/filter/read preserves instance, model, options, runtime, interaction mode, project, branch, and checkout.
3. Send produces one exact message identity, never duplicates after accepted-but-late projection, and wait observes a real provider completion.
4. Busy behavior is demonstrated as queue or unambiguous refusal.
5. Mode, Plan to Build, approval/user-input response, interrupt, stop, and resume are either proven or explicitly marked unsupported for that provider capability.
6. Every accepted mutation is verified through exact T3 readback and temporary auth is cleaned up.

## Operating-system status

| OS/runtime | Status | Evidence |
|---|---|---|
| Linux | Supported with limits | Python 3.11-3.13 and supported Hermes installs run on Ubuntu CI; secure release building is POSIX-only. The live `local-cli` end-to-end proof is currently WSL2. |
| WSL2 | Supported | Real local T3 discovery/authentication, provider completion, published install, active Hermes activation, and cleanup passed. |
| Native Windows | Not supported yet | `local-cli` requires Linux `/proc` and `os.pidfd_open`; `external-token` has not completed native Windows or macOS end-to-end acceptance. |
| macOS | Not supported yet | `local-cli` requires Linux `/proc` and `os.pidfd_open`; `external-token` has not completed native Windows or macOS end-to-end acceptance. |

`external-token` avoids Linux process discovery, but it remains an operator-isolated deployment path rather than evidence of consumer support on another OS.

## Operating-system acceptance gate

Native Windows or macOS support requires all of the following on a real runner and live T3 instance:

1. A maintainable way to authenticate without placing a bearer in argv, settings, logs, or repository files.
2. Numeric-loopback enforcement, no proxies/redirects, bounded responses, credential-reflection preflight, and exact cleanup behavior.
3. Python 3.11-3.13 unit/contract tests, supported Hermes install/Doctor, fresh-process discovery, and all ten callable definitions.
4. One disposable real-provider create/send/wait completion plus exact readback and duplicate prevention.
5. Deterministic release verification or an explicitly documented build-host boundary when POSIX publishing primitives are unavailable.

This is the acceptance brief for the separate v1.3 portability wave. v1.2.1 does not claim native Windows/macOS or non-Codex provider support.
