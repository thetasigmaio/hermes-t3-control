from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import tempfile
import unittest

import schemas


ROOT = pathlib.Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
INSTALL_SCRIPT_PATH = ROOT / "scripts" / "install-signed.sh"
INSTALL_SCRIPT_SHA256 = (
    "7ae2ee2e2a78355d6e318538b05754004679af113c316edf3bef3dc4c855cc6c"
)
AFTER_INSTALL_PATH = ROOT / "after-install.md"
TOOLS_PATH = ROOT / "docs" / "tools.md"
COMPATIBILITY_PATH = ROOT / "docs" / "compatibility.md"
SECURITY_PATH = ROOT / "docs" / "security.md"
OPERATIONS_PATH = ROOT / "docs" / "operations.md"
INDEX_PATH = ROOT / "docs" / "community-index.md"
INDEX_ENTRY_PATH = ROOT / "docs" / "community-index-entry.json"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


class ProductDocumentationContractTests(unittest.TestCase):
    def test_readme_is_short_outcome_first_and_links_detailed_guides(self) -> None:
        readme = _read(README_PATH)
        self.assertLessEqual(len(readme.splitlines()), 170)
        for phrase in (
            "# Hermes T3 Control 1.2.1",
            "Control T3 work from Hermes",
            "core thread lifecycle is verified end-to-end with Codex",
            "## Compatibility",
            "## Quick start",
            "## First safe check",
            "## Everyday workflow",
            "[Tool reference](docs/tools.md)",
            "[Compatibility evidence](docs/compatibility.md)",
            "[Security model](docs/security.md)",
            "[Operations and recovery](docs/operations.md)",
            "[Community index status](docs/community-index.md)",
            "[MIT License](LICENSE)",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readme)
        self.assertLess(readme.index("## Compatibility"), readme.index("## Quick start"))
        self.assertLess(readme.index("## Quick start"), readme.index("## Everyday workflow"))
        self.assertNotIn("## Ten-tool reference", readme)
        self.assertNotIn("## Published asset verification", readme)
        self.assertNotIn("## Development verification", readme)
        self.assertIn("all eleven tools", readme)
        self.assertNotIn("all eleven schemas", readme)

    def test_provider_and_os_matrix_make_only_evidenced_claims(self) -> None:
        readme = _read(README_PATH)
        compatibility = _read(COMPATIBILITY_PATH)
        for row in (
            "| Codex | Supported |",
            "| OpenCode | Not yet supported |",
            "| Other T3 providers | Not yet supported |",
            "| Linux | Supported with limits |",
            "| WSL2 | Supported |",
            "| Native Windows | Not supported yet |",
            "| macOS | Not supported yet |",
        ):
            with self.subTest(row=row):
                self.assertIn(row, readme)
        for phrase in (
            "codex_20x",
            "OpenCode instance was disabled and not installed",
            "No authenticated disposable provider completion was available",
            "Ubuntu CI",
            "live `local-cli` end-to-end proof is currently WSL2",
            "`/proc` and `os.pidfd_open`",
            "`external-token` has not completed native Windows or macOS end-to-end acceptance",
            "Provider acceptance gate",
            "Operating-system acceptance gate",
            "Provenance is generic to T3 orchestration; producing a compatible stored proposed plan is provider-specific.",
            "The canonical request/response envelope is generic; native mapping and behavior are provider-specific.",
            "core thread lifecycle is verified end-to-end with Codex",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, compatibility)
        self.assertNotIn("OpenCode | Supported", readme)
        self.assertNotRegex(readme, r"\| (?:Native Windows|macOS) \| Supported \|")

    def test_quick_start_is_a_five_line_hash_gated_wrapper(self) -> None:
        readme = _read(README_PATH)
        script = _read(INSTALL_SCRIPT_PATH)
        quick = readme.split("## Quick start", 1)[1].split("## First safe check", 1)[0]
        readme_blocks = re.findall(r"```bash\n(.*?)\n```", readme, re.DOTALL)
        command_blocks = re.findall(r"```bash\n(.*?)\n```", quick, re.DOTALL)
        self.assertGreaterEqual(len(command_blocks), 3)
        commands = command_blocks[0]
        self.assertEqual(readme_blocks[0], commands)
        digest = hashlib.sha256(INSTALL_SCRIPT_PATH.read_bytes()).hexdigest()
        self.assertEqual(digest, INSTALL_SCRIPT_SHA256)
        self.assertEqual(
            commands.splitlines(),
            [
                "( git clone --depth 1 "
                "https://github.com/thetasigmaio/hermes-t3-control &&",
                "cd ./hermes-t3-control &&",
                'SCRIPT=scripts/install-signed.sh && exec 3<"$SCRIPT" '
                "&& test -f /dev/fd/3 &&",
                f"HASH={INSTALL_SCRIPT_SHA256} &&",
                "printf '%s  /dev/fd/3\\n' \"$HASH\" | sha256sum -c - "
                "&& bash /dev/fd/3 )",
            ],
        )
        self.assertEqual(len(commands.splitlines()), 5)
        self.assertTrue(all(len(line) <= 80 for line in commands.splitlines()))
        self.assertTrue(script.endswith("\n"))
        self.assertNotRegex(readme, r"(?im)\bcurl\b[^\n|]*\|\s*(?:ba)?sh\b")
        for implementation_detail in (
            "safe_git",
            "VERIFY_DIR",
            "env -i",
            "XDG_CONFIG_HOME",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "protocol.file.allow",
            "gpg.format=ssh",
            "gpg.ssh.allowedSignersFile",
            "verify-tag",
            "rev-parse",
            "SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4",
        ):
            with self.subTest(implementation_detail=implementation_detail):
                self.assertNotIn(implementation_detail, readme)
        self.assertIn("automatic local authentication", quick)
        self.assertIn("When no profile-scoped T3 token is configured", quick)
        self.assertIn("Git supports SSH signature verification", quick)
        self.assertIn("approval-required", quick)
        self.assertIn("managed messaging gateway", quick)
        self.assertEqual(
            command_blocks[1],
            "hermes gateway restart\nhermes gateway status",
        )
        self.assertIn("Desktop or `hermes serve`", quick)
        self.assertEqual(command_blocks[2], "hermes serve --status")
        self.assertIn("Restart only the process that owns your Hermes session", quick)
        self.assertEqual(quick.count("registrations: 10 tool(s), 0 hook(s)"), 1)

    def test_quick_start_failure_paths_do_not_execute_the_installer(self) -> None:
        readme = _read(README_PATH)
        commands = re.findall(r"```bash\n(.*?)\n```", readme, re.DOTALL)[0]
        scenarios = (
            (
                "failed clone with pre-existing checkout",
                "exit 1\n",
                True,
                False,
                False,
            ),
            (
                "checksum mismatch",
                "mkdir -p hermes-t3-control/scripts\n"
                "printf 'tampered\\n' > "
                "hermes-t3-control/scripts/install-signed.sh\n",
                False,
                False,
                False,
            ),
            (
                "verified descriptor",
                "mkdir -p hermes-t3-control/scripts\n"
                "cp \"$SOURCE\" hermes-t3-control/scripts/install-signed.sh\n",
                False,
                True,
                False,
            ),
            (
                "hostile CDPATH",
                "mkdir -p hermes-t3-control/scripts\n"
                "cp \"$SOURCE\" hermes-t3-control/scripts/install-signed.sh\n",
                False,
                True,
                True,
            ),
            (
                "non-regular descriptor",
                "mkdir -p hermes-t3-control/scripts\n"
                "ln -s /dev/null hermes-t3-control/scripts/install-signed.sh\n",
                False,
                False,
                False,
            ),
        )
        for name, git_body, pre_existing, should_execute, hostile_cdpath in scenarios:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                sandbox = pathlib.Path(temporary)
                command_dir = sandbox / "commands"
                command_dir.mkdir()
                marker = sandbox / "installer-ran"
                fake_git = command_dir / "git"
                fake_git.write_text("#!/bin/sh\n" + git_body, encoding="utf-8")
                fake_git.chmod(0o700)
                fake_bash = command_dir / "bash"
                fake_bash.write_text(
                    "#!/bin/sh\nprintf '%s\\n' ran > \"$MARKER\"\n",
                    encoding="utf-8",
                )
                fake_bash.chmod(0o700)
                if pre_existing:
                    script = sandbox / "hermes-t3-control" / "scripts" / INSTALL_SCRIPT_PATH.name
                    script.parent.mkdir(parents=True)
                    script.write_bytes(INSTALL_SCRIPT_PATH.read_bytes())

                environment = {
                    "MARKER": str(marker),
                    "PATH": f"{command_dir}:/usr/bin:/bin",
                    "SOURCE": str(INSTALL_SCRIPT_PATH),
                }
                if hostile_cdpath:
                    hostile_script = (
                        sandbox
                        / "hostile"
                        / "hermes-t3-control"
                        / "scripts"
                        / INSTALL_SCRIPT_PATH.name
                    )
                    hostile_script.parent.mkdir(parents=True)
                    hostile_script.write_text("tampered\n", encoding="utf-8")
                    environment["CDPATH"] = str(sandbox / "hostile")

                result = subprocess.run(
                    ["/usr/bin/bash", "-c", commands],
                    cwd=sandbox,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(marker.exists(), should_execute, result.stderr)
                self.assertEqual(result.returncode == 0, should_execute, result.stderr)

    def test_signed_installer_preserves_the_secured_install_flow(self) -> None:
        script = _read(INSTALL_SCRIPT_PATH)
        self.assertTrue(script.startswith("#!/usr/bin/env bash\n"))
        for command in (
            "set -eu",
            "umask 077",
            'if [ "$#" -ne 0 ]; then',
            "hermes config path",
            "Install into this Hermes profile? [y/N]",
            'case "$CONFIRM" in y|Y)',
            'VERIFY_DIR="$(mktemp -d)"',
            'trap \'rm -rf -- "$VERIFY_DIR"\' EXIT',
            'mkdir -m 700 "$VERIFY_DIR/home" "$VERIFY_DIR/xdg" "$VERIFY_DIR/repo"',
            "safe_git() {",
            "env -i",
            'PATH="$PATH"',
            "LC_ALL=C",
            'HOME="$VERIFY_DIR/home"',
            'XDG_CONFIG_HOME="$VERIFY_DIR/xdg"',
            "GIT_CONFIG_NOSYSTEM=1",
            "GIT_CONFIG_GLOBAL=/dev/null",
            "protocol.file.allow=never",
            "fetch -q --no-tags",
            "https://github.com/thetasigmaio/hermes-t3-control.git",
            "refs/tags/v1.2.1:refs/tags/v1.2.1",
            "gpg.format=ssh",
            "gpg.ssh.allowedSignersFile=/dev/null",
            "verify-tag --raw v1.2.1",
            "SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4",
            "rev-parse --verify 'v1.2.1^{}'",
            "Release tag signature did not match the pinned signer.",
            "grep -Eq '^[0-9a-f]{40}$'",
            "hermes config set plugins.scan_on_install true",
            'hermes plugins install thetasigmaio/hermes-t3-control --ref "$HERMES_T3_CONTROL_REF" --no-enable',
            "hermes plugins doctor hermes-t3-control --ci",
            "hermes plugins enable hermes-t3-control --no-allow-tool-override",
        ):
            with self.subTest(command=command):
                self.assertIn(command, script)
        ordered_commands = (
            "hermes config path",
            "Install into this Hermes profile? [y/N]",
            "verify-tag --raw v1.2.1",
            "rev-parse --verify 'v1.2.1^{}'",
            "hermes config set plugins.scan_on_install true",
            "hermes plugins install",
            "hermes plugins doctor",
            "hermes plugins enable",
        )
        positions = [script.index(command) for command in ordered_commands]
        self.assertEqual(positions, sorted(positions))
        for command in (
            "hermes config set plugins.scan_on_install true",
            "hermes plugins install thetasigmaio/hermes-t3-control",
            "hermes plugins doctor hermes-t3-control --ci",
            "hermes plugins enable hermes-t3-control --no-allow-tool-override",
        ):
            with self.subTest(single_command=command):
                self.assertEqual(script.count(command), 1)
        self.assertNotIn("$1", script)
        self.assertNotIn("${", script)
        for forbidden in (
            "--force",
            "token",
            "auth_mode",
            "auth-mode",
            "git ls-remote",
            "hermes gateway",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, script.casefold())
        syntax = subprocess.run(
            ["bash", "-n", str(INSTALL_SCRIPT_PATH)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)

    def test_after_install_is_a_minimal_supported_next_step(self) -> None:
        after_install = _read(AFTER_INSTALL_PATH)
        self.assertLessEqual(len(after_install.splitlines()), 30)
        for phrase in (
            "# Hermes T3 Control: next steps",
            "After the supported `--no-enable` install, the plugin is disabled.",
            "hermes plugins doctor hermes-t3-control --ci",
            "hermes plugins enable hermes-t3-control --no-allow-tool-override",
            "hermes gateway restart",
            "hermes gateway status",
            "Call only `t3_threads` with `{}`; do not call mutation tools.",
            "first non-thread-mutating prompt",
            "bundled `README.md` and `docs/operations.md`",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, after_install)
        self.assertNotIn("T3_ORCHESTRATION_TOKEN", after_install)
        self.assertNotIn("--force", after_install)
        universal = after_install.split("If a managed messaging gateway", 1)[0]
        self.assertNotIn("hermes gateway", universal)
        self.assertNotIn("https://github.com/", after_install)

    def test_everyday_examples_are_neutral_bounded_and_schema_valid(self) -> None:
        readme = _read(README_PATH)
        tools = _read(TOOLS_PATH)
        all_copy = readme + tools
        self.assertNotIn("solarsim", all_copy.casefold())
        for phrase in (
            '"project": "demo-app"',
            '"title_query": "release check"',
            '"require_one": true',
            '"busy_policy": "queue"',
            '"until": "terminal"',
            "zero or multiple matches",
            "never creates a replacement thread",
            "accepted_pending_projection",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, all_copy)

        named_examples = tuple(
            (tool_name, json.loads(block))
            for tool_name, block in re.findall(
                r"(?m)^Example for `(t3_[^`]+)`:.*?^```json\n(.*?)\n```",
                tools,
                re.DOTALL,
            )
        )
        self.assertEqual(
            tuple(tool_name for tool_name, _ in named_examples),
            (
                "t3_threads",
                "t3_thread_read",
                "t3_thread_send",
                "t3_thread_wait",
                "t3_thread_respond",
                "t3_thread_create",
                "t3_thread_implement_plan",
            ),
        )
        self.assertEqual(
            len(re.findall(r"```json\n(.*?)\n```", tools, re.DOTALL)),
            len(named_examples),
        )
        for tool_name, example in named_examples:
            with self.subTest(tool=tool_name):
                parameters = schemas.SCHEMAS[tool_name]["parameters"]
                self.assertLessEqual(set(parameters["required"]), set(example))
                self.assertLessEqual(set(example), set(parameters["properties"]))
                for field, value in example.items():
                    field_schema = parameters["properties"][field]
                    if field_schema.get("type") == "string":
                        self.assertIsInstance(value, str)
                    if "enum" in field_schema:
                        self.assertIn(value, field_schema["enum"])

    def test_detailed_tool_reference_matches_the_public_eleven_tool_surface(self) -> None:
        tools = _read(TOOLS_PATH)
        table = tools.split("## Eleven tools", 1)[1].split("## Workflows", 1)[0]
        documented = tuple(re.findall(r"(?m)^\| `([^`]+)` \|", table))
        self.assertEqual(documented, schemas.TOOL_NAMES)
        for phrase in (
            "compact summaries by default",
            "material summary by default",
            "observation-only",
            "exact request ID",
            "sourceProposedPlan",
            "no atomic idle guard",
            "no atomic expected-turn guard",
            "question-id-from-t3_thread_read",
            "Answer keys must exactly match",
            "expected-turn guard for interrupt",
            "expected-session guard for stop",
            "T3 owns `sourceProposedPlan` provenance",
            "persisted T3 transition",
            't3_thread_settle {"thread_id":"exact-thread-id"}',
            "manual T3 UI-equivalent settlement",
            "native readback",
            "starting or running",
            "approval or user input is pending",
            "recent queued turn",
            "already-settled thread succeeds",
            "T3 itself clears any pin and snooze",
            "not stopped, deleted, or archived",
            "There is no public unsettle tool",
            "distinct from turn-liveness `settled`",
            "full-access",
        ):
            self.assertIn(phrase, tools)

    def test_security_and_operations_details_remain_complete_outside_readme(self) -> None:
        readme = _read(README_PATH)
        security = _read(SECURITY_PATH)
        operations = _read(OPERATIONS_PATH)
        for phrase in (
            "five-minute",
            "revoked in `finally`",
            "never enters argv",
            "numeric loopback",
            "proxies and redirects",
            "1 MiB",
            "16 MiB",
            "64 MiB",
            "eight administrative scopes",
            "single-user WSL trust boundary",
        ):
            self.assertIn(phrase, security)
        for endpoint in (
            "GET /.well-known/t3/environment",
            "GET /api/orchestration/shell",
            "GET /api/orchestration/threads/:threadId",
            "POST /api/orchestration/dispatch",
        ):
            self.assertIn(f"`{endpoint}`", security)
        for phrase in (
            "Update",
            "Rollback",
            "Uninstall",
            "accepted_pending_projection",
            "mutation_ambiguous",
            "auth_cleanup: failed",
            "Published asset verification",
            "Development verification",
            "uv sync --frozen",
            "first non-thread-mutating check",
        ):
            self.assertIn(phrase, operations)
        self.assertNotIn("GET /.well-known/t3/environment", readme)
        self.assertNotRegex(security, r"(?m)^\s*(?:export\s+)?T3_ORCHESTRATION_TOKEN\s*=")

    def test_release_download_remains_private_https_only_and_checksum_first(self) -> None:
        operations = _read(OPERATIONS_PATH)
        blocks = [
            block
            for block in re.findall(r"```bash\n(.*?)\n```", operations, re.DOTALL)
            if "/releases/download/" in block
        ]
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertIn("umask 077", block)
        self.assertRegex(block, r'RELEASE_DIR="\$\(mktemp -d\)"')
        self.assertIn("trap 'rm -rf -- \"$RELEASE_DIR\"' EXIT", block)
        self.assertEqual(block.count("--proto '=https'"), 2)
        self.assertEqual(block.count("--proto-redir '=https'"), 2)
        self.assertIn("hermes-t3-control-1.2.1.tar.gz.sha256", block)
        self.assertIn("scripts/verify_release.py", block)
        self.assertIn("verify-tag --raw v1.2.1", block)
        self.assertIn("SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4", block)
        self.assertIn("scripts/build_release.py", block)
        self.assertIn("cmp --", block)
        self.assertIn("Release tag signature did not match the pinned signer.", block)
        self.assertNotIn("--output dist/", block)

    def test_community_index_candidate_is_valid_and_blocker_is_truthful(self) -> None:
        entry = json.loads(_read(INDEX_ENTRY_PATH))
        self.assertEqual(
            entry,
            {
                "name": "hermes-t3-control",
                "description": "Control T3 work from Hermes; core thread lifecycle verified end-to-end with Codex.",
                "author": "Jakub Sladek",
                "tags": ["t3", "codex", "orchestration"],
                "repo": "thetasigmaio/hermes-t3-control",
                "ref": "8f42cb301fa065465e7d50e04e99577c328717f0",
                "homepage": "https://github.com/thetasigmaio/hermes-t3-control",
                "capabilities": ["tools"],
                "api_version": 1,
                "added_at": "2026-08-22",
            },
        )
        self.assertRegex(entry["ref"], r"^[0-9a-f]{40}$")
        index = _read(INDEX_PATH)
        for phrase in (
            "Submission status: externally blocked",
            "NousResearch/hermes-plugin-index",
            "returns HTTP 404",
            "hermes plugins search t3 --json --refresh",
            '"source": "seed"',
            "issues/86154",
            "issues/87565",
            "pull/87627",
            "pull/86214",
            "Do not submit",
            "Update `ref` to the exact new release commit",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, index)

    def test_document_links_exist_and_copy_has_no_machine_specific_paths(self) -> None:
        readme = _read(README_PATH)
        for link in re.findall(r"\[[^]]+\]\(([^)]+)\)", readme):
            if "://" not in link and not link.startswith("#"):
                self.assertTrue((ROOT / link.split("#", 1)[0]).is_file(), link)
        copy = "\n".join(
            _read(path)
            for path in (
                README_PATH,
                AFTER_INSTALL_PATH,
                TOOLS_PATH,
                COMPATIBILITY_PATH,
                SECURITY_PATH,
                OPERATIONS_PATH,
                INDEX_PATH,
            )
        )
        machine_paths = re.compile(
            r"/home/[^/\s]+/|/Users/[^/\s]+/|/mnt/[a-z]/|"
            r"[A-Za-z]:\\Users\\|\\\\wsl(?:\.localhost)?\\",
            re.IGNORECASE,
        )
        self.assertNotRegex(copy, machine_paths)
        self.assertNotIn("jaymade", copy.casefold())
        self.assertNotIn("jakub", copy.casefold())


if __name__ == "__main__":
    unittest.main()
