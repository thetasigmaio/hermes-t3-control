from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
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
TEST_CREDENTIAL = "test-only-placeholder"
FRESH_LOAD_INSPECTOR = r'''import json
import os
import socket
import sys
from pathlib import Path

network_attempts = []


def block_network(*args, **kwargs):
    del args, kwargs
    network_attempts.append(True)
    raise AssertionError("fresh plugin inspection attempted network I/O")


socket.create_connection = block_network
socket.socket.connect = block_network
socket.socket.connect_ex = block_network

expected_tools = tuple(json.loads(sys.argv[1]))
from hermes_cli.plugins import discover_plugins, get_plugin_manager
from tools.registry import registry

discover_plugins()
manager = get_plugin_manager()
loaded = manager._plugins["hermes-t3-control"]
manifest = loaded.manifest
hermes_home = Path(os.environ["HERMES_HOME"]).resolve()
installed_root = (hermes_home / "plugins" / "hermes-t3-control").resolve()

assert loaded.enabled is True
assert loaded.error is None
assert tuple(loaded.tools_registered) == expected_tools
assert Path(loaded.module.__file__).resolve().is_relative_to(installed_root)
assert manifest.manifest_version == 1
assert manifest.api_version == 1
assert (manifest.name, manifest.version, manifest.kind) == (
    "hermes-t3-control",
    "1.1.1",
    "standalone",
)
assert tuple(manifest.provides_tools) == expected_tools
assert manifest.requires_env == [
    {
        "name": "T3_ORCHESTRATION_TOKEN",
        "description": "Operator-provisioned short-lived T3 token with orchestration read and operate scopes.",
        "prompt": "T3 orchestration token",
        "password": True,
        "secret": True,
    }
]
assert manifest.config_schema["base_url"]["type"] == "string"
assert manifest.config_schema["base_url"]["required"] is True
assert manifest.config_schema["default_runtime_mode"]["default"] == "approval-required"

entries = []
for name in expected_tools:
    entry = registry.snapshot_registration(name, scope=manager.scope_key)
    assert entry is not None
    assert entry.toolset == "t3_control"
    assert entry.requires_env == ["T3_ORCHESTRATION_TOKEN"]
    assert entry.is_async is False
    assert entry.description == entry.schema["description"]
    assert entry.check_fn() is True
    entries.append(entry.name)

definitions = registry.get_definitions(set(expected_tools), quiet=True)
definition_names = tuple(item["function"]["name"] for item in definitions)
assert tuple(sorted(definition_names)) == tuple(sorted(expected_tools))
assert network_attempts == []
assert not (hermes_home / ".env").exists()
assert os.environ["T3_ORCHESTRATION_TOKEN"] not in (
    hermes_home / "config.yaml"
).read_text(encoding="utf-8")

print(
    json.dumps(
        {
            "enabled": loaded.enabled,
            "manifest_version": manifest.manifest_version,
            "network_attempts": len(network_attempts),
            "tools": entries,
        },
        sort_keys=True,
    )
)
'''


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
    def test_pinned_install_enable_and_fresh_load_register_exactly_eight_tools(
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
            config.write_text(
                "plugins:\n"
                "  scan_on_install: true\n"
                "  entries:\n"
                "    hermes-t3-control:\n"
                "      settings:\n"
                "        base_url: http://127.0.0.1:1\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env.update(
                {
                    "GIT_CONFIG_GLOBAL": str(git_config),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "HERMES_HOME": str(hermes_home),
                    "NO_COLOR": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "T3_ORCHESTRATION_TOKEN": TEST_CREDENTIAL,
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

            listed_disabled = _run(
                [str(hermes), "plugins", "list", "--user", "--json"],
                cwd=isolated,
                env=env,
            )
            self.assertEqual(listed_disabled.returncode, 0, listed_disabled.stderr)
            disabled_plugins = json.loads(listed_disabled.stdout)
            self.assertEqual(len(disabled_plugins), 1)
            self.assertEqual(disabled_plugins[0]["name"], "hermes-t3-control")
            self.assertEqual(disabled_plugins[0]["version"], "1.1.1")
            self.assertEqual(disabled_plugins[0]["source"], "git")
            self.assertEqual(disabled_plugins[0]["status"], "not enabled")

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

            enabled = _run(
                [
                    str(hermes),
                    "plugins",
                    "enable",
                    "hermes-t3-control",
                    "--no-allow-tool-override",
                ],
                cwd=isolated,
                env=env,
            )
            self.assertEqual(
                enabled.returncode,
                0,
                f"stdout:\n{enabled.stdout}\nstderr:\n{enabled.stderr}",
            )
            self.assertIn("Plugin hermes-t3-control enabled", enabled.stdout)
            self.assertIn("may not override built-in tools", enabled.stdout)

            listed_enabled = _run(
                [
                    str(hermes),
                    "plugins",
                    "list",
                    "--user",
                    "--enabled",
                    "--json",
                ],
                cwd=isolated,
                env=env,
            )
            self.assertEqual(listed_enabled.returncode, 0, listed_enabled.stderr)
            enabled_plugins = json.loads(listed_enabled.stdout)
            self.assertEqual(len(enabled_plugins), 1)
            self.assertEqual(enabled_plugins[0]["name"], "hermes-t3-control")
            self.assertEqual(enabled_plugins[0]["status"], "enabled")

            scan_setting = _run(
                [str(hermes), "config", "get", "plugins.scan_on_install"],
                cwd=isolated,
                env=env,
            )
            override_setting = _run(
                [
                    str(hermes),
                    "config",
                    "get",
                    "plugins.entries.hermes-t3-control.allow_tool_override",
                ],
                cwd=isolated,
                env=env,
            )
            self.assertEqual(scan_setting.returncode, 0, scan_setting.stderr)
            self.assertEqual(scan_setting.stdout.strip(), "true")
            self.assertEqual(override_setting.returncode, 0, override_setting.stderr)
            self.assertEqual(override_setting.stdout.strip(), "false")

            fresh_load = _run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    FRESH_LOAD_INSPECTOR,
                    json.dumps(EXPECTED_TOOLS),
                ],
                cwd=isolated,
                env=env,
            )
            fresh_output = fresh_load.stdout + fresh_load.stderr
            self.assertFalse(
                TEST_CREDENTIAL in fresh_output,
                "Fresh-process inspection exposed the test credential.",
            )
            self.assertEqual(
                fresh_load.returncode,
                0,
                f"stdout:\n{fresh_load.stdout}\nstderr:\n{fresh_load.stderr}",
            )
            fresh_summary = json.loads(fresh_load.stdout)
            self.assertEqual(
                fresh_summary,
                {
                    "enabled": True,
                    "manifest_version": 1,
                    "network_attempts": 0,
                    "tools": list(EXPECTED_TOOLS),
                },
            )

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

            remove_help = _run(
                [str(hermes), "plugins", "remove", "--help"],
                cwd=isolated,
                env=env,
            )
            self.assertEqual(remove_help.returncode, 0, remove_help.stderr)
            self.assertIn("usage: hermes plugins remove", remove_help.stdout)
            removed = _run(
                [str(hermes), "plugins", "remove", "hermes-t3-control"],
                cwd=isolated,
                env=env,
            )
            self.assertEqual(
                removed.returncode,
                0,
                f"stdout:\n{removed.stdout}\nstderr:\n{removed.stderr}",
            )
            self.assertFalse(installed_root.exists())


if __name__ == "__main__":
    unittest.main()
