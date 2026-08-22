from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import subprocess
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import auth
import client
import tools


SERVER_VERSION = "0.0.34-nightly.20260820.1141"
ENVIRONMENT_ID = "environment-test"
ORIGIN = "http://127.0.0.1:9137"
PID = 4242
EXPECTED_ADMIN_SCOPES = (
    "orchestration:read",
    "orchestration:operate",
    "terminal:operate",
    "review:write",
    "relay:read",
    "access:read",
    "access:write",
    "relay:write",
)


class FakeProcess:
    def __init__(self, stdout: str = "", *, returncode: int = 0) -> None:
        read_fd, write_fd = os.pipe()
        payload = stdout.encode("utf-8")

        def write_stdout() -> None:
            try:
                offset = 0
                while offset < len(payload):
                    offset += os.write(write_fd, payload[offset:])
            except BrokenPipeError:
                pass
            finally:
                os.close(write_fd)

        self.writer = threading.Thread(target=write_stdout, daemon=True)
        self.writer.start()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self.final_returncode = returncode
        self.pid = 43_210
        self.returncode: int | None = None
        self.killed = False
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_calls += 1
        if self.returncode is None:
            self.returncode = -9 if self.killed else self.final_returncode
        self.writer.join(timeout=0.1)
        if not self.stdout.closed:
            self.stdout.close()
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeContext:
    def __init__(
        self,
        *,
        base_url: str = ORIGIN,
        auth_mode: object = None,
    ) -> None:
        self.base_url = base_url
        self.auth_mode = auth_mode

    def get_config(self, key: str, default: object = None) -> object:
        if key == "base_url":
            return self.base_url
        if key == "auth_mode":
            return self.auth_mode
        return default


def issued_session(*, token: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        session_id="session-private",
        token=token or uuid.uuid4().hex,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    )


def issued_payload(session: SimpleNamespace) -> dict[str, object]:
    return {
        "sessionId": session.session_id,
        "token": session.token,
        "method": "bearer-access-token",
        "scopes": list(EXPECTED_ADMIN_SCOPES),
        "subject": "hermes-t3-control",
        "client": {
            "label": "hermes-t3-control",
            "deviceType": "bot",
        },
        "expiresAt": session.expires_at,
    }


def local_runtime(base_dir: Path) -> object:
    tree = base_dir / "userdata" / "wsl-server-tree" / SERVER_VERSION
    return auth.LocalRuntime(
        base_dir=base_dir,
        origin=ORIGIN,
        node_path=base_dir / "bin" / "node",
        cli_path=tree / "apps" / "server" / "dist" / "bin.mjs",
        environment_id=ENVIRONMENT_ID,
        server_version=SERVER_VERSION,
    )


def write_runtime_fixture(base_dir: Path) -> tuple[Path, Path]:
    state_dir = base_dir / "userdata"
    tree = state_dir / "wsl-server-tree" / SERVER_VERSION
    cli_path = tree / "apps" / "server" / "dist" / "bin.mjs"
    node_path = base_dir / "bin" / "node"
    cli_path.parent.mkdir(parents=True)
    node_path.parent.mkdir(parents=True)
    cli_path.write_text("// fixture\n", encoding="utf-8")
    node_path.write_text("#!/bin/sh\n", encoding="utf-8")
    node_path.chmod(0o755)
    (state_dir / "state.sqlite").write_bytes(b"SQLite format 3\x00fixture")
    (tree / "package.json").write_text(
        json.dumps({"name": "t3code-server", "version": SERVER_VERSION}),
        encoding="utf-8",
    )
    (state_dir / "environment-id").write_text(
        ENVIRONMENT_ID + "\n", encoding="utf-8"
    )
    (state_dir / "server-runtime.json").write_text(
        json.dumps(
            {
                "version": 1,
                "pid": PID,
                "port": 9137,
                "origin": ORIGIN,
                "startedAt": "2026-08-21T22:00:00.000Z",
            }
        ),
        encoding="utf-8",
    )
    return node_path, cli_path


