# Tool reference

Hermes T3 Control registers eleven synchronous tools in the `t3_control` toolset. Every handler rejects unknown fields, keeps output bounded, and returns JSON with `ok`. Errors also include a stable code, retryability, ambiguity, and safe recovery metadata.

## Eleven tools

| Tool | Purpose and important defaults |
|---|---|
| `t3_threads` | Filtered compact summaries by default; bounded explicit `raw` view. |
| `t3_thread_read` | Exact bounded material summary by default; optional raw page and cursor. |
| `t3_thread_create` | Create one native thread, optionally with one verified initial turn. |
| `t3_thread_send` | `reject` is observation-only; explicit `queue` acknowledges start-or-queue. |
| `t3_thread_set_mode` | Set exactly one stored runtime or interaction mode. |
| `t3_thread_implement_plan` | Verify and implement one stored same-thread plan. |
| `t3_turn_interrupt` | Best-effort interrupt of the provider session current for the exact thread. |
| `t3_session_stop` | Best-effort stop without deleting or replacing the thread. |
| `t3_thread_wait` | Wait for change/running/blocked/terminal/error for at most 30 seconds. |
| `t3_thread_respond` | Best-effort current-session response by exact request ID and expected turn ID. |
| `t3_thread_settle` | Apply native T3 thread settlement after completed work. |

Runtime modes are `approval-required`, `auto-accept-edits`, `auto`, and `full-access`. Interaction modes are `default` and `plan`.

## Workflows

### Find and read

Filters are optional and combined. `require_one` makes zero or multiple matches fail closed; the plugin never silently chooses by fuzzy title.

Example for `t3_threads`:

```json
{
  "project": "demo-app",
  "title_query": "release check",
  "require_one": true,
  "limit": 10
}
```

The compact result includes projects with no threads and sorted thread summaries. Request `{"view":"raw"}` only when diagnostics need the bounded legacy shell projection.

Example for `t3_thread_read`:

```json
{
  "thread_id": "thread-id-from-t3_threads",
  "view": "material",
  "turn_limit": 20
}
```

Material readback includes project/workspace, title, model and modes, lifecycle, provider and background liveness, latest turn/session, pending requests, last error, latest user and assistant updates, actionable plan, timestamps, and sequence cursors. History is bounded and paginated. Request `raw` explicitly for the legacy exact projection.

### Continue and wait

Sending preserves the stored model selection, model options, runtime mode, interaction mode, project, branch, and worktree. It never creates a replacement thread.

Pinned T3 has no atomic idle guard. Therefore the default `busy_policy: reject` is an observation-only check and never dispatches. Every actual send requires `queue`, acknowledging that T3 may start or queue that exact message.

Example for `t3_thread_send`:

```json
{
  "thread_id": "exact-thread-id",
  "message": "Continue the assigned goal and report material progress.",
  "busy_policy": "queue"
}
```

Command states distinguish `started`, `queued`, `completed`, `blocked`, `error`, and `accepted_pending_projection`. Full-access sends repeat the modification/deletion warning at the action point.

Example for `t3_thread_wait`:

```json
{
  "thread_id": "exact-thread-id",
  "after_thread_sequence": 42,
  "until": "terminal",
  "timeout_seconds": 30
}
```

Wait returns only material deltas and one latest assistant update. Provider liveness and work progress remain separate. A timeout means no requested observation occurred in that bounded window; it does not declare provider failure.

### Respond to a pending request

Responses are scoped by exact request ID and expected current turn. The handler revalidates both before dispatch. Pinned T3 has no atomic expected-turn guard, so the receipt is explicitly best-effort and unsuitable for mutually distrusted local actors racing the same thread.

Use `decision` for approval (`accept`, `acceptForSession`, `decline`, or `cancel`) or `answers` for a user-input request. Answer keys must exactly match the pending question IDs returned by `t3_thread_read`.

