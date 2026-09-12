# Hermes T3 Control

Check on your T3 coding tasks and continue them from Hermes. You can review progress, send follow-ups, and handle requests without switching between the two apps.

## Before you start

- Use Linux or WSL2 with Git that supports SSH signature verification.
- Install Hermes 0.20.4, 0.20.5, or 0.21.0.
- Keep T3 running in the same environment.

## 1. Install

[Download `install-t3.sh`](https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.4.0/install-t3.sh), open a Linux terminal in the downloaded file's folder, and run:

```bash
bash install-t3.sh
```

Confirm the Hermes profile shown by the installer.

<details>
<summary>What does the installer check?</summary>

Running this file trusts the installer published by this project on GitHub over HTTPS. It then verifies the signed plugin release, installs its exact version with Hermes scanning enabled, validates it with Plugin Doctor, and enables it without permission to replace built-in tools. It never uses `--force` or restarts Hermes. See [manual release verification](docs/operations.md#published-asset-verification) for the full independent check.

</details>

<details>
<summary>Using WSL?</summary>

If your browser saved the file to Windows, open that Downloads folder from your Linux terminal before running the command.

</details>

## 2. Reload Hermes

Close and reopen the Hermes CLI. If you use Hermes Desktop or `hermes serve`, fully relaunch that same process.

<details>
<summary>Using a managed gateway?</summary>

Run these commands in the same Hermes profile shown during installation:

```bash
hermes gateway restart
hermes gateway status
```

</details>

## 3. Try it

Start a fresh Hermes session and ask:

> Show my T3 threads.

Hermes should show your T3 tasks and their current progress, or tell you that none were found.

## What you can do

- Check progress and read results.
- Create tasks and send follow-up instructions.
- Answer approvals, interrupt a turn, or stop a session.

## Experimental continuation

Same-conversation verification and optional Telegram completion notices are experimental and off by default. They require a separate patch for one exact Hermes revision; follow [Experimental continuation](docs/experimental-continuation.md) if you want to evaluate it.

## Help and reference

- [Troubleshooting, upgrade, rollback, and uninstall](docs/operations.md)
- [Compatibility](docs/compatibility.md)
- [Tool reference](docs/tools.md)
- [Security model](docs/security.md)
- [Community index status](docs/community-index.md)

Released under the [MIT License](LICENSE).