class CompatibilityModeTests(unittest.TestCase):
    def test_auto_prefers_valid_external_secret_without_local_fallback(self) -> None:
        token = uuid.uuid4().hex
        transport = mock.Mock()
        with mock.patch.object(auth, "_profile_secret", return_value=token), mock.patch.object(
            auth, "resolve_local_runtime"
        ) as resolve_local, mock.patch.object(
            auth, "issue_local_session"
        ) as issue, mock.patch.object(
            auth, "T3Client", return_value=transport
        ) as client_factory, auth.operation_client(
            FakeContext(auth_mode=None), {"thread_id": "thread-1"}
        ) as lease:
            self.assertIs(lease.client, transport)

        client_factory.assert_called_once_with(ORIGIN, token)
        transport._preflight_public_arguments.assert_called_once_with(
            {"thread_id": "thread-1"}
        )
        resolve_local.assert_not_called()
        issue.assert_not_called()

    def test_auto_without_valid_secret_uses_local_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            session = issued_session()
            transport = mock.Mock()
            events: list[str] = []
            transport._preflight_public_arguments.side_effect = lambda _args: events.append(
                "preflight"
            )
            with mock.patch.object(auth, "_profile_secret", return_value=None), mock.patch.object(
                auth, "resolve_local_runtime", return_value=runtime
            ) as resolve_local, mock.patch.object(
                auth, "_pinned_runtime_process"
            ) as pinned, mock.patch.object(
                auth,
                "_validate_unauthenticated_runtime",
                side_effect=lambda *_args: events.append("unauth-environment"),
            ) as validate_unauthenticated, mock.patch.object(
                auth,
                "issue_local_session",
                side_effect=lambda _runtime: (events.append("issue"), session)[1],
            ), mock.patch.object(
                auth,
                "T3Client",
                side_effect=lambda *_args, **_kwargs: (
                    events.append("construct"),
                    transport,
                )[1],
            ) as client_factory, mock.patch.object(
                auth,
                "_validate_runtime_environment",
                side_effect=lambda *_args: events.append("environment"),
            ) as validate, mock.patch.object(
                auth,
                "revoke_local_session",
                side_effect=lambda *_args: events.append("revoke"),
            ) as revoke:
                pinned.return_value.__enter__.return_value = SimpleNamespace()
                with auth.operation_client(
                    FakeContext(auth_mode=None), {"thread_id": "thread-1"}
                ) as lease:
                    events.append("body")
                    self.assertIs(lease.client, transport)

            resolve_local.assert_called_once_with()
            client_factory.assert_called_once_with(
                runtime.origin,
                session.token,
                connected_socket_validator=mock.ANY,
            )
            validate.assert_called_once_with(transport, runtime)
            self.assertEqual(validate_unauthenticated.call_count, 2)
            revoke.assert_called_once_with(runtime, session.session_id)
            self.assertEqual(
                events,
                [
                    "unauth-environment",
                    "issue",
                    "construct",
                    "preflight",
                    "unauth-environment",
                    "environment",
                    "body",
                    "revoke",
                ],
            )

    def test_auto_fails_closed_on_secret_resolver_error_or_malformed_value(self) -> None:
        failures = (
            RuntimeError("private resolver failure"),
            "",
            "malformed external secret",
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__), mock.patch.object(
                auth,
                "_profile_secret",
                **(
                    {"side_effect": failure}
                    if isinstance(failure, Exception)
                    else {"return_value": failure}
                ),
            ), mock.patch.object(
                auth, "resolve_local_runtime"
            ) as resolve_local, mock.patch.object(
                auth, "issue_local_session"
            ) as issue:
                with self.assertRaises(client.ConfigurationError):
                    with auth.operation_client(FakeContext(auth_mode=None), {}):
                        self.fail("auto mode fell back after an unsafe external-secret result")
            resolve_local.assert_not_called()
            issue.assert_not_called()

    def test_explicit_modes_never_fall_back(self) -> None:
        external_secret = "invalid secret"
        with mock.patch.object(
            auth, "_profile_secret", return_value=external_secret
        ), mock.patch.object(auth, "resolve_local_runtime") as resolve_local:
            with self.assertRaises(client.ConfigurationError) as caught:
                with auth.operation_client(
                    FakeContext(auth_mode="external-token"), {}
                ):
                    self.fail("explicit external-token mode accepted no credential")
            resolve_local.assert_not_called()
        self.assertNotIn(external_secret, json.dumps(caught.exception.to_dict()))

        token = uuid.uuid4().hex
        local_error = client.ConfigurationError("local runtime unavailable")
        with mock.patch.object(auth, "_profile_secret", return_value=token), mock.patch.object(
            auth, "resolve_local_runtime", side_effect=local_error
        ), mock.patch.object(auth, "T3Client") as client_factory:
            with self.assertRaises(client.ConfigurationError):
                with auth.operation_client(FakeContext(auth_mode="local-cli"), {}):
                    self.fail("explicit local-cli mode fell back to the external token")
            client_factory.assert_not_called()

        with self.assertRaises(client.ConfigurationError):
            with auth.operation_client(FakeContext(auth_mode="unknown"), {}):
                self.fail("unknown auth mode was accepted")

        with mock.patch.object(auth, "_profile_secret") as profile_secret, mock.patch.object(
            auth, "resolve_local_runtime"
        ) as resolve_local:
            with self.assertRaises(client.ConfigurationError):
                with auth.operation_client(FakeContext(auth_mode="auto"), {}):
                    self.fail("internal automatic selection was exposed as a config mode")
            profile_secret.assert_not_called()
            resolve_local.assert_not_called()


