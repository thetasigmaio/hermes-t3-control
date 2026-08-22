"""Operation-scoped authentication for the local T3 orchestration client."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import pathlib
import re
import selectors
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

try:
    from .client import (
        ConfigurationError,
        T3Client,
        T3ClientError,
        is_valid_bearer_credential,
        probe_environment_descriptor,
    )
except ImportError:  # Direct repository import used by unit tests.
    from client import (
        ConfigurationError,
        T3Client,
        T3ClientError,
        is_valid_bearer_credential,
        probe_environment_descriptor,
    )


TOKEN_ENV = "T3_ORCHESTRATION_TOKEN"
EXPLICIT_AUTH_MODES = frozenset({"external-token", "local-cli"})
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
MAX_CLI_STDOUT_BYTES = 32 * 1024
CLI_READ_CHUNK_BYTES = 4 * 1024
CLI_TIMEOUT_SECONDS = 20.0
MAX_RUNTIME_JSON_BYTES = 16 * 1024
MAX_PACKAGE_JSON_BYTES = 16 * 1024
MAX_PROCESS_ARGV_BYTES = 64 * 1024
MAX_ENVIRONMENT_ID_BYTES = 512
MAX_SESSION_ID_CHARS = 512
MAX_EXPIRY_CHARS = 64
MAX_PROC_NET_BYTES = 2 * 1024 * 1024
MAX_PROCESS_FDS = 16 * 1024
CONNECTED_SOCKET_POLL_SECONDS = 0.01
PROC_ROOT = "/proc/"

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9._:-]+", re.ASCII)
_SAFE_VERSION_RE = re.compile(r"[A-Za-z0-9._+-]+", re.ASCII)
_SOCKET_LINK_RE = re.compile(r"socket:\[([0-9]+)\]", re.ASCII)


@dataclass(frozen=True)
class LocalRuntime:
    base_dir: pathlib.Path
    origin: str
    node_path: pathlib.Path
    cli_path: pathlib.Path
    environment_id: str
    server_version: str


@dataclass(frozen=True)
class LocalSession:
    session_id: str
    token: str
    expires_at: str


@dataclass(frozen=True)
class RuntimeProcessGuard:
    pid: int
    start_time: int
    pidfd: int


@dataclass
class ClientLease:
    client: T3Client
    _expires_at: str | None = None
    _cleanup_failed: bool = False

    def annotate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Attach bounded, non-secret cleanup state after an accepted operation."""
        if not self._cleanup_failed:
            return payload
        result = dict(payload)
        result.update(_cleanup_metadata(self._expires_at))
        return result


def _profile_secret(name: str) -> str | None:
    from agent.secret_scope import get_secret

    return get_secret(name)


def _configuration_error(message: str) -> ConfigurationError:
    return ConfigurationError(message)


