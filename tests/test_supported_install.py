from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
IDENTIFIER = "thetasigmaio/hermes-t3-control"
EXPECTED_TOOLS = (
    "t3_threads",
    "t3_thread_read",
    "t3_thread_create",
    "t3_thread_send",
    "t3_thread_set_mode",
    "t3_thread_implement_plan",
    "t3_turn_interrupt",
    "t3_session_stop",
)


def _run(
    args: list[str],
    *,
    cwd: pathlib.Path,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _snapshot_repository(target: pathlib.Path) -> str:
    listed = _run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
    )
    if listed.returncode != 0:
        raise AssertionError(listed.stderr)
    tracked = {pathlib.Path(item) for item in listed.stdout.split("\0") if item}
    tracked.add(pathlib.Path(__file__).relative_to(ROOT))
    for relative_path in sorted(tracked):
        source = ROOT / relative_path
        if not source.exists():
            continue
        destination = target / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    commands = (
        ["git", "init", "-q", "--initial-branch=main"],
        ["git", "config", "user.email", "install-regression@example.invalid"],
        ["git", "config", "user.name", "Install Regression"],
        ["git", "add", "--all"],
        ["git", "-c", "commit.gpgsign=false", "commit", "-qm", "test snapshot"],
    )
    for command in commands:
        result = _run(command, cwd=target)
        if result.returncode != 0:
            raise AssertionError(result.stderr)
    revision = _run(["git", "rev-parse", "HEAD"], cwd=target)
    if revision.returncode != 0:
        raise AssertionError(revision.stderr)
    return revision.stdout.strip()


@unittest.skipUnless(
    os.environ.get("HERMES_SUPPORTED_INSTALL_TEST") == "1",
    "set HERMES_SUPPORTED_INSTALL_TEST=1 with a pinned Hermes CLI installed",
)
class SupportedInstallRegressionTests(unittest.TestCase):
    def test_pinned_install_with_scanner_enabled_registers_exactly_eight_tools(
        self,
    ) -> None:
        hermes = shutil.which("hermes")
        self.assertIsNotNone(hermes, "hermes CLI is required")

        with tempfile.TemporaryDirectory() as temporary:
            isolated = pathlib.Path(temporary)
            repository = isolated / "repository"
            repository.mkdir()
            revision = _snapshot_repository(repository)

            git_config = isolated / "gitconfig"
            git_config.write_text(
                f'[url "{repository.as_uri()}"]\n'
                f"\tinsteadOf = https://github.com/{IDENTIFIER}.git\n"
                '[protocol "file"]\n'
                "\tallow = always\n",
                encoding="utf-8",
            )
            hermes_home = isolated / "hermes-home"
            hermes_home.mkdir()
            config = hermes_home / "config.yaml"
            config.write_text("plugins:\n  scan_on_install: true\n", encoding="utf-8")
            env = dict(os.environ)
            env.update(
                {
                    "GIT_CONFIG_GLOBAL": str(git_config),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "HERMES_HOME": str(hermes_home),
                    "NO_COLOR": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "T3_ORCHESTRATION_TOKEN": "test-only-placeholder",
                }
            )
            install_command = [
                str(hermes),
                "plugins",
                "install",
                IDENTIFIER,
                "--ref",
                revision,
                "--no-enable",
            ]
            self.assertNotIn("--force", install_command)
            installed = _run(install_command, cwd=isolated, env=env)
            self.assertEqual(
                installed.returncode,
                0,
                f"stdout:\n{installed.stdout}\nstderr:\n{installed.stderr}",
            )
            self.assertIn("Plugin installed but not enabled", installed.stdout)
            self.assertEqual(
                config.read_text(encoding="utf-8"),
                "plugins:\n  scan_on_install: true\n",
            )

            installed_root = hermes_home / "plugins" / "hermes-t3-control"
            manifest = json.loads(
                (installed_root / "plugin.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["manifest_version"], 1)
            self.assertEqual(manifest["version"], "1.1.1")
            self.assertEqual(tuple(manifest["provides_tools"]), EXPECTED_TOOLS)
            installed_revision = _run(
                ["git", "rev-parse", "HEAD"],
                cwd=installed_root,
                env=env,
            )
            self.assertEqual(installed_revision.returncode, 0, installed_revision.stderr)
            self.assertEqual(installed_revision.stdout.strip(), revision)

            doctor = _run(
                [str(hermes), "plugins", "doctor", "hermes-t3-control", "--ci"],
                cwd=isolated,
                env=env,
            )
            self.assertEqual(
                doctor.returncode,
                0,
                f"stdout:\n{doctor.stdout}\nstderr:\n{doctor.stderr}",
            )
            self.assertIn("registrations: 8 tool(s), 0 hook(s)", doctor.stdout)
            self.assertNotIn("WARN:", doctor.stdout)


if __name__ == "__main__":
    unittest.main()
