from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import secrets
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_release.py"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_release.py"
SMOKE_SCRIPT = ROOT / "scripts" / "live_smoke.py"
ARCHIVE_FILES = {
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "__init__.py",
    "auth.py",
    "client.py",
    "plugin.yaml",
    "schemas.py",
    "tools.py",
}
ARCHIVE_NAME = "hermes-t3-control-1.2.0.tar.gz"
CHECKSUM_NAME = f"{ARCHIVE_NAME}.sha256"
RELEASE_ARTIFACT_NAMES = (ARCHIVE_NAME, CHECKSUM_NAME)
ACTION_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "astral-sh/setup-uv": "c771a70e6277c0a99b617c7a806ffedaca235ff9",
}
HERMES_COMMITS = {
    "e624e9fde561e1add9388384012b295fde669ade",
    "fcbd1076a93841fa88855acce810e342a5b78101",
}
SECRET_LITERAL_RE = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|"
    rb"github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _load_build_module():
    spec = importlib.util.spec_from_file_location("release_build", BUILD_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load release build script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("release_live_smoke", SMOKE_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load live smoke script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseMetadataTests(unittest.TestCase):
    def test_version_license_homepage_and_changelog_agree(self) -> None:
        manifest = json.loads((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "1.2.0")
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(
            manifest["homepage"],
            "https://github.com/thetasigmaio/hermes-t3-control",
        )
        self.assertIn("## [1.2.0] - 2026-08-22", changelog)
        self.assertIn("1.2.0", readme)
        self.assertIn("MIT", readme)
        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn("Copyright (c) 2026 Jakub Sladek", license_text)
        self.assertIn("Permission is hereby granted, free of charge", license_text)

    def test_repository_and_ignore_hygiene(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for spec_root in (
            ROOT / ".codex" / "specs" / "hermes-t3-control",
            ROOT / ".claude" / "specs" / "hermes-t3-control",
        ):
            self.assertFalse(
                any(
                    entry.is_file() or entry.is_symlink()
                    for entry in spec_root.rglob("*")
                )
            )
        self.assertNotIn(".codex", ignore)
        for required in (
            "__pycache__/",
            ".venv/",
            ".coverage",
            "htmlcov/",
            "dist/",
            ".env",
        ):
            with self.subTest(required=required):
                self.assertIn(required, ignore)

class DeterministicArtifactTests(unittest.TestCase):
    def _build(self, directory: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
        result = _run(str(BUILD_SCRIPT), "--output-dir", str(directory))
        self.assertEqual(result.returncode, 0, result.stderr)
        archive = directory / ARCHIVE_NAME
        checksum = directory / CHECKSUM_NAME
        self.assertEqual(set(directory.iterdir()), {archive, checksum})
        return archive, checksum

    def _assert_rejected_without_residue(
        self, directory: pathlib.Path, artifact: pathlib.Path
    ) -> subprocess.CompletedProcess[str]:
        result = _run(str(BUILD_SCRIPT), "--output-dir", str(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(set(directory.iterdir()), {artifact})
        self.assertFalse(
            any(
                entry.name.startswith(f".{artifact_name}.tmp-")
                for entry in directory.iterdir()
                for artifact_name in RELEASE_ARTIFACT_NAMES
            )
        )
        return result

    @unittest.skipUnless(hasattr(os, "umask"), "POSIX umask support is required")
    def test_new_output_directory_is_private_under_permissive_caller_umask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary) / "new-release-directory"
            original_umask = os.umask(0o002)
            try:
                result = _run(str(BUILD_SCRIPT), "--output-dir", str(output))
            finally:
                os.umask(original_umask)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertEqual(
                {entry.name for entry in output.iterdir()},
                set(RELEASE_ARTIFACT_NAMES),
            )

    @unittest.skipUnless(hasattr(os, "geteuid"), "effective UID support is required")
    def test_output_directory_metadata_rejects_wrong_owner(self) -> None:
        build = _load_build_module()
        effective_uid = os.geteuid()
        wrong_owner = mock.Mock(
            st_mode=stat.S_IFDIR | 0o700,
            st_uid=effective_uid + 1,
        )

        with self.assertRaises(build.ReleaseBuildError) as raised:
            build._require_secure_output_directory_metadata(
                wrong_owner,
                effective_uid,
            )

        self.assertEqual(
            str(raised.exception),
            "Release output directory has an unsafe owner.",
        )

    def test_build_rejects_group_or_world_writable_output_without_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary)
            sentinel_payload = b"unsafe-output-target-must-survive"
            for index, mode in enumerate((0o720, 0o702)):
                with self.subTest(mode=oct(mode)):
                    directory = base / f"unsafe-output-{index}"
                    directory.mkdir(mode=0o700)
                    artifact = directory / ARCHIVE_NAME
                    artifact.write_bytes(sentinel_payload)
                    directory.chmod(mode)

                    result = self._assert_rejected_without_residue(directory, artifact)

                    self.assertEqual(artifact.read_bytes(), sentinel_payload)
                    self.assertEqual(stat.S_IMODE(directory.stat().st_mode), mode)
                    self.assertNotIn(sentinel_payload.decode("ascii"), result.stderr)

    def test_staged_inode_substitution_is_rejected_before_publication(self) -> None:
        build = _load_build_module()
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary)
            directory = base / "release"
            directory.mkdir(mode=0o700)
            external_sentinel = base / "external-sentinel"
            sentinel_payload = b"external-sentinel-must-survive"
            external_sentinel.write_bytes(sentinel_payload)
            replacement_payload = b"substituted-staging-inode"
            staged: list[tuple[str, int, int]] = []
            original_stage_artifact = build._stage_artifact

            def stage_and_substitute(
                staging_fd: int, final_name: str, payload: bytes
            ) -> tuple[str, int, int]:
                staged_artifact = original_stage_artifact(
                    staging_fd,
                    final_name,
                    payload,
                )
                staged.append(staged_artifact)
                if len(staged) == len(RELEASE_ARTIFACT_NAMES):
                    replacement_name = f".replacement-{secrets.token_hex(16)}"
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                    descriptor = os.open(
                        replacement_name,
                        flags,
                        0o600,
                        dir_fd=staging_fd,
                    )
                    with os.fdopen(descriptor, "wb") as replacement:
                        replacement.write(replacement_payload)
                        replacement.flush()
                        os.fsync(replacement.fileno())
                    os.replace(
                        replacement_name,
                        staged[0][0],
                        src_dir_fd=staging_fd,
                        dst_dir_fd=staging_fd,
                    )
                return staged_artifact

            with mock.patch.object(
                build,
                "_stage_artifact",
                side_effect=stage_and_substitute,
            ), self.assertRaises(build.ReleaseBuildError) as raised:
                build.build_release(directory)

            self.assertEqual(len(staged), len(RELEASE_ARTIFACT_NAMES))
            self.assertEqual(set(directory.iterdir()), set())
            self.assertEqual(external_sentinel.read_bytes(), sentinel_payload)
            self.assertNotIn(replacement_payload.decode("ascii"), str(raised.exception))

    def test_builds_are_byte_identical_and_checksum_is_portable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary)
            first_archive, first_checksum = self._build(base / "first")
            second_archive, second_checksum = self._build(base / "second")

            self.assertEqual(first_archive.read_bytes(), second_archive.read_bytes())
            self.assertEqual(first_checksum.read_bytes(), second_checksum.read_bytes())
            self.assertEqual(first_archive.read_bytes()[4:8], b"\x00\x00\x00\x00")
            digest = hashlib.sha256(first_archive.read_bytes()).hexdigest()
            self.assertEqual(
                first_checksum.read_text(encoding="ascii"),
                f"{digest}  {first_archive.name}\n",
            )
            verified = _run(str(VERIFY_SCRIPT), str(first_checksum))
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(verified.stdout, f"Checksum OK: {first_archive.name}\n")

    def test_archive_has_one_safe_allowlisted_root_and_fixed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive, _ = self._build(pathlib.Path(temporary) / "release")
            with tarfile.open(archive, mode="r:gz") as package:
                members = package.getmembers()
                names = [member.name for member in members]
                files = {member.name.removeprefix("hermes-t3-control/") for member in members if member.isfile()}
                self.assertEqual(files, ARCHIVE_FILES)
                self.assertEqual(names, sorted(names))
                self.assertEqual(names[0], "hermes-t3-control")
                for member in members:
                    with self.subTest(member=member.name):
                        path = pathlib.PurePosixPath(member.name)
                        self.assertFalse(path.is_absolute())
                        self.assertNotIn("..", path.parts)
                        self.assertTrue(
                            member.name == "hermes-t3-control"
                            or member.name.startswith("hermes-t3-control/")
                        )
                        self.assertEqual(member.mtime, 0)
                        self.assertEqual((member.uid, member.gid), (0, 0))
                        self.assertEqual((member.uname, member.gname), ("", ""))
                        self.assertEqual(member.mode, 0o755 if member.isdir() else 0o644)
                for member in members:
                    if not member.isfile():
                        continue
                    extracted = package.extractfile(member)
                    self.assertIsNotNone(extracted)
                    content = extracted.read() if extracted is not None else b""
                    self.assertIsNone(SECRET_LITERAL_RE.search(content))

    def test_build_rejects_archive_and_checksum_symlink_sentinels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary)
            sentinel_payload = b"external-symlink-sentinel-must-survive"
            for index, artifact_name in enumerate(RELEASE_ARTIFACT_NAMES):
                with self.subTest(artifact=artifact_name):
                    directory = base / f"symlink-output-{index}"
                    directory.mkdir()
                    sentinel = base / f"symlink-sentinel-{index}"
                    sentinel.write_bytes(sentinel_payload)
                    artifact = directory / artifact_name
                    artifact.symlink_to(sentinel)

                    result = self._assert_rejected_without_residue(directory, artifact)

                    self.assertTrue(artifact.is_symlink())
                    self.assertEqual(artifact.readlink(), sentinel)
                    self.assertEqual(sentinel.read_bytes(), sentinel_payload)
                    self.assertNotIn(sentinel_payload.decode("ascii"), result.stderr)

    def test_build_rejects_archive_and_checksum_hardlink_sentinels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary)
            sentinel_payload = b"external-hardlink-sentinel-must-survive"
            for index, artifact_name in enumerate(RELEASE_ARTIFACT_NAMES):
                with self.subTest(artifact=artifact_name):
                    directory = base / f"hardlink-output-{index}"
                    directory.mkdir()
                    sentinel = base / f"hardlink-sentinel-{index}"
                    sentinel.write_bytes(sentinel_payload)
                    artifact = directory / artifact_name
                    os.link(sentinel, artifact)

                    result = self._assert_rejected_without_residue(directory, artifact)

                    self.assertTrue(artifact.samefile(sentinel))
                    self.assertEqual(sentinel.read_bytes(), sentinel_payload)
                    self.assertNotIn(sentinel_payload.decode("ascii"), result.stderr)

    def test_build_rejects_archive_and_checksum_directory_sentinels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary)
            sentinel_payload = b"directory-sentinel-must-survive"
            for index, artifact_name in enumerate(RELEASE_ARTIFACT_NAMES):
                with self.subTest(artifact=artifact_name):
                    directory = base / f"directory-output-{index}"
                    directory.mkdir()
                    external_sentinel = base / f"directory-sentinel-{index}"
                    external_sentinel.write_bytes(sentinel_payload)
                    artifact = directory / artifact_name
                    artifact.mkdir()
                    nested_sentinel = artifact / "sentinel"
                    nested_sentinel.write_bytes(sentinel_payload)

                    result = self._assert_rejected_without_residue(directory, artifact)

                    self.assertTrue(artifact.is_dir())
                    self.assertEqual(nested_sentinel.read_bytes(), sentinel_payload)
                    self.assertEqual(external_sentinel.read_bytes(), sentinel_payload)
                    self.assertNotIn(sentinel_payload.decode("ascii"), result.stderr)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO support is required")
    def test_build_rejects_archive_and_checksum_fifo_sentinels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary)
            external_payload = b"external-fifo-sentinel-must-survive"
            for index, artifact_name in enumerate(RELEASE_ARTIFACT_NAMES):
                with self.subTest(artifact=artifact_name):
                    directory = base / f"fifo-output-{index}"
                    directory.mkdir()
                    external_sentinel = base / f"fifo-sentinel-{index}"
                    external_sentinel.write_bytes(external_payload)
                    artifact = directory / artifact_name
                    os.mkfifo(artifact)

                    result = self._assert_rejected_without_residue(directory, artifact)

                    self.assertTrue(stat.S_ISFIFO(artifact.lstat().st_mode))
                    self.assertEqual(external_sentinel.read_bytes(), external_payload)
                    self.assertNotIn(external_payload.decode("ascii"), result.stderr)

    def test_verifier_fails_closed_on_tampering_and_unsafe_checksum_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = pathlib.Path(temporary)
            archive, checksum = self._build(directory)
            archive.write_bytes(archive.read_bytes() + b"tampered")
            mismatch = _run(str(VERIFY_SCRIPT), str(checksum))
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertNotIn(hashlib.sha256(archive.read_bytes()).hexdigest(), mismatch.stderr)

            unsafe = directory / "unsafe.sha256"
            unsafe.write_text(f"{'0' * 64}  ../outside.tar.gz\n", encoding="ascii")
            traversal = _run(str(VERIFY_SCRIPT), str(unsafe))
            self.assertNotEqual(traversal.returncode, 0)