def _read_bounded_descriptor(descriptor: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size <= maximum:
        chunk = os.read(descriptor, min(8_192, maximum + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    value = b"".join(chunks)
    if len(value) > maximum:
        raise _configuration_error("Local T3 metadata exceeds its safe size limit.")
    return value


def _read_proc_file(path: pathlib.Path, maximum: int) -> bytes:
    """Read a bounded procfs pseudo-file, whose size and owner are not file-like."""
    descriptor: int | None = None
    try:
        descriptor = os.open(
            os.fspath(path),
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
        return _read_bounded_descriptor(descriptor, maximum)
    except ConfigurationError:
        raise
    except Exception:
        raise _configuration_error("The local T3 process metadata is unavailable.") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_local_metadata_file(path: pathlib.Path, maximum: int) -> bytes:
    """Read one same-user regular metadata file through its pinned descriptor."""
    descriptor: int | None = None
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        if not no_follow:
            before = os.stat(os.fspath(path), follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                raise _configuration_error("Local T3 metadata is unavailable or unsafe.")
        descriptor = os.open(os.fspath(path), flags | no_follow)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size < 0
            or metadata.st_size > maximum
        ):
            raise _configuration_error("Local T3 metadata is unavailable or unsafe.")
        if not no_follow and (
            metadata.st_dev != before.st_dev or metadata.st_ino != before.st_ino
        ):
            raise _configuration_error("Local T3 metadata is unavailable or unsafe.")
        return _read_bounded_descriptor(descriptor, maximum)
    except ConfigurationError:
        raise
    except Exception:
        raise _configuration_error("Local T3 metadata is unavailable or unsafe.") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_local_json_file(path: pathlib.Path, maximum: int) -> Any:
    try:
        return json.loads(_read_local_metadata_file(path, maximum).decode("utf-8"))
    except ConfigurationError:
        raise
    except Exception:
        raise _configuration_error("Local T3 metadata is invalid.") from None


def _regular_file(path: pathlib.Path, *, executable: bool = False) -> bool:
    try:
        metadata = os.stat(os.fspath(path))
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode) and (
        not executable or bool(metadata.st_mode & 0o111)
    )


def _process_uid(pid: int) -> int | None:
    try:
        return os.stat(f"{PROC_ROOT}{pid}").st_uid
    except OSError:
        return None


def _process_argv(pid: int) -> tuple[str, ...]:
    try:
        raw = _read_proc_file(
            pathlib.Path(f"{PROC_ROOT}{pid}/cmdline"), MAX_PROCESS_ARGV_BYTES
        )
        parts = raw.split(b"\0")
        if parts and parts[-1] == b"":
            parts.pop()
        return tuple(part.decode("utf-8") for part in parts if part)
    except Exception:
        raise _configuration_error("The local T3 process metadata is unavailable.") from None


def _process_exe(pid: int) -> pathlib.Path:
    try:
        return pathlib.Path(os.readlink(f"{PROC_ROOT}{pid}/exe"))
    except OSError:
        raise _configuration_error("The local T3 process executable is unavailable.") from None


def _process_start_time(pid: int) -> int:
    try:
        raw = _read_proc_file(
            pathlib.Path(f"{PROC_ROOT}{pid}/stat"), MAX_PROCESS_ARGV_BYTES
        ).decode("ascii")
        closing_parenthesis = raw.rfind(")")
        fields = raw[closing_parenthesis + 2 :].split()
        value = int(fields[19])
        if closing_parenthesis <= 0 or value <= 0:
            raise ValueError
        return value
    except Exception:
        raise _configuration_error("The local T3 process identity is unavailable.") from None


def _runtime_pid(runtime: LocalRuntime) -> int:
    state = _read_local_json_file(
        runtime.base_dir / "userdata" / "server-runtime.json",
        MAX_RUNTIME_JSON_BYTES,
    )
    if not isinstance(state, dict):
        raise _configuration_error("The local T3 runtime state is invalid.")
    pid = state.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise _configuration_error("The local T3 runtime state is invalid.")
    return pid


def _pidfd_has_exited(pidfd: int) -> bool:
    selector = selectors.DefaultSelector()
    try:
        selector.register(pidfd, selectors.EVENT_READ)
        return bool(selector.select(0))
    except Exception:
        raise _configuration_error("The local T3 process identity is unavailable.") from None
    finally:
        selector.close()


def _validate_runtime_process_guard(
    runtime: LocalRuntime, guard: RuntimeProcessGuard
) -> None:
    if _pidfd_has_exited(guard.pidfd):
        raise _configuration_error("The local T3 process changed during authentication.")
    if (
        _runtime_pid(runtime) != guard.pid
        or _process_start_time(guard.pid) != guard.start_time
        or resolve_local_runtime(runtime.base_dir) != runtime
        or _pidfd_has_exited(guard.pidfd)
    ):
        raise _configuration_error("The local T3 process changed during authentication.")


@contextlib.contextmanager
def _pinned_runtime_process(runtime: LocalRuntime) -> Iterator[RuntimeProcessGuard]:
    pid = _runtime_pid(runtime)
    start_time = _process_start_time(pid)
    try:
        pidfd = os.pidfd_open(pid, 0)
    except Exception:
        raise _configuration_error("The local T3 process identity cannot be pinned.") from None
    guard = RuntimeProcessGuard(pid=pid, start_time=start_time, pidfd=pidfd)
    try:
        _validate_runtime_process_guard(runtime, guard)
        yield guard
    finally:
        try:
            os.close(pidfd)
        except OSError:
            pass


def _proc_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    packed = address.packed
    if address.version == 4:
        return packed[::-1].hex().upper()
    return b"".join(
        packed[offset : offset + 4][::-1] for offset in range(0, len(packed), 4)
    ).hex().upper()


def _listener_socket_inodes(runtime: LocalRuntime) -> set[str]:
    parsed = urlsplit(runtime.origin)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError:
        raise _configuration_error("The local T3 runtime origin is invalid.") from None
    if port is None:
        raise _configuration_error("The local T3 runtime origin is invalid.")
    table_name = "tcp" if address.version == 4 else "tcp6"
    target = f"{_proc_address(address)}:{port:04X}"
    wildcard = f"{'0' * len(_proc_address(address))}:{port:04X}"
    try:
        lines = _read_proc_file(
            pathlib.Path(PROC_ROOT) / "net" / table_name, MAX_PROC_NET_BYTES
        ).decode("ascii").splitlines()
    except ConfigurationError:
        raise
    except Exception:
        raise _configuration_error("The local T3 listener identity is unavailable.") from None
    exact_inodes: set[str] = set()
    wildcard_inodes: set[str] = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 10:
            raise _configuration_error("The local T3 listener identity is unavailable.")
        if fields[1] in {target, wildcard} and fields[3] == "0A":
            inode = fields[9]
            if not inode.isascii() or not inode.isdigit() or inode == "0":
                raise _configuration_error("The local T3 listener identity is unavailable.")
            if fields[1] == target:
                exact_inodes.add(inode)
            else:
                wildcard_inodes.add(inode)
    return exact_inodes or wildcard_inodes


def _process_socket_inodes(pid: int) -> set[str]:
    try:
        entries = os.scandir(f"{PROC_ROOT}{pid}/fd")
    except OSError:
        raise _configuration_error("The local T3 listener identity is unavailable.") from None
    inodes: set[str] = set()
    try:
        for count, entry in enumerate(entries, start=1):
            if count > MAX_PROCESS_FDS:
                raise _configuration_error("The local T3 process has too many open files.")
            try:
                target = os.readlink(entry.path)
            except OSError:
                continue
            match = _SOCKET_LINK_RE.fullmatch(target)
            if match is not None:
                inodes.add(match.group(1))
    finally:
        entries.close()
    return inodes


def _validate_listener_process(runtime: LocalRuntime, pid: int) -> None:
    listeners = _listener_socket_inodes(runtime)
    if not listeners or not listeners.issubset(_process_socket_inodes(pid)):
        raise _configuration_error(
            "The local T3 loopback listener is not owned by the validated process."
        )


def _connected_socket_inodes(sock: Any) -> set[str]:
    try:
        client_endpoint = sock.getsockname()
        server_endpoint = sock.getpeername()
        client_address = ipaddress.ip_address(client_endpoint[0])
        server_address = ipaddress.ip_address(server_endpoint[0])
        client_port = client_endpoint[1]
        server_port = server_endpoint[1]
    except Exception:
        raise _configuration_error("The local T3 connection identity is unavailable.") from None
    if (
        client_address.version != server_address.version
        or not server_address.is_loopback
        or not isinstance(client_port, int)
        or isinstance(client_port, bool)
        or not 1 <= client_port <= 65_535
        or not isinstance(server_port, int)
        or isinstance(server_port, bool)
        or not 1 <= server_port <= 65_535
    ):
        raise _configuration_error("The local T3 connection identity is unavailable.")
    table_name = "tcp" if server_address.version == 4 else "tcp6"
    local = f"{_proc_address(server_address)}:{server_port:04X}"
    remote = f"{_proc_address(client_address)}:{client_port:04X}"
    try:
        lines = _read_proc_file(
            pathlib.Path(PROC_ROOT) / "net" / table_name, MAX_PROC_NET_BYTES
        ).decode("ascii").splitlines()
    except ConfigurationError:
        raise
    except Exception:
        raise _configuration_error("The local T3 connection identity is unavailable.") from None
    inodes: set[str] = set()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 10:
            raise _configuration_error("The local T3 connection identity is unavailable.")
        if fields[1] == local and fields[2] == remote and fields[3] == "01":
            inode = fields[9]
            if not inode.isascii() or not inode.isdigit():
                raise _configuration_error("The local T3 connection identity is unavailable.")
            # Linux temporarily exposes the server-side row with inode zero until
            # userspace accepts the already-connected loopback socket.
            if inode == "0":
                continue
            inodes.add(inode)
    return inodes


def _validate_connected_socket_process(
    runtime: LocalRuntime,
    guard: RuntimeProcessGuard,
    sock: Any,
    *,
    deadline: float,
) -> None:
    while True:
        _validate_runtime_process_guard(runtime, guard)
        connections = _connected_socket_inodes(sock)
        if connections:
            if not connections.issubset(_process_socket_inodes(guard.pid)):
                raise _configuration_error(
                    "The local T3 connection is not owned by the validated process."
                )
            _validate_runtime_process_guard(runtime, guard)
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _configuration_error(
                "The local T3 connection identity is unavailable."
            )
        time.sleep(min(CONNECTED_SOCKET_POLL_SECONDS, remaining))


def _bound_socket_validator(
    runtime: LocalRuntime, guard: RuntimeProcessGuard
) -> Callable[[Any, float], None]:
    def validate(sock: Any, deadline: float) -> None:
        _validate_runtime_process_guard(runtime, guard)
        _validate_listener_process(runtime, guard.pid)
        _validate_connected_socket_process(runtime, guard, sock, deadline=deadline)

    return validate


def _validate_unauthenticated_runtime(
    runtime: LocalRuntime, guard: RuntimeProcessGuard
) -> None:
    _validate_local_runtime_process(runtime, guard)
    descriptor = probe_environment_descriptor(runtime.origin)
    if (
        descriptor.get("environmentId") != runtime.environment_id
        or descriptor.get("serverVersion") != runtime.server_version
    ):
        raise _configuration_error("The live T3 environment does not match local metadata.")
    _validate_local_runtime_process(runtime, guard)


def _validate_local_runtime_process(
    runtime: LocalRuntime, guard: RuntimeProcessGuard
) -> None:
    """Validate pinned process and listener identity without network I/O."""
    _validate_runtime_process_guard(runtime, guard)
    _validate_listener_process(runtime, guard.pid)


def _base_directory(value: Any) -> pathlib.Path:
    if value is None:
        return pathlib.Path.home() / ".t3"
    if isinstance(value, pathlib.Path):
        path = value
    elif isinstance(value, str) and value and value == value.strip() and len(value) <= 4_096:
        path = pathlib.Path(value).expanduser()
    else:
        raise _configuration_error("The configured T3 base directory is invalid.")
    if not path.is_absolute():
        raise _configuration_error("The configured T3 base directory is invalid.")
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        raise _configuration_error("The configured T3 base directory is invalid.") from None


def _runtime_origin(value: Any, port_value: Any) -> str:
    if not isinstance(value, str) or not isinstance(port_value, int) or isinstance(
        port_value, bool
    ):
        raise _configuration_error("The local T3 runtime origin is invalid.")
    try:
        parsed = urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError:
        raise _configuration_error("The local T3 runtime origin is invalid.") from None
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
        or port != port_value
        or not 1 <= port <= 65_535
    ):
        raise _configuration_error("The local T3 runtime origin is invalid.")
    display_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    normalized = f"http://{display_host}:{port}"
    if value.rstrip("/") != normalized:
        raise _configuration_error("The local T3 runtime origin is invalid.")
    return normalized


def _bounded_text(value: Any, *, maximum: int, pattern: re.Pattern[str]) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or value != value.strip()
        or pattern.fullmatch(value) is None
    ):
        raise _configuration_error("Local T3 metadata contains an invalid identity.")
    return value


def resolve_local_runtime(base_dir: Any = None) -> LocalRuntime:
    """Resolve a same-user live T3 runtime without performing network I/O."""
    root = _base_directory(base_dir)
    state_dir = root / "userdata"
    runtime_state = _read_local_json_file(
        state_dir / "server-runtime.json", MAX_RUNTIME_JSON_BYTES
    )
    if not isinstance(runtime_state, dict):
        raise _configuration_error("The local T3 runtime state is invalid.")
    pid = runtime_state.get("pid")
    if (
        runtime_state.get("version") != 1
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(runtime_state.get("startedAt"), str)
    ):
        raise _configuration_error("The local T3 runtime state is invalid.")
    origin = _runtime_origin(runtime_state.get("origin"), runtime_state.get("port"))

    process_uid = _process_uid(pid)
    if process_uid is None or process_uid != os.getuid():
        raise _configuration_error("The local T3 runtime process is unavailable or foreign.")
    argv = _process_argv(pid)
    if len(argv) < 2:
        raise _configuration_error("The local T3 runtime process metadata is invalid.")

    process_exe = _process_exe(pid).resolve(strict=False)
    argv_node = pathlib.Path(argv[0]).resolve(strict=False)
    cli_path = pathlib.Path(argv[1]).resolve(strict=False)
    if argv_node != process_exe or not _regular_file(process_exe, executable=True):
        raise _configuration_error("The local T3 Node executable does not match its process.")
    if tuple(cli_path.parts[-4:]) != ("apps", "server", "dist", "bin.mjs"):
        raise _configuration_error("The local T3 CLI entrypoint is invalid.")
    if not _regular_file(cli_path):
        raise _configuration_error("The local T3 CLI entrypoint is unavailable.")

    package_root = cli_path.parents[3]
    package = _read_local_json_file(
        package_root / "package.json", MAX_PACKAGE_JSON_BYTES
    )
    if not isinstance(package, dict) or package.get("name") != "t3code-server":
        raise _configuration_error("The local T3 CLI package metadata is invalid.")
    server_version = _bounded_text(
        package.get("version"), maximum=128, pattern=_SAFE_VERSION_RE
    )
    if package_root.name != server_version:
        raise _configuration_error("The local T3 CLI version metadata is inconsistent.")

    try:
        environment_id = _read_local_metadata_file(
            state_dir / "environment-id", MAX_ENVIRONMENT_ID_BYTES
        ).decode("utf-8").strip()
    except ConfigurationError:
        raise
    except Exception:
        raise _configuration_error("The local T3 environment identity is invalid.") from None
    environment_id = _bounded_text(
        environment_id, maximum=256, pattern=_SAFE_ID_RE
    )
    if not _regular_file(state_dir / "state.sqlite"):
        raise _configuration_error("The local T3 state database is unavailable.")

    return LocalRuntime(
        base_dir=root,
        origin=origin,
        node_path=process_exe,
        cli_path=cli_path,
        environment_id=environment_id,
        server_version=server_version,
    )


def _fixed_cli_environment() -> dict[str, str]:
    return {
        "HOME": str(pathlib.Path.home()),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
        "PATH": "/usr/bin:/bin",
    }


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    try:
        process.wait(timeout=1.0)
    except Exception:
        pass


def _read_process_stdout(
    process: subprocess.Popen[Any], *, deadline: float
) -> str:
    stream = process.stdout
    if stream is None:
        _terminate_process(process)
        raise _configuration_error("The private T3 CLI output is unavailable.")
    descriptor = stream.fileno()
    collected = bytearray()
    selector = selectors.DefaultSelector()
    try:
        selector.register(descriptor, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(process)
                raise _configuration_error("The private T3 CLI operation timed out.")
            if not selector.select(remaining):
                _terminate_process(process)
                raise _configuration_error("The private T3 CLI operation timed out.")
            chunk = os.read(
                descriptor,
                min(
                    CLI_READ_CHUNK_BYTES,
                    MAX_CLI_STDOUT_BYTES + 1 - len(collected),
                ),
            )
            if not chunk:
                break
            collected.extend(chunk)
            if len(collected) > MAX_CLI_STDOUT_BYTES:
                _terminate_process(process)
                raise _configuration_error("The private T3 CLI output was too large.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process(process)
            raise _configuration_error("The private T3 CLI operation timed out.")
        try:
            return_code = process.wait(timeout=remaining)
        except Exception:
            _terminate_process(process)
            raise _configuration_error("The private T3 CLI operation timed out.") from None
        if return_code != 0:
            raise _configuration_error("The private T3 CLI operation failed.")
        try:
            return bytes(collected).decode("utf-8")
        except UnicodeDecodeError:
            raise _configuration_error("The private T3 CLI output was invalid.") from None
    finally:
        selector.close()
        try:
            stream.close()
        except Exception:
            pass


def _run_cli(runtime: LocalRuntime, arguments: list[str], *, capture: bool) -> str:
    argv = [str(runtime.node_path), str(runtime.cli_path), *arguments]
    try:
        process = subprocess.Popen(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_fixed_cli_environment(),
            close_fds=True,
            start_new_session=True,
            bufsize=0,
        )
    except Exception:
        raise _configuration_error("The private T3 CLI operation could not start.") from None
    deadline = time.monotonic() + CLI_TIMEOUT_SECONDS
    if capture:
        try:
            return _read_process_stdout(process, deadline=deadline)
        except ConfigurationError:
            raise
        except Exception:
            _terminate_process(process)
            raise _configuration_error("The private T3 CLI operation failed.") from None
    try:
        return_code = process.wait(timeout=CLI_TIMEOUT_SECONDS)
    except Exception:
        _terminate_process(process)
        raise _configuration_error("The private T3 CLI operation timed out.") from None
    if return_code != 0:
        raise _configuration_error("The private T3 CLI operation failed.")
    return ""


def _parse_rfc3339(value: Any) -> datetime:
    if not isinstance(value, str) or not value or len(value) > MAX_EXPIRY_CHARS:
        raise _configuration_error("The private T3 CLI returned an invalid session.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise _configuration_error("The private T3 CLI returned an invalid session.") from None
    if parsed.utcoffset() is None:
        raise _configuration_error("The private T3 CLI returned an invalid session.")
    return parsed.astimezone(timezone.utc)


def _cleanup_metadata(expires_at: str | None) -> dict[str, str]:
    expiry = expires_at or "the short lease deadline"
    return {
        "auth_cleanup": "failed",
        "auth_cleanup_warning": (
            "Temporary T3 authentication cleanup could not be confirmed; "
            f"the credential expires at {expiry}. Do not repeat an accepted mutation; "
            "reconcile the exact target state first."
        )[:512],
    }


def _annotate_cleanup_error(error: BaseException, expires_at: str | None) -> None:
    metadata = _cleanup_metadata(expires_at)
    if isinstance(error, T3ClientError):
        details = dict(error.details)
        details.update(metadata)
        error.details = details
        return
    try:
        error.auth_cleanup_metadata = metadata  # type: ignore[attr-defined]
        error.add_note(metadata["auth_cleanup_warning"])
    except Exception:
        pass


def _safe_session_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= MAX_SESSION_ID_CHARS
        or _SAFE_ID_RE.fullmatch(value) is None
    ):
        raise _configuration_error("The private T3 CLI returned an invalid session.")
    return value


def _list_local_session_ids(runtime: LocalRuntime) -> frozenset[str]:
    output = _run_cli(
        runtime,
        [
            "auth",
            "session",
            "list",
            "--json",
            "--base-dir",
            str(runtime.base_dir),
        ],
        capture=True,
    )
    try:
        active = json.loads(output)
        if not isinstance(active, list):
            raise ValueError
        session_ids = tuple(
            _safe_session_id(item.get("sessionId"))
            for item in active
            if isinstance(item, dict)
        )
        if len(session_ids) != len(active) or len(set(session_ids)) != len(session_ids):
            raise ValueError
        return frozenset(session_ids)
    except Exception:
        raise _configuration_error(
            "The private T3 session list was invalid."
        ) from None


def _cleanup_issued_session(
    runtime: LocalRuntime,
    previous_session_ids: frozenset[str],
    session_id: str | None,
) -> bool:
    try:
        if session_id is not None:
            revoke_local_session(runtime, session_id)
            return True
        new_session_ids = _list_local_session_ids(runtime) - previous_session_ids
        if not new_session_ids:
            return True
        # Never guess among concurrent issuances; the caller reports the bounded
        # lease expiry when one exact cleanup target cannot be established.
        if len(new_session_ids) != 1:
            return False
        revoke_local_session(runtime, next(iter(new_session_ids)))
        return True
    except Exception:
        return False


def issue_local_session(runtime: LocalRuntime) -> LocalSession:
    """Issue one validated five-minute administrative session in private memory."""
    previous_session_ids = _list_local_session_ids(runtime)
    session_id: str | None = None
    safe_expiry: str | None = None
    try:
        output = _run_cli(
            runtime,
            [
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
            ],
            capture=True,
        )
        try:
            issued = json.loads(output)
        except Exception:
            raise _configuration_error(
                "The private T3 CLI returned an invalid session."
            ) from None
        if not isinstance(issued, dict):
            raise _configuration_error("The private T3 CLI returned an invalid session.")

        expires_at = issued.get("expiresAt")
        expiry: datetime | None = None
        expiry_error: ConfigurationError | None = None
        try:
            expiry = _parse_rfc3339(expires_at)
            safe_expiry = expires_at
        except ConfigurationError as error:
            expiry_error = error
        session_id = _safe_session_id(issued.get("sessionId"))
        if expiry_error is not None:
            raise expiry_error
        if expiry is None:
            raise _configuration_error("The private T3 CLI returned an invalid session.")

        token = issued.get("token")
        scopes = issued.get("scopes")
        client_metadata = issued.get("client")
        if (
            not is_valid_bearer_credential(token)
            or issued.get("method") != "bearer-access-token"
            or not isinstance(scopes, list)
            or tuple(scopes) != EXPECTED_ADMIN_SCOPES
            or issued.get("subject") != "hermes-t3-control"
            or not isinstance(client_metadata, dict)
            or client_metadata.get("label") != "hermes-t3-control"
            or client_metadata.get("deviceType") != "bot"
        ):
            raise _configuration_error("The private T3 CLI returned an invalid session.")
        remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
        if remaining < 240 or remaining > 330:
            raise _configuration_error(
                "The private T3 CLI returned an invalid session expiry."
            )
        return LocalSession(
            session_id=session_id,
            token=token,
            expires_at=expires_at,
        )
    except ConfigurationError as error:
        if not _cleanup_issued_session(runtime, previous_session_ids, session_id):
            _annotate_cleanup_error(error, safe_expiry)
        raise
    except Exception:
        error = _configuration_error("The private T3 CLI returned an invalid session.")
        if not _cleanup_issued_session(runtime, previous_session_ids, session_id):
            _annotate_cleanup_error(error, safe_expiry)
        raise error from None


def revoke_local_session(runtime: LocalRuntime, session_id: str) -> None:
    """Revoke an exact session and confirm it is absent from the active list."""
    if (
        not isinstance(session_id, str)
        or not 0 < len(session_id) <= MAX_SESSION_ID_CHARS
        or _SAFE_ID_RE.fullmatch(session_id) is None
    ):
        raise _configuration_error("The private T3 session identity is invalid.")
    revoke_failed = False
    try:
        _run_cli(
            runtime,
            [
                "auth",
                "session",
                "revoke",
                session_id,
                "--base-dir",
                str(runtime.base_dir),
            ],
            capture=False,
        )
    except ConfigurationError:
        revoke_failed = True

    try:
        active_ids = _list_local_session_ids(runtime)
        confirmed_absent = session_id not in active_ids
    except Exception:
        confirmed_absent = False
    if revoke_failed or not confirmed_absent:
        raise _configuration_error("Temporary T3 authentication cleanup was not confirmed.")


def _validate_runtime_environment(client: T3Client, runtime: LocalRuntime) -> None:
    descriptor = client.get_environment_descriptor()
    if (
        not isinstance(descriptor, dict)
        or descriptor.get("environmentId") != runtime.environment_id
        or descriptor.get("serverVersion") != runtime.server_version
    ):
        raise _configuration_error("The live T3 environment does not match local metadata.")


def _config(ctx: Any, key: str, default: Any = None) -> Any:
    try:
        return ctx.get_config(key, default)
    except Exception:
        raise _configuration_error("The plugin authentication configuration is unavailable.") from None


def _external_secret(*, explicit: bool) -> str | None:
    try:
        value = _profile_secret(TOKEN_ENV)
    except Exception:
        raise _configuration_error("The profile-scoped T3 credential is unavailable.") from None
    if value is None and not explicit:
        return None
    if is_valid_bearer_credential(value):
        return value
    raise _configuration_error("The profile-scoped T3 credential is unavailable.")


@contextlib.contextmanager
def operation_client(
    ctx: Any, public_arguments: Mapping[str, Any]
) -> Iterator[ClientLease]:
    """Yield one preflighted client backed by an operation-scoped auth lease."""
    configured_mode = _config(ctx, "auth_mode", None)
    if configured_mode is not None and (
        not isinstance(configured_mode, str)
        or configured_mode not in EXPLICIT_AUTH_MODES
    ):
        raise _configuration_error("The plugin authentication mode is unsupported.")

    external_value: str | None = None
    if configured_mode == "external-token":
        external_value = _external_secret(explicit=True)
    elif configured_mode is None:
        external_value = _external_secret(explicit=False)

    if external_value is not None:
        transport = T3Client(_config(ctx, "base_url", ""), external_value)
        transport._preflight_public_arguments(public_arguments)
        yield ClientLease(client=transport)
        return

    configured_base_dir = _config(ctx, "t3_base_dir", None)
    runtime = (
        resolve_local_runtime()
        if configured_base_dir is None
        else resolve_local_runtime(configured_base_dir)
    )
    with _pinned_runtime_process(runtime) as process_guard:
        _validate_local_runtime_process(runtime, process_guard)
        session = issue_local_session(runtime)
        lease: ClientLease | None = None
        operation_error: BaseException | None = None
        try:
            transport = T3Client(
                runtime.origin,
                session.token,
                connected_socket_validator=_bound_socket_validator(
                    runtime, process_guard
                ),
            )
            lease = ClientLease(client=transport, _expires_at=session.expires_at)
            transport._preflight_public_arguments(public_arguments)
            _validate_local_runtime_process(runtime, process_guard)
            _validate_runtime_environment(transport, runtime)
            yield lease
        except BaseException as error:
            operation_error = error
            raise
        finally:
            try:
                revoke_local_session(runtime, session.session_id)
            except Exception:
                if lease is not None:
                    lease._cleanup_failed = True
                if operation_error is not None:
                    _annotate_cleanup_error(operation_error, session.expires_at)
