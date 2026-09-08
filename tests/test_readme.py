from __future__ import annotations

import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
OPERATIONS = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
EXPERIMENTAL = (ROOT / "docs" / "experimental-continuation.md").read_text(
    encoding="utf-8"
)
INSTALLER_PATH = ROOT / "scripts" / "install-signed.sh"
INSTALLER = INSTALLER_PATH.read_text(encoding="utf-8")
EXACT_BASE = "63279301bcbdc185c1b07b98a9312eb0c862f26d"
PINNED_SIGNER = "SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4"
REVISION = "a" * 40


def _run_installer(
    *,
    answer: str = "y\n",
    tag_type: str = "tag",
    tag_name: str = "v1.3.1",
    signature: str = PINNED_SIGNER,
    revision: str = REVISION,
    revision_status: int = 0,
    fail_action: str = "",
    scan_setting: str = "true",
    set_noop: bool = False,
    enable_noop: bool = False,
    disable_noop: bool = False,
    enable_fail_after_write: bool = False,
    plugin_list_mode: str = "normal",
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    with tempfile.TemporaryDirectory() as temporary:
        base = pathlib.Path(temporary)
        fake_bin = base / "bin"
        fake_bin.mkdir()
        hermes_log = base / "hermes.log"
        git_log = base / "git.log"
        scan_state = base / "scan.state"
        plugin_state = base / "plugin.state"
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' \"$*\" >> {git_log!s}\n"
            "for argument in \"$@\"; do\n"
            "  case \"$argument\" in\n"
            f"    cat-file) printf '%s\\n' {tag_type!r}; exit 0 ;;\n"
            f"    for-each-ref) printf '%s\\n' {tag_name!r}; exit 0 ;;\n"
            "    verify-tag) printf '%s\\n' "
            f"'Good \"git\" signature with ED25519 key {signature}'; exit 0 ;;\n"
            f"    rev-parse) printf '%s\\n' {revision!r}; exit {revision_status} ;;\n"
            "  esac\n"
            "done\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_hermes = fake_bin / "hermes"
        fake_hermes.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$HERMES_LOG\"\n"
            "if [ \"$*\" = 'config path' ]; then\n"
            "  printf '%s\\n' '/isolated/hermes/config.yaml'\n"
            "fi\n"
            "if [ -n \"$FAIL_ACTION\" ] && [ \"$*\" = \"$FAIL_ACTION\" ]; then exit 9; fi\n"
            "if [ \"$*\" = 'config get plugins.scan_on_install --json' ]; then\n"
            "  if [ -f \"$SCAN_STATE\" ]; then cat \"$SCAN_STATE\"; exit 0; fi\n"
            "  case \"$SCAN_SETTING\" in\n"
            "    true|false) printf '%s\\n' \"$SCAN_SETTING\"; exit 0 ;;\n"
            "    unset) printf '%s\\n' 'Config key not set: plugins.scan_on_install' >&2; exit 1 ;;\n"
            "    string_false) printf '%s\\n' '\"false\"'; exit 0 ;;\n"
            "    malformed) printf '%s\\n' 'private parse warning' 'Config key not set: plugins.scan_on_install' >&2; exit 1 ;;\n"
            "    error) printf '%s\\n' 'private config failure' >&2; exit 2 ;;\n"
            "  esac\n"
            "fi\n"
            "if [ \"$*\" = 'config set plugins.scan_on_install true' ] && [ \"$SET_NOOP\" != 1 ]; then\n"
            "  printf '%s\\n' true > \"$SCAN_STATE\"\n"
            "fi\n"
            "if [ \"$*\" = 'config set plugins.scan_on_install false' ]; then\n"
            "  printf '%s\\n' false > \"$SCAN_STATE\"\n"
            "fi\n"
            "if [ \"$*\" = 'config unset plugins.scan_on_install' ]; then\n"
            "  rm -f -- \"$SCAN_STATE\"\n"
            "fi\n"
            "case \"$*\" in\n"
            "  'plugins install '*) printf '%s\\n' disabled > \"$PLUGIN_STATE\" ;;\n"
            "  'plugins enable hermes-t3-control --no-allow-tool-override')\n"
            "    if [ \"$ENABLE_FAIL_AFTER_WRITE\" = 1 ]; then\n"
            "      printf '%s\\n' enabled > \"$PLUGIN_STATE\"; exit 9\n"
            "    fi\n"
            "    if [ \"$ENABLE_NOOP\" != 1 ]; then printf '%s\\n' enabled > \"$PLUGIN_STATE\"; fi ;;\n"
            "  'plugins disable hermes-t3-control')\n"
            "    if [ \"$DISABLE_NOOP\" != 1 ]; then printf '%s\\n' disabled > \"$PLUGIN_STATE\"; fi ;;\n"
            "  'plugins list --user --enabled --json')\n"
            "    case \"$PLUGIN_LIST_MODE\" in\n"
            "      error) printf '%s\\n' 'private inventory failure' >&2; exit 2 ;;\n"
            "      malformed) printf '%s\\n' '{}'; exit 0 ;;\n"
            "    esac\n"
            "    if [ -f \"$PLUGIN_STATE\" ] && [ \"$(cat \"$PLUGIN_STATE\")\" = enabled ]; then\n"
            "      printf '%s\\n' '[{\"name\":\"hermes-t3-control\"}]'\n"
            "    else\n"
            "      printf '%s\\n' '[]'\n"
            "    fi ;;\n"
            "esac\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o700)
        fake_hermes.chmod(0o700)
        environment = {
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HOME": str(base / "home"),
            "HERMES_LOG": str(hermes_log),
            "FAIL_ACTION": fail_action,
            "SCAN_SETTING": scan_setting,
            "SCAN_STATE": str(scan_state),
            "SET_NOOP": "1" if set_noop else "0",
            "PLUGIN_STATE": str(plugin_state),
            "ENABLE_NOOP": "1" if enable_noop else "0",
            "DISABLE_NOOP": "1" if disable_noop else "0",
            "ENABLE_FAIL_AFTER_WRITE": "1" if enable_fail_after_write else "0",
            "PLUGIN_LIST_MODE": plugin_list_mode,
        }
        result = subprocess.run(
            ["bash", str(INSTALLER_PATH)],
            cwd=base,
            env=environment,
            input=answer,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        hermes_calls = (
            hermes_log.read_text(encoding="utf-8").splitlines()
            if hermes_log.exists()
            else []
        )
        git_calls = (
            git_log.read_text(encoding="utf-8").splitlines()
            if git_log.exists()
            else []
        )
        return result, hermes_calls, git_calls


class ReadmeContractTests(unittest.TestCase):
    def test_readme_has_the_short_three_step_onboarding(self) -> None:
        self.assertLessEqual(len(README.splitlines()), 100)
        for heading in (
            "# Hermes T3 Control",
            "## Before you start",
            "## 1. Install",
            "## 2. Reload Hermes",
            "## 3. Try it",
            "## What you can do",
            "## Experimental continuation",
            "## Help and reference",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, README)
        self.assertIn("v1.3.1/install-t3.sh", README)
        self.assertEqual(README.count("bash install-t3.sh"), 1)
        self.assertIn("> Show my T3 threads.", README)
        self.assertNotIn("/dev/fd", README)
        self.assertNotIn("sha256sum", README)
        self.assertNotIn("registrations:", README)
        self.assertIn("Git that supports SSH signature verification", README)

    def test_readme_states_the_plain_bootstrap_and_experimental_boundaries(self) -> None:
        self.assertIn("trusts the installer published by this project on GitHub over HTTPS", README)
        self.assertIn("Automatic updates into the same Hermes conversation are experimental and off by default", README)
        self.assertIn("a separate patch for one exact Hermes revision", README)
        self.assertNotIn(EXACT_BASE, README)
        self.assertNotIn("Sunsama", README)
        self.assertNotIn("private routing metadata", README)

    def test_installer_retains_the_signed_disabled_validation_boundary(self) -> None:
        for required in (
            "refs/tags/v1.3.1:refs/tags/v1.3.1",
            "cat-file -t refs/tags/v1.3.1",
            "for-each-ref --format='%(tag)' refs/tags/v1.3.1",
            "verify-tag --raw v1.3.1",
            PINNED_SIGNER,
            "plugins.scan_on_install true",
            "--no-enable",
            "plugins doctor hermes-t3-control --ci",
            "--no-allow-tool-override",
            "plugins list --user --enabled --json",
            "v1.3.1^{commit}",
        ):
            with self.subTest(required=required):
                self.assertIn(required, INSTALLER)
        self.assertNotIn("--force", INSTALLER)
        self.assertNotIn("gateway restart", INSTALLER)
        self.assertNotIn("plugins remove", INSTALLER)

    def test_downloadable_assets_are_authenticated_by_signed_source(self) -> None:
        for guide in (OPERATIONS, EXPERIMENTAL):
            self.assertIn("verify-tag --raw v1.3.1", guide)
            self.assertIn("for-each-ref --format='%(tag)' refs/tags/v1.3.1", guide)
            self.assertIn(PINNED_SIGNER, guide)
            self.assertIn("release/v1.3.1.sha256", guide)
            self.assertIn("--proto '=https'", guide)
            self.assertIn("cmp --", guide)
        self.assertIn("hermes-t3-control-1.3.1.tar.gz", OPERATIONS)
        self.assertIn("install-t3.sh.sha256", OPERATIONS)
        self.assertIn('$RELEASE_REF:scripts/install-signed.sh', OPERATIONS)
        self.assertIn("hermes-gateway-continuation-1.3.1.patch", EXPERIMENTAL)
        self.assertIn("v1.3.1^{commit}", OPERATIONS)
        self.assertIn("v1.3.1^{commit}", EXPERIMENTAL)

    def test_experimental_manual_patch_contract_is_exact(self) -> None:
        self.assertIn(EXACT_BASE, EXPERIMENTAL)
        self.assertIn("git apply --check", EXPERIMENTAL)
        self.assertIn("git diff --check", EXPERIMENTAL)
        self.assertIn("uv sync --frozen --python 3.11 --extra dev", EXPERIMENTAL)
        self.assertIn("uv run --frozen --extra dev pytest -q", EXPERIMENTAL)
        self.assertNotIn("--with pytest", EXPERIMENTAL)
        self.assertIn("t3-continuation bind", EXPERIMENTAL)
        self.assertIn("t3-continuation renew --replaces OLD_MISSION_ID", EXPERIMENTAL)
        self.assertIn("every bind argument supplied again", EXPERIMENTAL)
        self.assertIn("captures a fresh registration baseline", EXPERIMENTAL)
        self.assertIn("including when the cursor is zero", EXPERIMENTAL)
        self.assertIn("registration, not a running observer", EXPERIMENTAL)
        self.assertIn("t3-continuation stop OLD_MISSION_ID", EXPERIMENTAL)
        self.assertIn("cannot resume", EXPERIMENTAL)
        self.assertIn("migrated v3 ledger to older continuation code is unsupported", EXPERIMENTAL)
        self.assertIn("different table shape are rejected without migration", EXPERIMENTAL)

    def test_public_docs_do_not_contain_workstation_aliases_or_private_paths(self) -> None:
        public = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")))
        )
        for private_pattern in (
            r"codex_[0-9]+x",
            r"gpt-[0-9]+\.[0-9]+-[a-z]+",
            r"/home/[a-z0-9_-]+/Projects/",
            r"/mnt/[a-z]/Users/[a-z0-9_-]+/",
        ):
            with self.subTest(private_pattern=private_pattern):
                self.assertIsNone(re.search(private_pattern, public, re.IGNORECASE))
        self.assertIsNone(
            re.search(
                r"(?i)(?:token|secret|password)\s*[:=]\s*[A-Za-z0-9_-]{16,}",
                public,
            )
        )


class InstallerBehaviorTests(unittest.TestCase):
    def test_success_runs_the_verified_disabled_doctor_enable_sequence(self) -> None:
        result, calls, _ = _run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls,
            [
                "config path",
                "config get plugins.scan_on_install --json",
                f"plugins install thetasigmaio/hermes-t3-control --ref {REVISION} --no-enable",
                "plugins doctor hermes-t3-control --ci",
                "plugins enable hermes-t3-control --no-allow-tool-override",
                "plugins list --user --enabled --json",
            ],
        )
        self.assertIn("Hermes T3 Control 1.3.1 is installed and enabled.", result.stdout)

    def test_decline_stops_before_git_or_mutation(self) -> None:
        result, calls, git_calls = _run_installer(answer="n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["config path"])
        self.assertEqual(git_calls, [])

    def test_wrong_signature_stops_before_config_mutation(self) -> None:
        result, calls, _ = _run_installer(signature="SHA256:wrong")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["config path"])
        self.assertIn("signature did not match", result.stderr)

    def test_valid_old_signed_tag_under_new_ref_is_rejected(self) -> None:
        result, calls, git_calls = _run_installer(tag_name="v1.3.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["config path"])
        self.assertTrue(any("verify-tag --raw v1.3.1" in call for call in git_calls))
        self.assertIn("tag name did not match", result.stderr)

    def test_noncommit_tag_target_stops_before_config_mutation(self) -> None:
        result, calls, git_calls = _run_installer(revision_status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["config path"])
        self.assertTrue(
            any("rev-parse --verify v1.3.1^{commit}" in call for call in git_calls)
        )

    def test_install_failure_preserves_preexisting_plugin_and_restores_scan(self) -> None:
        install = f"plugins install thetasigmaio/hermes-t3-control --ref {REVISION} --no-enable"
        result, calls, _ = _run_installer(fail_action=install, scan_setting="false")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("config set plugins.scan_on_install false", calls)
        self.assertNotIn("plugins disable hermes-t3-control", calls)
        self.assertFalse(any(call.startswith("plugins remove ") for call in calls))

    def test_scanner_set_success_without_readback_change_stops_before_install(self) -> None:
        result, calls, _ = _run_installer(scan_setting="false", set_noop=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            calls,
            [
                "config path",
                "config get plugins.scan_on_install --json",
                "config set plugins.scan_on_install true",
                "config get plugins.scan_on_install --json",
                "config get plugins.scan_on_install --json",
            ],
        )
        self.assertIn("did not enable install-time scanning", result.stderr)

    def test_scanner_read_error_stops_before_mutation(self) -> None:
        result, calls, _ = _run_installer(scan_setting="error")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            calls, ["config path", "config get plugins.scan_on_install --json"]
        )
        self.assertNotIn("private config failure", result.stderr)

    def test_exact_unset_scan_setting_is_enabled_for_install(self) -> None:
        result, calls, _ = _run_installer(scan_setting="unset")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls.count("config set plugins.scan_on_install true"), 1)
        self.assertNotIn("config unset plugins.scan_on_install", calls)

    def test_unset_scan_setting_is_restored_after_install_failure(self) -> None:
        install = f"plugins install thetasigmaio/hermes-t3-control --ref {REVISION} --no-enable"
        result, calls, _ = _run_installer(
            scan_setting="unset", fail_action=install
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("config unset plugins.scan_on_install", calls)
        self.assertEqual(calls[-1], "config get plugins.scan_on_install --json")

    def test_nonboolean_or_noisy_missing_scan_setting_fails_closed(self) -> None:
        for scan_setting in ("string_false", "malformed"):
            with self.subTest(scan_setting=scan_setting):
                result, calls, _ = _run_installer(scan_setting=scan_setting)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    calls,
                    ["config path", "config get plugins.scan_on_install --json"],
                )
                self.assertNotIn("private parse warning", result.stderr)

    def test_doctor_failure_leaves_new_plugin_disabled(self) -> None:
        result, calls, _ = _run_installer(
            fail_action="plugins doctor hermes-t3-control --ci"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plugins disable hermes-t3-control", calls)
        self.assertIn("plugins list --user --enabled --json", calls)
        self.assertNotIn(
            "plugins enable hermes-t3-control --no-allow-tool-override", calls
        )
        self.assertIn("remains installed but disabled", result.stderr)

    def test_enable_failure_has_no_success_message_and_leaves_plugin_disabled(self) -> None:
        result, calls, _ = _run_installer(
            fail_action="plugins enable hermes-t3-control --no-allow-tool-override"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1], "plugins list --user --enabled --json")
        self.assertNotIn("is installed and enabled", result.stdout)
        self.assertIn("remains installed but disabled", result.stderr)

    def test_enable_success_without_persistence_is_rejected(self) -> None:
        result, calls, _ = _run_installer(enable_noop=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls.count("plugins list --user --enabled --json"), 2)
        self.assertNotIn("is installed and enabled", result.stdout)

    def test_cleanup_does_not_claim_disabled_when_disable_did_not_persist(self) -> None:
        result, calls, _ = _run_installer(
            enable_fail_after_write=True, disable_noop=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plugins disable hermes-t3-control", calls)
        self.assertIn(
            "could not confirm that the new plugin is disabled", result.stderr
        )
        self.assertNotIn("remains installed but disabled", result.stderr)

    def test_enabled_inventory_parse_failure_fails_closed(self) -> None:
        result, calls, _ = _run_installer(plugin_list_mode="malformed")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls.count("plugins list --user --enabled --json"), 2)
        self.assertNotIn("is installed and enabled", result.stdout)


if __name__ == "__main__":
    unittest.main()
