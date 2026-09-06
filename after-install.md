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

The same-session continuation observer remains disabled unless an operator creates an exact local binding and explicitly enables `continuation_enabled`. Stock Hermes lacks the required native gateway APIs. The separately published experimental patch applies only to clean upstream commit `63279301bcbdc185c1b07b98a9312eb0c862f26d`; see `docs/experimental-continuation.md`. The eleven basic tools remain available without it.