class LocalRuntimeDiscoveryTests(unittest.TestCase):
    def test_resolves_numeric_loopback_live_same_uid_and_exact_server_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            node_path, cli_path = write_runtime_fixture(base_dir)
            with mock.patch.object(
                auth, "_process_uid", return_value=os.getuid()
            ) as process_uid, mock.patch.object(
                auth,
                "_process_argv",
                return_value=(
                    str(node_path),
                    str(cli_path),
                    "--bootstrap-fd",
                    "0",
                ),
            ) as process_argv, mock.patch.object(
                auth, "_process_exe", return_value=node_path
            ) as process_exe, mock.patch.object(
                auth, "_validate_runtime_environment"
            ) as validate:
                resolved = auth.resolve_local_runtime(base_dir)

            self.assertEqual(resolved, local_runtime(base_dir))
            process_uid.assert_called_once_with(PID)
            process_argv.assert_called_once_with(PID)
            process_exe.assert_called_once_with(PID)
            validate.assert_not_called()

    def test_rejects_non_numeric_loopback_dead_or_foreign_process(self) -> None:
        cases = (
            ("origin", "http://localhost:9137"),
            ("origin", "http://192.0.2.1:9137"),
            ("origin", "https://127.0.0.1:9137"),
            ("process_uid", None),
            ("process_uid", os.getuid() + 1),
        )
        for kind, value in cases:
            with self.subTest(kind=kind, value=value), tempfile.TemporaryDirectory() as directory:
                base_dir = Path(directory)
                node_path, cli_path = write_runtime_fixture(base_dir)
                if kind == "origin":
                    state_path = base_dir / "userdata" / "server-runtime.json"
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    state["origin"] = value
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                process_uid = value if kind == "process_uid" else os.getuid()
                with mock.patch.object(
                    auth, "_process_uid", return_value=process_uid
                ), mock.patch.object(
                    auth,
                    "_process_argv",
                    return_value=(str(node_path), str(cli_path), "--bootstrap-fd", "0"),
                ), mock.patch.object(
                    auth, "_process_exe", return_value=node_path
                ):
                    with self.assertRaises(client.ConfigurationError):
                        auth.resolve_local_runtime(base_dir)

    def test_rejects_wrong_cli_path_or_server_package_metadata(self) -> None:
        cases = ("cli_path", "package_name", "package_version")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                base_dir = Path(directory)
                node_path, cli_path = write_runtime_fixture(base_dir)
                process_cli = cli_path
                package_path = cli_path.parents[3] / "package.json"
                package = json.loads(package_path.read_text(encoding="utf-8"))
                if case == "cli_path":
                    process_cli = cli_path.with_name("server.mjs")
                    process_cli.write_text("// decoy\n", encoding="utf-8")
                elif case == "package_name":
                    package["name"] = "not-t3code-server"
                else:
                    package["version"] = "0.0.0-wrong"
                package_path.write_text(json.dumps(package), encoding="utf-8")

                with mock.patch.object(
                    auth, "_process_uid", return_value=os.getuid()
                ), mock.patch.object(
                    auth,
                    "_process_argv",
                    return_value=(
                        str(node_path),
                        str(process_cli),
                        "--bootstrap-fd",
                        "0",
                    ),
                ), mock.patch.object(
                    auth, "_process_exe", return_value=node_path
                ):
                    with self.assertRaises(client.ConfigurationError):
                        auth.resolve_local_runtime(base_dir)


