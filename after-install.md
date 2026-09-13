# Hermes T3 Control: next steps

Keep T3 running. After the supported `--no-enable` install, the plugin is disabled. Ordinary Linux/WSL2 use needs no copied token or extra settings.

```bash
hermes plugins doctor hermes-t3-control --ci
hermes plugins enable hermes-t3-control --no-allow-tool-override
```

Doctor must report `registrations: 11 tool(s), 0 hook(s)`. Restart only the process that owns Hermes.

If a managed messaging gateway is the owner:

```bash
hermes gateway restart
hermes gateway status
```

For Desktop or `hermes serve`, run `hermes serve --status`, then fully relaunch that same owner. Start a fresh session and use this first non-thread-mutating prompt:

> Call only `t3_threads` with `{}`; do not call mutation tools.

Full setup and recovery are in the bundled `README.md` and `docs/operations.md`.

The same-session continuation observer is opt-in and needs the matching native host patch plus a live authenticated consumer. Version 1.5.0 automatically registers one result report for ordinary `t3_thread_send`, or fails before dispatch. Use `completion_policy: "none"` only for an intentional unwatched send. No per-task binding or credential setup is needed. See `docs/experimental-continuation.md`. The upstream host PR is not merged.