Example for `t3_thread_respond`:

```json
{
  "thread_id": "exact-thread-id",
  "request_id": "pending-user-input-request-id",
  "turn_id": "pending-request-turn-id",
  "answers": {
    "question-id-from-t3_thread_read": "Focused"
  }
}
```

The compact receipt contains request, thread, command, and verification/reconciliation identities, never the full raw detail.

### Settle completed work

After completed work, call `t3_thread_settle {"thread_id":"exact-thread-id"}` explicitly. This is the manual T3 UI-equivalent settlement action and is available only when the connected T3 server advertises the native capability. The tool verifies native readback; it does not emulate settlement locally.

Settlement conflicts while a turn is starting or running, approval or user input is pending, or a recent queued turn is present. An already-settled thread succeeds. T3 itself clears any pin and snooze as part of settlement. The thread remains available and is not stopped, deleted, or archived. There is no public unsettle tool.

Thread settlement is distinct from turn-liveness `settled`: the liveness value means the current provider and background work are no longer active, but does not apply the native thread settlement state.

### Create

Create is explicit and is never a fallback from send. Omit `instance_id` and `model` together to use the project's model default. If either is supplied, both are required. Optional branch/worktree values are T3 metadata; the plugin does not create or inspect a checkout.

Example for `t3_thread_create`:

```json
{
  "project_id": "project-id-from-t3_threads",
  "title": "Disposable release acceptance",
  "runtime_mode": "approval-required",
  "interaction_mode": "default",
  "initial_message": "Inspect the disposable fixture and report one harmless finding."
}
```

### Strict Plan to Build

Plan implementation uses the server-stored plan, never caller-authored plan prose:

1. Set or create the exact thread in `plan` interaction mode and send the planning goal.
2. Wait until provider and background work settle.
3. Read one unimplemented proposed-plan ID from that exact thread.
4. Implement it with only the thread and stored plan IDs.

Example for `t3_thread_implement_plan`:

```json
{
  "thread_id": "same-thread-id",
  "plan_id": "server-plan-id-from-t3_thread_read"
}
```

The tool verifies the persisted T3 transition to `default`, revalidates the unchanged plan, and starts the same-thread turn with `sourceProposedPlan`. If mode projection is still pending, it stops before implementation dispatch and returns a reconciliation path. Live canonical background work blocks Plan implementation even when the main session looks stopped or errored.

Plan support is provider-specific: an adapter must emit a compatible stored proposed plan. T3 owns `sourceProposedPlan` provenance. See [compatibility evidence](compatibility.md).

## Modes and control

`t3_thread_set_mode` changes exactly one persisted runtime or interaction mode and verifies readback. Changing runtime mode does not retroactively answer an approval already pending for a started turn.

`t3_turn_interrupt` and `t3_session_stop` target the provider session currently observed for the exact thread. T3 has no atomic expected-turn guard for interrupt and no atomic expected-session guard for stop; both are best-effort and return correlation/readback details rather than pretending a stronger guarantee.

## Limits and v1.2 compatibility

Messages are trimmed and limited to 120000 JavaScript UTF-16 code units. Identifiers/titles are limited to 512 characters, cursors/worktree paths to 4096, compact lists to 50, and model options to 64 unique entries. Pending-response answers have a 524288-byte aggregate UTF-8 limit.

Material text is truncated with explicit metadata: messages/errors at 8192 UTF-16 units, plan Markdown at 16384, and nested collections at 32 items/depth 5. Complete compact/material/wait projections are limited to 262144 UTF-8 bytes. Explicit raw reads retain server text within transport bounds.

v1.2 changed omitted list/read views to compact/material. Legacy callers can request `t3_threads {"view":"raw"}` and `t3_thread_read {"thread_id":"...","view":"raw"}` explicitly.

`full-access` permits a trusted provider to execute commands and modify or delete files without approval. Use it only with a trusted provider and checkout.