class LocalCliTests(unittest.TestCase):
    def test_issue_uses_exact_five_minute_session_and_safe_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            session = issued_session()
            process = FakeProcess(json.dumps(issued_payload(session)))
            with mock.patch.object(
                auth.subprocess, "Popen", return_value=process
            ) as popen:
                actual = auth.issue_local_session(runtime)

            self.assertEqual(actual.session_id, session.session_id)
            self.assertEqual(actual.token, session.token)
            self.assertEqual(actual.expires_at, session.expires_at)
            expected_argv = [
                str(runtime.node_path),
                str(runtime.cli_path),
                "auth",
                "session",
                "issue",
                "--json",
                "--ttl",
                "5m",
                "--label",
                "hermes-t3-control",
                "--subject",
                "hermes-t3-control",
                "--base-dir",
                str(runtime.base_dir),
            ]
            self.assertEqual(popen.call_args.args[0], expected_argv)
            options = popen.call_args.kwargs
            self.assertIs(options["shell"], False)
            self.assertEqual(options["stdin"], subprocess.DEVNULL)
            self.assertEqual(options["stdout"], subprocess.PIPE)
            self.assertEqual(options["stderr"], subprocess.DEVNULL)
            self.assertEqual(
                options["env"],
                {
                    "HOME": str(Path.home()),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "NO_COLOR": "1",
                    "PATH": "/usr/bin:/bin",
                },
            )
            self.assertFalse(
                any("proxy" in name.lower() for name in options["env"])
            )
            self.assertFalse(
                any(
                    marker in name.lower()
                    for name in options["env"]
                    for marker in ("token", "secret", "credential")
                )
            )
            self.assertNotIn(session.token, json.dumps(expected_argv))

    def test_cli_stdout_is_bounded_and_failures_are_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            secret = uuid.uuid4().hex
            oversized = "x" * (auth.MAX_CLI_STDOUT_BYTES + 1)
            self.assertLessEqual(auth.MAX_CLI_STDOUT_BYTES, 65_536)
            wrong_scopes = issued_session(token=secret)
            wrong_scope_payload = issued_payload(wrong_scopes)
            wrong_scope_payload["scopes"] = list(EXPECTED_ADMIN_SCOPES[:-1])
            wrong_expiry = issued_session(token=secret)
            wrong_expiry_payload = issued_payload(wrong_expiry)
            wrong_expiry_payload["expiresAt"] = (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat().replace("+00:00", "Z")
            failures = (
                FakeProcess(oversized),
                FakeProcess(json.dumps({"token": secret, "error": "missing session"})),
                FakeProcess(json.dumps(wrong_scope_payload)),
                FakeProcess(json.dumps(wrong_expiry_payload)),
                RuntimeError("private subprocess failure " + secret),
            )
            for failure in failures:
                with self.subTest(failure=type(failure).__name__):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    behavior = (
                        {"return_value": failure}
                        if isinstance(failure, FakeProcess)
                        else {"side_effect": failure}
                    )
                    with mock.patch.object(
                        auth.subprocess, "Popen", **behavior
                    ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                        with self.assertRaises(client.ConfigurationError) as caught:
                            auth.issue_local_session(runtime)
                    public = json.dumps(caught.exception.to_dict())
                    self.assertNotIn(secret, public)
                    self.assertNotIn("missing session", public)
                    self.assertNotIn("private subprocess failure", public)
                    self.assertNotIn(secret, stdout.getvalue() + stderr.getvalue())
                    if isinstance(failure, FakeProcess) and failure is failures[0]:
                        self.assertGreaterEqual(failure.wait_calls, 1)

    def test_timeout_kills_and_reaps_the_entire_cli_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            process = FakeProcess()

            def wait(timeout: float | None = None) -> int:
                process.wait_calls += 1
                if process.wait_calls == 1:
                    raise subprocess.TimeoutExpired("private-cli", timeout)
                process.writer.join(timeout=0.1)
                if not process.stdout.closed:
                    process.stdout.close()
                return -signal.SIGKILL

            process.wait = wait  # type: ignore[method-assign]
            with mock.patch.object(
                auth.subprocess, "Popen", return_value=process
            ), mock.patch.object(auth.os, "killpg") as killpg:
                with self.assertRaises(client.ConfigurationError):
                    auth._run_cli(runtime, ["auth", "session", "list"], capture=False)

            killpg.assert_called_once_with(process.pid, signal.SIGKILL)
            self.assertGreaterEqual(process.wait_calls, 2)

    def test_revoke_uses_session_id_then_list_confirms_exact_absence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            token = uuid.uuid4().hex
            revoke_process = FakeProcess()
            list_process = FakeProcess("[]")
            with mock.patch.object(
                auth.subprocess, "Popen", side_effect=[revoke_process, list_process]
            ) as popen:
                auth.revoke_local_session(runtime, "session-private")

            revoke_argv = popen.call_args_list[0].args[0]
            self.assertEqual(
                revoke_argv,
                [
                    str(runtime.node_path),
                    str(runtime.cli_path),
                    "auth",
                    "session",
                    "revoke",
                    "session-private",
                    "--base-dir",
                    str(runtime.base_dir),
                ],
            )
            list_argv = popen.call_args_list[1].args[0]
            self.assertEqual(
                list_argv,
                [
                    str(runtime.node_path),
                    str(runtime.cli_path),
                    "auth",
                    "session",
                    "list",
                    "--json",
                    "--base-dir",
                    str(runtime.base_dir),
                ],
            )
            self.assertNotIn(token, json.dumps([revoke_argv, list_argv]))
            self.assertIs(popen.call_args_list[0].kwargs["shell"], False)
            self.assertEqual(
                popen.call_args_list[0].kwargs["stdout"], subprocess.DEVNULL
            )
            self.assertEqual(
                popen.call_args_list[0].kwargs["stderr"], subprocess.DEVNULL
            )
            self.assertEqual(
                popen.call_args_list[1].kwargs["stdout"], subprocess.PIPE
            )

            with mock.patch.object(
                auth.subprocess,
                "Popen",
                side_effect=[
                    FakeProcess(),
                    FakeProcess(json.dumps([{"sessionId": "session-private"}])),
                ],
            ):
                with self.assertRaises(client.ConfigurationError):
                    auth.revoke_local_session(runtime, "session-private")

            with mock.patch.object(
                auth.subprocess,
                "Popen",
                side_effect=[FakeProcess(), FakeProcess(json.dumps([{}]))],
            ):
                with self.assertRaises(client.ConfigurationError):
                    auth.revoke_local_session(runtime, "session-private")


class OperationLeaseTests(unittest.TestCase):
    def _local_patches(
        self,
        runtime: object,
        session: SimpleNamespace,
        transport: mock.Mock,
    ) -> contextlib.ExitStack:
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(auth, "_profile_secret", return_value=None))
        stack.enter_context(
            mock.patch.object(auth, "resolve_local_runtime", return_value=runtime)
        )
        pinned = stack.enter_context(mock.patch.object(auth, "_pinned_runtime_process"))
        pinned.return_value.__enter__.return_value = SimpleNamespace()
        stack.enter_context(
            mock.patch.object(auth, "_validate_unauthenticated_runtime")
        )
        stack.enter_context(
            mock.patch.object(auth, "issue_local_session", return_value=session)
        )
        stack.enter_context(mock.patch.object(auth, "T3Client", return_value=transport))
        stack.enter_context(mock.patch.object(auth, "_validate_runtime_environment"))
        return stack

    def test_environment_check_is_first_http_after_single_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            session = issued_session()
            events: list[str] = []
            transport = mock.Mock()
            transport._preflight_public_arguments.side_effect = lambda _args: events.append(
                "preflight"
            )
            with self._local_patches(runtime, session, transport), mock.patch.object(
                auth,
                "_validate_unauthenticated_runtime",
                side_effect=lambda *_args: events.append("environment-unauthenticated"),
            ), mock.patch.object(
                auth,
                "_validate_runtime_environment",
                side_effect=lambda *_args: events.append("environment-credentialed"),
            ), mock.patch.object(auth, "revoke_local_session"):
                with auth.operation_client(
                    FakeContext(), {"message": "public operation"}
                ):
                    events.append("handler-http")

            self.assertEqual(
                events,
                [
                    "environment-unauthenticated",
                    "preflight",
                    "environment-unauthenticated",
                    "environment-credentialed",
                    "handler-http",
                ],
            )
            transport._preflight_public_arguments.assert_called_once_with(
                {"message": "public operation"}
            )

    def test_failed_post_issue_identity_recheck_sends_no_credential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            session = issued_session()
            transport = mock.Mock()
            stale = client.ConfigurationError("listener identity changed")
            with self._local_patches(runtime, session, transport), mock.patch.object(
                auth,
                "_validate_unauthenticated_runtime",
                side_effect=[None, stale],
            ) as validate_unauthenticated, mock.patch.object(
                auth, "_validate_runtime_environment"
            ) as credentialed_http, mock.patch.object(
                auth, "revoke_local_session"
            ) as revoke:
                with self.assertRaises(client.ConfigurationError):
                    with auth.operation_client(FakeContext(), {}):
                        self.fail("a stale listener received a credential")

            self.assertEqual(validate_unauthenticated.call_count, 2)
            credentialed_http.assert_not_called()
            revoke.assert_called_once_with(runtime, session.session_id)

    def test_listener_must_be_owned_by_the_exact_runtime_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            with mock.patch.object(
                auth, "_listener_socket_inodes", return_value={"12345"}
            ), mock.patch.object(
                auth, "_process_socket_inodes", return_value={"12345", "99999"}
            ):
                auth._validate_listener_process(runtime, PID)

            with mock.patch.object(
                auth, "_listener_socket_inodes", return_value={"12345"}
            ), mock.patch.object(
                auth, "_process_socket_inodes", return_value={"99999"}
            ):
                with self.assertRaises(client.ConfigurationError):
                    auth._validate_listener_process(runtime, PID)

    def test_listener_resolution_prefers_exact_loopback_over_wildcard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base_dir = Path(directory)
            runtime = local_runtime(base_dir)
            proc_root = base_dir / "proc"
            table = proc_root / "net" / "tcp"
            table.parent.mkdir(parents=True)
            heading = (
                "  sl  local_address rem_address st tx_queue rx_queue tr "
                "tm->when retrnsmt uid timeout inode\n"
            )
            wildcard = (
                "   0: 00000000:23B1 00000000:0000 0A 0:0 0:0 0 0 0 12345\n"
            )
            exact = (
                "   1: 0100007F:23B1 00000000:0000 0A 0:0 0:0 0 0 0 67890\n"
            )
            with mock.patch.object(auth, "PROC_ROOT", str(proc_root) + "/"):
                table.write_text(heading + wildcard, encoding="ascii")
                self.assertEqual(auth._listener_socket_inodes(runtime), {"12345"})
                table.write_text(heading + wildcard + exact, encoding="ascii")
                self.assertEqual(auth._listener_socket_inodes(runtime), {"67890"})

    def test_connected_socket_must_match_validated_process_before_auth_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            guard = auth.RuntimeProcessGuard(pid=PID, start_time=17, pidfd=19)
            session = issued_session()
            socket_validator = auth._bound_socket_validator(runtime, guard)
            transport = client.T3Client(
                runtime.origin,
                session.token,
                connected_socket_validator=socket_validator,
            )

            def connect(connection: object) -> None:
                connection.sock = mock.Mock()  # type: ignore[attr-defined]

            stale = client.ConfigurationError("foreign accepted socket")
            with mock.patch.object(
                auth, "_validate_runtime_process_guard"
            ), mock.patch.object(
                auth, "_validate_listener_process"
            ), mock.patch.object(
                client.http.client.HTTPConnection,
                "connect",
                autospec=True,
                side_effect=connect,
            ), mock.patch.object(
                auth,
                "_validate_connected_socket_process",
                side_effect=stale,
            ), mock.patch.object(
                client.http.client.HTTPConnection, "request", autospec=True
            ) as request:
                with self.assertRaises(client.ConfigurationError):
                    transport.get_environment_descriptor()

            request.assert_not_called()

    def test_connected_socket_waits_for_delayed_server_accept_before_auth_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            guard = auth.RuntimeProcessGuard(pid=PID, start_time=17, pidfd=19)
            sock = mock.Mock()
            with mock.patch.object(
                auth, "_validate_runtime_process_guard"
            ), mock.patch.object(
                auth,
                "_connected_socket_inodes",
                side_effect=[set(), {"12345"}],
            ) as connected, mock.patch.object(
                auth, "_process_socket_inodes", return_value={"12345"}
            ), mock.patch.object(
                auth.time, "monotonic", side_effect=[10.0, 10.01]
            ), mock.patch.object(auth.time, "sleep") as sleep:
                auth._validate_connected_socket_process(
                    runtime,
                    guard,
                    sock,
                    deadline=10.1,
                )

            self.assertEqual(connected.call_count, 2)
            sleep.assert_called_once_with(0.01)

    def test_invalid_issued_session_is_revoked_once_safe_id_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            session = issued_session()
            payload = issued_payload(session)
            payload["scopes"] = list(EXPECTED_ADMIN_SCOPES[:-1])
            with mock.patch.object(
                auth, "_run_cli", return_value=json.dumps(payload)
            ), mock.patch.object(auth, "revoke_local_session") as revoke:
                with self.assertRaises(client.ConfigurationError):
                    auth.issue_local_session(runtime)

            revoke.assert_called_once_with(runtime, session.session_id)

    def test_invalid_session_cleanup_failure_is_sanitized_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            session = issued_session()
            payload = issued_payload(session)
            payload["scopes"] = []
            with mock.patch.object(
                auth, "_run_cli", return_value=json.dumps(payload)
            ), mock.patch.object(
                auth,
                "revoke_local_session",
                side_effect=RuntimeError(
                    f"private revoke {session.session_id} {session.token}"
                ),
            ):
                with self.assertRaises(client.ConfigurationError) as caught:
                    auth.issue_local_session(runtime)

            encoded = json.dumps(caught.exception.to_dict())
            self.assertIn('"auth_cleanup": "failed"', encoded)
            self.assertNotIn(session.session_id, encoded)
            self.assertNotIn(session.token, encoded)
            self.assertNotIn("private revoke", encoded)

    def test_environment_descriptor_identity_and_version_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            cases = (
                {
                    "environmentId": "different-environment",
                    "serverVersion": runtime.server_version,
                },
                {
                    "environmentId": runtime.environment_id,
                    "serverVersion": "0.0.0-different",
                },
            )
            for descriptor in cases:
                with self.subTest(descriptor=descriptor):
                    transport = mock.Mock()
                    transport.get_environment_descriptor.return_value = descriptor
                    with self.assertRaises(client.ConfigurationError):
                        auth._validate_runtime_environment(transport, runtime)
                    transport.get_environment_descriptor.assert_called_once_with()

    def test_local_session_is_always_revoked_on_success_and_handler_error(self) -> None:
        for body_error in (False, True):
            with self.subTest(body_error=body_error), tempfile.TemporaryDirectory() as directory:
                runtime = local_runtime(Path(directory))
                session = issued_session()
                transport = mock.Mock()
                with self._local_patches(runtime, session, transport), mock.patch.object(
                    auth, "revoke_local_session"
                ) as revoke:
                    if body_error:
                        with self.assertRaisesRegex(RuntimeError, "handler failed"):
                            with auth.operation_client(FakeContext(), {}) as lease:
                                self.assertIs(lease.client, transport)
                                raise RuntimeError("handler failed")
                    else:
                        with auth.operation_client(FakeContext(), {}) as lease:
                            self.assertIs(lease.client, transport)
                revoke.assert_called_once_with(runtime, session.session_id)

    def test_environment_validation_failure_still_revokes_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            session = issued_session()
            transport = mock.Mock()
            failure = client.ConfigurationError("environment mismatch")
            with self._local_patches(runtime, session, transport), mock.patch.object(
                auth, "_validate_runtime_environment", side_effect=failure
            ), mock.patch.object(auth, "revoke_local_session") as revoke:
                with self.assertRaises(client.ConfigurationError):
                    with auth.operation_client(FakeContext(), {}):
                        self.fail("environment mismatch was accepted")
            revoke.assert_called_once_with(runtime, session.session_id)

    def test_cleanup_failure_is_sanitized_metadata_not_mutation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            session = issued_session()
            transport = mock.Mock()
            private_error = RuntimeError(
                f"could not revoke {session.session_id} with {session.token}"
            )
            with self._local_patches(runtime, session, transport), mock.patch.object(
                auth, "revoke_local_session", side_effect=private_error
            ):
                with auth.operation_client(FakeContext(), {}) as lease:
                    accepted = {"action": "mutation_accepted", "sequence": 17}
                    original_keys = set(accepted)
                annotated = lease.annotate(accepted)

            self.assertEqual(annotated["action"], "mutation_accepted")
            self.assertEqual(annotated["sequence"], 17)
            self.assertEqual(annotated["auth_cleanup"], "failed")
            added = set(annotated) - original_keys
            self.assertTrue(added)
            self.assertTrue(all(key.startswith("auth_cleanup") for key in added))
            encoded = json.dumps(annotated)
            self.assertIn(session.expires_at, encoded)
            self.assertLessEqual(len(encoded), 1_024)
            self.assertNotIn(session.token, encoded)
            self.assertNotIn(session.session_id, encoded)
            self.assertNotIn("could not revoke", encoded)

    def test_cleanup_failure_never_replaces_handler_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            session = issued_session()
            transport = mock.Mock()
            with self._local_patches(runtime, session, transport), mock.patch.object(
                auth,
                "revoke_local_session",
                side_effect=RuntimeError("private cleanup detail " + session.token),
            ):
                operation_error = client.ConfigurationError("handler error")
                with self.assertRaisesRegex(client.ConfigurationError, "handler error") as caught:
                    with auth.operation_client(FakeContext(), {}):
                        raise operation_error
            self.assertNotIn(session.token, str(caught.exception))
            encoded = json.dumps(caught.exception.to_dict())
            self.assertIn('"auth_cleanup": "failed"', encoded)
            self.assertNotIn(session.session_id, encoded)
            self.assertNotIn(session.token, encoded)
            self.assertNotIn("private cleanup detail", encoded)

    def test_bound_handler_preserves_only_sanitized_cleanup_warning_on_generic_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = local_runtime(Path(directory))
            session = issued_session()
            transport = mock.Mock()
            private_handler = "private handler detail " + session.token
            private_cleanup = "private cleanup detail " + session.token

            def operation(ctx: object, _args: object) -> dict[str, object]:
                def fail(_transport: object) -> dict[str, object]:
                    raise RuntimeError(private_handler)

                return tools._execute_operation(ctx, {}, fail)

            with self._local_patches(runtime, session, transport), mock.patch.object(
                auth,
                "revoke_local_session",
                side_effect=RuntimeError(private_cleanup),
            ):
                result = json.loads(tools.bind_handler(FakeContext(), operation)({}))

            encoded = json.dumps(result)
            self.assertEqual(result["error_code"], "internal_error")
            self.assertEqual(result["details"]["auth_cleanup"], "failed")
            self.assertIn("Do not repeat an accepted mutation", encoded)
            self.assertNotIn(session.token, encoded)
            self.assertNotIn(session.session_id, encoded)
            self.assertNotIn(private_handler, encoded)
            self.assertNotIn(private_cleanup, encoded)


if __name__ == "__main__":
    unittest.main()
