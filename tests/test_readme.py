from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
OPERATIONS = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
EXPERIMENTAL = (ROOT / "docs" / "experimental-continuation.md").read_text(encoding="utf-8")
INSTALLER = (ROOT / "scripts" / "install-signed.sh").read_text(encoding="utf-8")
EXACT_BASE = "63279301bcbdc185c1b07b98a9312eb0c862f26d"
PINNED_SIGNER = "SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4"


class ReadmeContractTests(unittest.TestCase):
    def test_readme_is_concise_and_covers_public_onboarding(self) -> None:
        self.assertLessEqual(len(README.splitlines()), 135)
        for heading in ("# Hermes T3 Control 1.3.0", "## Quick example", "## Verified quick start", "## Compatibility", "## Upgrade and uninstall", "## Troubleshooting", "## Security", "## Advanced guides"):
            with self.subTest(heading=heading):
                self.assertIn(heading, README)
        self.assertIn("eleven bounded tools", README)
        self.assertIn("registrations: 11 tool(s), 0 hook(s)", README)
        self.assertIn("0.0.34-nightly.20260820.1141", README)
        self.assertIn("hashed `0.0.37` shape", README)
        self.assertIn("endpoint and capability mismatches fail closed", README)

    def test_basic_and_experimental_boundaries_are_explicit(self) -> None:
        self.assertIn("need neither the experimental host patch nor a Sunsama account", README)
        self.assertIn("Stock Hermes 0.20.4, 0.20.5, 0.21.0, and current main", README)
        self.assertIn(EXACT_BASE, README)
        self.assertIn("never installed automatically", README)
        self.assertIn("operator-supplied task reference", README)
        self.assertIn("does not contact Sunsama", README)
        self.assertIn("status` output contains private routing metadata", README)

    def test_quickstart_pins_installer_bytes_and_signed_tag(self) -> None:
        digest = hashlib.sha256((ROOT / "scripts" / "install-signed.sh").read_bytes()).hexdigest()
        self.assertIn(f"HASH={digest}", README)
        self.assertIn("sha256sum -c -", README)
        self.assertIn("refs/tags/v1.3.0:refs/tags/v1.3.0", INSTALLER)
        self.assertIn("verify-tag --raw v1.3.0", INSTALLER)
        self.assertIn(PINNED_SIGNER, INSTALLER)
        self.assertIn("plugins.scan_on_install true", INSTALLER)
        self.assertIn("--no-enable", INSTALLER)
        self.assertIn("--no-allow-tool-override", INSTALLER)
        self.assertNotIn("--force", INSTALLER)

    def test_downloadable_assets_are_authenticated_by_signed_source(self) -> None:
        for guide in (OPERATIONS, EXPERIMENTAL):
            self.assertIn("verify-tag --raw v1.3.0", guide)
            self.assertIn(PINNED_SIGNER, guide)
            self.assertIn("release/v1.3.0.sha256", guide)
            self.assertIn("--proto '=https'", guide)
            self.assertIn("cmp --", guide)
        self.assertIn("hermes-t3-control-1.3.0.tar.gz", OPERATIONS)
        self.assertIn("hermes-gateway-continuation-1.3.0.patch", EXPERIMENTAL)

    def test_experimental_signature_check_accepts_real_multiline_git_output(self) -> None:
        check = (
            "printf '%s\\n' \"$TAG_VERIFY\" | grep -Fqx "
            "'Good \"git\" signature with ED25519 key "
            f"{PINNED_SIGNER}'"
        )
        self.assertIn(check, EXPERIMENTAL)
        transcript = (
            f'Good "git" signature with ED25519 key {PINNED_SIGNER}\n'
            "No principal matched."
        )
        accepted = subprocess.run(
            ["sh", "-c", check],
            env={"PATH": "/usr/bin:/bin", "TAG_VERIFY": transcript},
            check=False,
        )
        rejected = subprocess.run(
            ["sh", "-c", check],
            env={"PATH": "/usr/bin:/bin", "TAG_VERIFY": transcript.replace(PINNED_SIGNER, "SHA256:wrong")},
            check=False,
        )
        self.assertEqual(accepted.returncode, 0)
        self.assertNotEqual(rejected.returncode, 0)

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
        self.assertIn("starts the new cursor at zero", EXPERIMENTAL)
        self.assertIn("cannot resume", EXPERIMENTAL)
        self.assertIn("pre-v2 experimental code is unsupported", EXPERIMENTAL)

    def test_public_docs_do_not_contain_workstation_aliases_or_private_paths(self) -> None:
        public = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))))
        for private_pattern in (
            r"codex_[0-9]+x",
            r"gpt-[0-9]+\.[0-9]+-[a-z]+",
            r"/home/[a-z0-9_-]+/Projects/",
            r"/mnt/[a-z]/Users/[a-z0-9_-]+/",
        ):
            with self.subTest(private_pattern=private_pattern):
                self.assertIsNone(re.search(private_pattern, public, re.IGNORECASE))
        self.assertIsNone(re.search(r"(?i)(?:token|secret|password)\s*[:=]\s*[A-Za-z0-9_-]{16,}", public))


if __name__ == "__main__":
    unittest.main()
