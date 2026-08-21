from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
LOWER = README.lower()


class ReadmeContractTests(unittest.TestCase):
    def test_documents_release_targets_and_install_disabled_by_default(self) -> None:
        for phrase in (
            "Hermes T3 Control 1.1.0",
            "native directory plugin",
            "Hermes 0.20.4 and 0.20.5",
            "Python 3.11-3.13",
            "0.0.34-nightly.20260820.1141",
            "[MIT License](LICENSE)",
            "thetasigmaio/hermes-t3-control",
            "Git installation is disabled by default",
            "--ref <40-character-release-commit-sha> --no-enable",
            "hermes plugins enable hermes-t3-control",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)

    def test_documents_profile_secret_and_configuration_boundaries(self) -> None:
        for phrase in (
            "masked `requires_env` prompt",
            "selected Hermes profile",
            "Never put the token in argv",
            "T3_ORCHESTRATION_TOKEN",
            "orchestration:read",
            "orchestration:operate",
            "plugins.entries.hermes-t3-control.settings",
            "base_url: http://127.0.0.1:9137",
            "numeric loopback",
            "default_runtime_mode: full-access",
            "conservative fallback is `approval-required`",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)
        self.assertIn(
            "full-access` permits trusted provider work to execute commands and modify or delete files without approval",
            README,
        )
        self.assertNotRegex(README, r"(?m)^\s*(?:export\s+)?T3_ORCHESTRATION_TOKEN\s*=")
        self.assertNotRegex(LOWER, r"(?m)^\s*.*(?:--token|token=)[^`\n]+")

    def test_documents_exact_eight_tool_surface_and_inputs(self) -> None:
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
        self.assertIn("exactly eight non-overriding tools", README)
        for name in tools:
            with self.subTest(name=name):
                self.assertIn(f"`{name}`", README)
        for input_name in (
            "project_id",
            "title",
            "instance_id",
            "model",
            "model_options",
            "runtime_mode",
            "interaction_mode",
            "initial_message",
            "branch",
            "worktree_path",
            "thread_id",
            "message",
            "plan_id",
            "turn_limit",
            "before_cursor",
        ):
            with self.subTest(input_name=input_name):
                self.assertIn(input_name, README)
        self.assertIn("approval-required`, `auto-accept-edits`, `auto`, and `full-access`", README)
        self.assertIn("interaction modes are `default` and `plan`", README)

    def test_documents_create_options_metadata_and_same_thread_resume(self) -> None:
        for phrase in (
            "canonical options are preserved",
            "A different explicit pair never inherits selection-specific options",
            "metadata only",
            "does not inspect Git, resolve a branch, or create a worktree",
            "exact create readback first",
            "same model selection, runtime mode, and interaction mode",
            "separate create and turn command IDs",
            "no atomic expected-mode guard",
            "same T3 thread",
            "never re-resolves the creation default",
            "never creates a replacement thread",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)

    def test_documents_native_plan_to_build_provenance_and_race(self) -> None:
        for phrase in (
            "Native same-thread Plan to Build",
            "returned server plan ID",
            "detail.thread.proposedPlans[].id",
            "`t3_thread_implement_plan` with only the same thread and returned server plan ID",
            "thread.interaction-mode.set",
            "sourceProposedPlan: {threadId, planId}",
            "latestTurn.sourceProposedPlan",
            "source_proposed_plan",
            "implementedAt",
            "implementationThreadId",
            "not at-most-once implementation",
            "duplicate implementation work",
            "does not roll the mode back",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)
        self.assertNotIn('"plan_markdown"', README)
        self.assertNotIn('"plan_text"', README)

    def test_documents_mode_non_retroactivity_interrupt_and_stop(self) -> None:
        for phrase in (
            "exactly one typed field",
            "does **not** cancel, remove, or retroactively authorize an approval already created",
            "does not prove that the running provider session changed mode",
            "no atomic expected-turn guard",
            "concurrent_state_change",
            "no expected-session identity guard",
            "never deletes or replaces the thread",
            "resumes the same thread",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, README)

    def test_documents_transport_idempotency_limits_and_exclusions(self) -> None:
        for phrase in (
            "fresh UUIDv4 command ID",
            "byte-identical body with that same command ID",
            "mutation_ambiguous",
            "verification_failed",
            "10-second absolute monotonic deadline",
            "30 seconds",
            "three dispatch attempts",
            "1 MiB",
            "16 MiB",
            "120000 JavaScript UTF-16 code units",
            "never follows redirects",
            "does not issue or persist tokens",
            "SQLite",
            "event-log JSONL",
            "process credentials",
            "raw dispatch",
            "WebSocket control",
            "upload attachments",
            "answer approvals",
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

    def test_contains_exact_portable_verification_and_release_patterns(self) -> None:
        commands = (
            "python3 -B -m unittest discover -s tests -v",
            "env PYTHONDONTWRITEBYTECODE=1 hermes plugins doctor . --ci",
            "python3 -B scripts/build_release.py --output-dir dist",
            "python3 -B scripts/verify_release.py dist/hermes-t3-control-1.1.0.tar.gz.sha256",
            "python3 -B -m unittest -v tests.test_client",
            "python3 -B -m unittest -v tests.test_tools tests.test_registration",
            "python3 -B -m unittest -v tests.test_release",
            "python3 -B -m unittest -v tests.test_readme",
            "python3 -B scripts/live_smoke.py",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(README.count(command), 1)
        for url in (
            "https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.1.0/hermes-t3-control-1.1.0.tar.gz",
            "https://github.com/thetasigmaio/hermes-t3-control/releases/download/v1.1.0/hermes-t3-control-1.1.0.tar.gz.sha256",
        ):
            with self.subTest(url=url):
                self.assertIn(url, README)
        for env_name in (
            "T3_SMOKE_ISOLATED=1",
            "T3_SMOKE_THREAD_ID",
            "T3_ORCHESTRATION_BASE_URL",
            "T3_ORCHESTRATION_TOKEN",
        ):
            with self.subTest(env_name=env_name):
                self.assertIn(env_name, README)
        self.assertIn("one operator-designated isolated non-SolarSim thread", README)
        self.assertIn("The optional live smoke has not been run", README)

    def test_rejects_stale_spec_location_and_machine_specific_paths(self) -> None:
        self.assertNotIn(".claude/spec", LOWER)
        machine_paths = re.compile(
            r"/home/[^/\s]+/|/Users/[^/\s]+/|/mnt/[a-z]/|"
            r"[A-Za-z]:\\Users\\|\\\\wsl(?:\.localhost)?\\",
            re.IGNORECASE,
        )
        self.assertNotRegex(README, machine_paths)


if __name__ == "__main__":
    unittest.main()
