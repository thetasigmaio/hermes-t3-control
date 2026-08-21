from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
LOWER = README.lower()


class ReadmeContractTests(unittest.TestCase):
    def test_release_compatibility_and_fresh_pinned_install_are_exact(self) -> None:
        for phrase in (
            "# Hermes T3 Control 1.1.1",
            "synchronous native directory plugin",
            "Hermes 0.20.4 and 0.20.5",
            "Python 3.11-3.13",
            "0.0.34-nightly.20260820.1141",
            "Manifest version 1 is deliberate",
            "[MIT License](LICENSE)",
            "plugins.scan_on_install",
            "does not need `--force` for a fresh install",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)
        fresh_install = (
            'hermes plugins install thetasigmaio/hermes-t3-control --ref '
            '"$HERMES_T3_CONTROL_REF" --no-enable'
        )
        self.assertEqual(README.count(fresh_install), 1)
        self.assertNotIn("--force", fresh_install)
        self.assertNotIn("1.1.0", README)

    def test_configuration_activation_and_first_read_are_copy_pasteable(self) -> None:
        for command in (
            "hermes config path",
            "hermes config env-path",
            "hermes config set plugins.entries.hermes-t3-control.settings.base_url http://127.0.0.1:9137",
            "hermes config set plugins.entries.hermes-t3-control.settings.default_runtime_mode approval-required",
            "hermes config get plugins.entries.hermes-t3-control.settings --json",
            "hermes plugins doctor hermes-t3-control --ci",
            "hermes plugins enable hermes-t3-control --no-allow-tool-override",
            "hermes plugins show hermes-t3-control",
            "hermes gateway restart",
            "hermes serve --status",
            "hermes serve",
        ):
            with self.subTest(command=command):
                self.assertIn(command, README)
        self.assertIn("registrations: 8 tool(s), 0 hook(s)", README)
        self.assertIn("reconnecting to the same process is insufficient", README)
        self.assertIn("stops every Hermes web-server process", README)
        self.assertIn("first read-only verification", README)
        self.assertIn("invoke exactly `t3_threads` with `{}` before any mutation", README)
        self.assertIn("Do not use one-shot `-z` as a read-only safety boundary", README)
        self.assertIn("numeric loopback", LOWER)
        self.assertIn("Hostnames such as `localhost`", README)

    def test_documents_exact_eight_tools_defaults_and_result_shapes(self) -> None:
        tools = (
            "t3_threads",
            "t3_thread_read",
            "t3_thread_create",
            "t3_thread_send",
            "t3_thread_set_mode",
            "t3_thread_implement_plan",
            "t3_turn_interrupt",
            "t3_session_stop",
        )
        table = README.split("## Eight-tool reference", 1)[1].split(
            "## Safety and recovery", 1
        )[0]
        for name in tools:
            with self.subTest(name=name):
                self.assertIn(f"| `{name}` |", table)
        for phrase in (
            "`turn_limit` 1-150, default 20",
            "exactly one of `runtime_mode` or `interaction_mode`",
            "`thread_created` or `thread_created_with_initial_turn`",
            "`thread_mode_set` or verified `mode_already_set`",
            "`best_effort_session_stop` or verified `already_stopped`",
            "All results contain `ok`",
            "`error_code`, `retryable`, `outcome_ambiguous`",
            "approval-required`, `auto-accept-edits`, `auto`, and `full-access`",
            "Interaction modes are `default` and `plan`",
            "120000 JavaScript UTF-16 code units",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)

    def test_documents_create_send_and_strict_same_thread_plan_flow(self) -> None:
        for phrase in (
            "Call `t3_thread_create` with a project ID returned by `t3_threads`",
            "if either is supplied, both are required",
            '"runtime_mode": "approval-required"',
            '"interaction_mode": "default"',
            "same thread with its returned ID",
            "never creates a replacement thread",
            "metadata only",
            "Strict Plan to Build on the same thread",
            "never caller-authored plan prose",
            "detail.thread.proposedPlans[].id",
            '"plan_id": "server-plan-id-from-t3_thread_read"',
            "sourceProposedPlan: {threadId, planId}",
            "same-thread provenance",
            "duplicate concurrent work",
            "does not roll the mode back",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)
        self.assertNotIn('"plan_markdown"', README)
        self.assertNotIn('"plan_text"', README)

    def test_documents_full_access_races_recovery_and_security_boundary(self) -> None:
        for phrase in (
            "full-access` permits trusted provider work to execute commands and modify or delete files without approval",
            "cannot cancel, remove, or retroactively authorize an approval already pending",
            "fresh UUIDv4 command ID",
            "byte-identical command",
            "`mutation_ambiguous` or `verification_failed`",
            "`concurrent_state_change`",
            "10-second absolute monotonic deadline",
            "30 seconds",
            "three dispatch attempts",
            "1 MiB",
            "16 MiB",
            "Never put it in argv",
            "does not issue or persist credentials",
            "SQLite",
            "event-log JSONL",
            "raw dispatch or WebSocket control",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)
        for endpoint in (
            "GET /api/orchestration/shell",
            "GET /api/orchestration/threads/:threadId",
            "POST /api/orchestration/dispatch",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(f"`{endpoint}`", README)
        self.assertNotRegex(README, r"(?m)^\s*(?:export\s+)?T3_ORCHESTRATION_TOKEN\s*=")

    def test_documents_update_rollback_uninstall_and_release_verification(self) -> None:
        update_block = README.split("## Update, rollback, and uninstall", 1)[1].split(
            "## Credential and data boundary", 1
        )[0]
        commands = (
            "read -r -p 'New audited 40-character commit SHA: ' HERMES_T3_CONTROL_REF",
            "hermes plugins disable hermes-t3-control",
            'hermes plugins install thetasigmaio/hermes-t3-control --force --ref "$HERMES_T3_CONTROL_REF" --no-enable',
            "hermes plugins uninstall hermes-t3-control",
            "hermes config unset plugins.entries.hermes-t3-control",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, update_block)
        self.assertLess(
            update_block.index("hermes plugins disable hermes-t3-control"),
            update_block.index("hermes plugins install thetasigmaio/hermes-t3-control"),
        )
        self.assertIn("there is no moving-channel or automatic rollback command", README)
        self.assertIn("Plugin removal does not erase profile settings or secrets", README)
        self.assertIn("leaves `hermes-t3-control` in `plugins.disabled`", README)
        self.assertIn("hermes config edit", update_block)

        for command in (
            "python3 -B -m unittest discover -s tests -v",
            "env HERMES_SUPPORTED_INSTALL_TEST=1 PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v tests.test_supported_install",
            "env PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor . --ci",
            "python3 -B scripts/build_release.py --output-dir dist",
        ):
            with self.subTest(command=command):
                self.assertEqual(README.count(command), 1)
        for url in (
            "https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.1.1/hermes-t3-control-1.1.1.tar.gz",
            "https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.1.1/hermes-t3-control-1.1.1.tar.gz.sha256",
        ):
            with self.subTest(url=url):
                self.assertIn(url, README)
        self.assertEqual(
            README.count(
                "python3 -B scripts/verify_release.py dist/hermes-t3-control-1.1.1.tar.gz.sha256"
            ),
            2,
        )
        for command in (
            "curl --fail --location --output dist/hermes-t3-control-1.1.1.tar.gz ",
            "curl --fail --location --output dist/hermes-t3-control-1.1.1.tar.gz.sha256 ",
            "git ls-remote https://github.com/thetasigmaio/hermes-t3-control.git 'refs/tags/v1.1.1^{}'",
            'test "${#HERMES_T3_CONTROL_REF}" -eq 40',
        ):
            with self.subTest(command=command):
                self.assertIn(command, README)

    def test_live_smoke_and_repository_paths_are_safe(self) -> None:
        for name in (
            "python3 -B scripts/live_smoke.py",
            "T3_SMOKE_ISOLATED=1",
            "T3_SMOKE_THREAD_ID",
            "T3_ORCHESTRATION_BASE_URL",
            "T3_ORCHESTRATION_TOKEN",
            "operator-designated isolated non-SolarSim thread",
        ):
            with self.subTest(name=name):
                self.assertIn(name, README)
        self.assertNotIn(".claude/spec", LOWER)
        machine_paths = re.compile(
            r"/home/[^/\s]+/|/Users/[^/\s]+/|/mnt/[a-z]/|"
            r"[A-Za-z]:\\Users\\|\\\\wsl(?:\.localhost)?\\",
            re.IGNORECASE,
        )
        self.assertNotRegex(README, machine_paths)


if __name__ == "__main__":
    unittest.main()