class ContinuousIntegrationContractTests(unittest.TestCase):
    def test_ci_permissions_action_pins_matrices_and_graph(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertRegex(workflow, r"(?m)^permissions:\n  contents: read$")
        self.assertNotRegex(workflow, r"(?m)^  (?!contents:)[a-z-]+: (?:read|write)$")

        uses = re.findall(r"(?m)^\s*uses:\s*([^@\s]+)@([^\s]+)$", workflow)
        self.assertGreater(len(uses), 0)
        for action, revision in uses:
            with self.subTest(action=action):
                self.assertIn(action, ACTION_PINS)
                self.assertEqual(revision, ACTION_PINS[action])
                self.assertRegex(revision, r"^[0-9a-f]{40}$")

        self.assertIn('python-version: ["3.11", "3.12", "3.13"]', workflow)
        self.assertIn('python-version: "3.11"', workflow)
        unit_gate = """      - name: Run unit and mocked integration tests
        env:
          PYTHONWARNINGS: "error"
        run: python -B -m unittest discover -s tests -v
"""
        self.assertEqual(workflow.count(unit_gate), 1)
        self.assertIn(
            "name: Supported install + Doctor (Hermes ${{ matrix.hermes-version }})",
            workflow,
        )
        for commit in HERMES_COMMITS:
            self.assertIn(commit, workflow)
        hermes_bootstrap = """      - name: Check out pinned Hermes revision
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          repository: NousResearch/hermes-agent
          ref: ${{ matrix.hermes-commit }}
          path: .ci/hermes-agent
          persist-credentials: false
      - name: Set up Python 3.11
        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97
        with:
          python-version: "3.11"
      - name: Set up pinned uv
        uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9
        with:
          version: "0.12.0"
          enable-cache: false
      - name: Sync frozen Hermes dependency graph
        run: uv sync --frozen --project .ci/hermes-agent --python 3.11
"""
        self.assertEqual(
            workflow.count(hermes_bootstrap),
            1,
        )
        self.assertNotIn("git+https://github.com/NousResearch/hermes-agent", workflow)
        self.assertNotIn("pip install --editable .ci/hermes-agent", workflow)
        supported_install = """      - name: Run supported pinned-ref install regression
        env:
          HERMES_SUPPORTED_INSTALL_TEST: "1"
          PYTHONDONTWRITEBYTECODE: "1"
          PYTHONWARNINGS: "error"
        run: uv run --frozen --project .ci/hermes-agent python -B -m unittest -v tests.test_supported_install
"""
        self.assertEqual(workflow.count(supported_install), 1)
        self.assertNotIn("--force", supported_install)
        self.assertRegex(workflow, r"(?m)^  package:\n(?:.*\n)*?    needs: \[unit, doctor\]$")
        self.assertLess(workflow.index("needs: [unit, doctor]"), workflow.index("Upload release artifacts"))

    def test_package_upload_contains_only_archive_and_checksum(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        upload = workflow.split("- name: Upload release artifacts", 1)[1]
        self.assertIn("python -B scripts/build_release.py --output-dir dist", workflow)
        self.assertIn(
            "python -B scripts/verify_release.py dist/hermes-t3-control-1.2.0.tar.gz.sha256",
            workflow,
        )
        paths = re.findall(r"(?m)^            (dist/\S+)$", upload)
        self.assertEqual(
            paths,
            [
                "dist/hermes-t3-control-1.2.0.tar.gz",
                "dist/hermes-t3-control-1.2.0.tar.gz.sha256",
            ],
        )


class LiveSmokeContractTests(unittest.TestCase):
    def test_exact_thread_read_is_sanitized_and_read_only(self) -> None:
        smoke = _load_smoke_module()
        disposable_token = secrets.token_hex(24)
        captured: dict[str, object] = {}
        sensitive_message = "message-that-must-not-print"

        class FakeClient:
            def __init__(self, base_url: str, token: str) -> None:
                captured["constructor"] = (base_url, token)

            def get_thread(self, thread_id: str, *, turn_limit: int):
                captured["read"] = (thread_id, turn_limit)
                return {
                    "snapshotSequence": 41,
                    "thread": {
                        "id": thread_id,
                        "runtimeMode": "approval-required",
                        "interactionMode": "plan",
                        "messages": [{"text": sensitive_message}],
                        "activity": [{"detail": disposable_token}],
                        "proposedPlans": [{"planMarkdown": sensitive_message}],
                    },
                }

        environment = {
            "T3_SMOKE_ISOLATED": "1",
            "T3_SMOKE_THREAD_ID": "isolated-release-thread",
            "T3_ORCHESTRATION_BASE_URL": "http://127.0.0.1:9137",
            "T3_ORCHESTRATION_TOKEN": disposable_token,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            smoke, "T3Client", FakeClient
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = smoke.main()

        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(
            captured,
            {
                "constructor": ("http://127.0.0.1:9137", disposable_token),
                "read": ("isolated-release-thread", 1),
            },
        )
        summary = json.loads(stdout.getvalue())
        self.assertEqual(
            summary,
            {
                "interaction_mode": "plan",
                "ok": True,
                "runtime_mode": "approval-required",
                "snapshot_sequence": 41,
                "thread_id": "isolated-release-thread",
            },
        )
        self.assertNotIn(disposable_token, stdout.getvalue())
        self.assertNotIn(sensitive_message, stdout.getvalue())

    def test_missing_isolation_or_solarsim_target_makes_no_client(self) -> None:
        smoke = _load_smoke_module()
        disposable_token = secrets.token_hex(24)
        base_environment = {
            "T3_SMOKE_THREAD_ID": "isolated-release-thread",
            "T3_ORCHESTRATION_BASE_URL": "http://127.0.0.1:9137",
            "T3_ORCHESTRATION_TOKEN": disposable_token,
        }
        for override in (
            {},
            {"T3_SMOKE_ISOLATED": "0"},
            {
                "T3_SMOKE_ISOLATED": "1",
                "T3_SMOKE_THREAD_ID": "SolarSim-production-thread",
            },
        ):
            environment = dict(base_environment)
            environment.update(override)
            with self.subTest(override=override), mock.patch.dict(
                os.environ, environment, clear=True
            ), mock.patch.object(smoke, "T3Client") as client_factory, contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(smoke.main(), 2)
                client_factory.assert_not_called()

    def test_source_exposes_no_shell_or_dispatch_operation(self) -> None:
        source = SMOKE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(".get_thread(", source)
        self.assertNotIn(".get_shell(", source)
        self.assertNotIn(".mutate(", source)
        self.assertNotIn("/api/orchestration/shell", source)
        self.assertNotIn("/api/orchestration/dispatch", source)
        for name in (
            "T3_SMOKE_ISOLATED",
            "T3_SMOKE_THREAD_ID",
            "T3_ORCHESTRATION_BASE_URL",
            "T3_ORCHESTRATION_TOKEN",
        ):
            self.assertIn(name, source)


if __name__ == "__main__":
    unittest.main()
