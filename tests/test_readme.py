from __future__ import annotations

import json
import pathlib
import re
import unittest

import schemas


ROOT = pathlib.Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
LOWER = README.lower()


class ReadmeContractTests(unittest.TestCase):
    def test_release_compatibility_and_pinned_install_are_exact(self) -> None:
        for phrase in (
            "# Hermes T3 Control 1.2.0",
            "synchronous native directory plugin",
            "Hermes 0.20.4 and 0.20.5",
            "Python 3.11-3.13",
            "0.0.34-nightly.20260820.1141",
            "Manifest version 1 is deliberate",
            "[MIT License](LICENSE)",
            "hermes config set plugins.scan_on_install true",
            "hermes config get plugins.scan_on_install --json",
            "does not need `--force`",
            "v1.1.0 is not an installable rollback target on Hermes 0.20.4 or 0.20.5",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)
        install = (
            'hermes plugins install thetasigmaio/hermes-t3-control --ref '
            '"$HERMES_T3_CONTROL_REF" --no-enable'
        )
        self.assertEqual(README.count(install), 2)
        self.assertNotIn("--force", install)

    def test_zero_copy_setup_activation_restart_and_first_read_are_literal(self) -> None:
        for command in (
            "hermes config path",
            "hermes config set plugins.entries.hermes-t3-control.settings.auth_mode local-cli",
            "hermes config set plugins.entries.hermes-t3-control.settings.default_runtime_mode approval-required",
            "hermes config get plugins.entries.hermes-t3-control.settings --json",
            "hermes plugins doctor hermes-t3-control --ci",
            "hermes plugins enable hermes-t3-control --no-allow-tool-override",
            "hermes plugins show hermes-t3-control",
            "hermes gateway restart",
            "hermes gateway status",
            "hermes serve --status",
        ):
            with self.subTest(command=command):
                self.assertIn(command, README)
        self.assertIn("registrations: 10 tool(s), 0 hook(s)", README)
        self.assertIn("Doctor runs while the plugin is still disabled", README)
        self.assertIn(
            "Call only `t3_threads` with `{}`; do not call mutation tools.",
            README,
        )
        self.assertIn("fresh Hermes process or session", README)
        self.assertIn("reconnecting to the same process is insufficient", README)
        self.assertNotIn("network-blocked", LOWER)

    def test_exact_ten_tool_reference_matches_schemas(self) -> None:
        table = README.split("## Ten-tool reference", 1)[1].split(
            "## Safety and recovery", 1
        )[0]
        documented = tuple(re.findall(r"(?m)^\| `([^`]+)` \|", table))
        self.assertEqual(documented, schemas.TOOL_NAMES)
        for phrase in (
            "compact summaries by default",
            "explicit `raw` view",
            "material summary by default",
            "`busy_policy` defaults to `reject`",
            "observation-only",
            "explicit `queue` acknowledges that T3 may start or queue",
            "`timeout_seconds` 0-30",
            "exact request ID",
            "approval-required`, `auto-accept-edits`, `auto`, and `full-access`",
            "Interaction modes are `default` and `plan`",
            "120000 JavaScript UTF-16 code units",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)

    def test_every_json_example_parses_and_is_structurally_valid_for_named_tool(self) -> None:
        named_examples = tuple(
            (tool_name, json.loads(block))
            for tool_name, block in re.findall(
                r"(?m)^Example for `(t3_[^`]+)`:.*?^```json\n(.*?)\n```",
                README,
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
        all_json_blocks = re.findall(r"```json\n(.*?)\n```", README, re.DOTALL)
        self.assertEqual(len(all_json_blocks), len(named_examples))
        for tool_name, example in named_examples:
            with self.subTest(tool=tool_name):
                self.assertIn(tool_name, schemas.SCHEMAS)
                parameters = schemas.SCHEMAS[tool_name]["parameters"]
                self.assertIsInstance(example, dict)
                self.assertLessEqual(set(parameters["required"]), set(example))
                self.assertLessEqual(set(example), set(parameters["properties"]))
                for field, value in example.items():
                    field_schema = parameters["properties"][field]
                    if field_schema.get("type") == "string":
                        self.assertIsInstance(value, str)
                    if "enum" in field_schema:
                        self.assertIn(value, field_schema["enum"])

        examples_by_tool = dict(named_examples)
        self.assertIn("answers", examples_by_tool["t3_thread_respond"])
        self.assertIn("turn_id", examples_by_tool["t3_thread_respond"])
        self.assertNotIn("decision", examples_by_tool["t3_thread_respond"])

    def test_v12_migration_and_immutable_upstream_races_are_explicit(self) -> None:
        for phrase in (
            "v1.2 migration",
            '`t3_threads {"view":"raw"}`',
            '`t3_thread_read {"thread_id":"...","view":"raw"}`',
            "no atomic idle guard",
            "no atomic expected-turn guard",
            "best-effort current-session response",
            "full-access send",
            "full-access Plan",
            "model-facing text is truncated",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)

    def test_workflows_cover_exact_selector_continuation_wait_response_and_plan(self) -> None:
        for phrase in (
            "project `solarsim` plus a title query",
            "zero or multiple matches",
            "never silently chooses",
            "preserves the stored model selection, model options, runtime mode, interaction mode, project, branch, and worktree",
            "never creates a replacement thread",
            "queued",
            "started",
            "completed",
            "blocked",
            "accepted_pending_projection",
            "provider liveness",
            "work progress",
            "approval request",
            "user-input request",
            "Strict Plan to Build",
            "sourceProposedPlan",
            "never caller-authored plan prose",
            "required_snapshot_sequence",
            "expected_message_id",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)

    def test_security_auth_bounds_and_recovery_are_truthful(self) -> None:
        for phrase in (
            "five-minute",
            "revokes it in `finally`",
            "never enters argv",
            "never persists",
            "eight administrative scopes",
            "external-token",
            "profile-scoped `T3_ORCHESTRATION_TOKEN`",
            "numeric loopback",
            "proxies and redirects",
            "1 MiB",
            "16 MiB",
            "15-second projection window",
            "byte-identical command",
            "Never resend after a successful dispatch response",
            "`accepted_pending_projection` is not a failure",
            "`mutation_ambiguous`",
            "`network_error`",
            "`conflict`",
            "`auth_cleanup: failed`",
            "full-access` permits trusted provider work to execute commands and modify or delete files without approval",
            "`local-cli` currently requires Linux with `/proc` and `pidfd` support",
            "single-user WSL trust boundary",
            "connected socket belongs to that pinned process",
            "not a multi-user isolation mechanism",
            "mutually untrusted local users",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)
        for endpoint in (
            "GET /.well-known/t3/environment",
            "GET /api/orchestration/shell",
            "GET /api/orchestration/threads/:threadId",
            "POST /api/orchestration/dispatch",
        ):
            self.assertIn(f"`{endpoint}`", README)
        self.assertNotRegex(README, r"(?m)^\s*(?:export\s+)?T3_ORCHESTRATION_TOKEN\s*=")
        self.assertIn("non-thread-mutating first check", LOWER)
        self.assertNotIn("read-only first check", LOWER)

    def test_update_rollback_uninstall_and_optional_external_auth_are_supported(self) -> None:
        update = README.split("## Update, rollback, and uninstall", 1)[1].split(
            "## Security and credential handling", 1
        )[0]
        for command in (
            "read -r -p 'New audited 40-character commit SHA: ' HERMES_T3_CONTROL_REF",
            "hermes plugins disable hermes-t3-control",
            "hermes plugins remove hermes-t3-control",
            'hermes plugins install thetasigmaio/hermes-t3-control --ref "$HERMES_T3_CONTROL_REF" --no-enable',
            "hermes config unset plugins.entries.hermes-t3-control",
        ):
            self.assertIn(command, update)
        self.assertNotIn("hermes plugins uninstall hermes-t3-control", README)
        self.assertIn("operator-isolated headless or local deployment", LOWER)
        self.assertIn("base_url", README)
        self.assertIn(
            "hermes config set plugins.entries.hermes-t3-control.settings.auth_mode external-token",
            README,
        )
        self.assertIn(
            "hermes config set plugins.entries.hermes-t3-control.settings.base_url http://127.0.0.1:3773",
            README,
        )
        self.assertIn("hermes config env-path", README)
        self.assertIn("orchestration:read", README)
        self.assertIn("orchestration:operate", README)
        self.assertNotIn("use explicit `external-token` mode in a multi-user distro", README)

    def test_published_assets_use_a_private_fresh_https_only_directory(self) -> None:
        self.assertIn(
            "trusted v1.2.0 source checkout containing `scripts/verify_release.py`",
            README,
        )
        blocks = [
            block
            for block in re.findall(r"```bash\n(.*?)\n```", README, re.DOTALL)
            if "/releases/download/" in block
        ]
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertIn("umask 077", block)
        self.assertRegex(block, r'RELEASE_DIR="\$\(mktemp -d\)"')
        self.assertIn("trap 'rm -rf -- \"$RELEASE_DIR\"' EXIT", block)
        self.assertEqual(block.count("--proto '=https'"), 2)
        self.assertEqual(block.count("--proto-redir '=https'"), 2)
        self.assertNotIn("--output dist/", block)
        self.assertIn('"$RELEASE_DIR/hermes-t3-control-1.2.0.tar.gz"', block)
        self.assertIn('"$RELEASE_DIR/hermes-t3-control-1.2.0.tar.gz.sha256"', block)
        self.assertIn(
            'python3 -B scripts/verify_release.py "$RELEASE_DIR/hermes-t3-control-1.2.0.tar.gz.sha256"',
            block,
        )

    def test_development_gates_and_fresh_process_claim_are_precise(self) -> None:
        for command in (
            "PYTHONWARNINGS=error python3.11 -B -m unittest discover -s tests -v",
            "env HERMES_SUPPORTED_INSTALL_TEST=1 PYTHONDONTWRITEBYTECODE=1 python3.11 -B -m unittest -v tests.test_supported_install",
            "env PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor . --ci",
            "python3 -B scripts/build_release.py --output-dir dist",
            "python3 -B scripts/verify_release.py dist/hermes-t3-control-1.2.0.tar.gz.sha256",
        ):
            self.assertEqual(README.count(command), 1, command)
        self.assertIn("`uv sync --frozen`", README)
        self.assertIn("expected loopback tcp connection", LOWER)
        self.assertNotIn("network-blocked", LOWER)

    def test_repository_paths_and_unpublished_status_are_safe(self) -> None:
        self.assertNotIn(".claude/spec", LOWER)
        self.assertNotIn(".codex/spec", LOWER)
        machine_paths = re.compile(
            r"/home/[^/\s]+/|/Users/[^/\s]+/|/mnt/[a-z]/|"
            r"[A-Za-z]:\\Users\\|\\\\wsl(?:\.localhost)?\\",
            re.IGNORECASE,
        )
        self.assertNotRegex(README, machine_paths)


if __name__ == "__main__":
    unittest.main()
