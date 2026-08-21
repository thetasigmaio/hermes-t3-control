#!/usr/bin/env python3
"""Build the deterministic, allowlisted Hermes T3 Control release archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import secrets
import stat
import sys
import tarfile
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "hermes-t3-control"
RELEASE_FILES = (
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "__init__.py",
    "client.py",
    "plugin.yaml",
    "schemas.py",
    "tools.py",
)
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class ReleaseBuildError(Exception):
    """A sanitized release-build failure."""


def _manifest_version() -> str:
    manifest_path = SOURCE_ROOT / "plugin.yaml"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError("plugin.yaml is missing or invalid JSON.") from exc
    version = manifest.get("version")
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise ReleaseBuildError("plugin.yaml contains an unsafe release version.")
    return version


def _release_inputs() -> list[tuple[str, bytes]]:
    inputs: list[tuple[str, bytes]] = []
    for relative_name in sorted(RELEASE_FILES):
        source = SOURCE_ROOT / relative_name
        if source.is_symlink() or not source.is_file():
            raise ReleaseBuildError(f"Required release input is missing: {relative_name}")
        try:
            content = source.read_bytes()
        except OSError as exc:
            raise ReleaseBuildError(f"Could not read release input: {relative_name}") from exc
        inputs.append((relative_name, content))
    return inputs


def _tar_bytes(inputs: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        root = tarfile.TarInfo(f"{ARCHIVE_ROOT}/")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        root.mtime = 0
        root.uid = 0
        root.gid = 0
        root.uname = ""
        root.gname = ""
        archive.addfile(root)

        for relative_name, content in inputs:
            member = tarfile.TarInfo(f"{ARCHIVE_ROOT}/{relative_name}")
            member.size = len(content)
            member.mode = 0o644
            member.mtime = 0
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            archive.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


def _gzip_bytes(payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", compresslevel=9, fileobj=buffer, mtime=0
    ) as compressed:
        compressed.write(payload)
    return buffer.getvalue()


def _require_secure_publish_primitives() -> None:
    required_dir_fd_functions = (os.mkdir, os.open, os.rmdir, os.stat, os.unlink)
    if (
        os.name != "posix"
        or not hasattr(os, "geteuid")
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NONBLOCK")
        or not hasattr(os, "O_NOFOLLOW")
        or any(function not in os.supports_dir_fd for function in required_dir_fd_functions)
        or os.stat not in os.supports_follow_symlinks
    ):
        raise ReleaseBuildError(
            "Secure release artifact publishing is unavailable on this platform."
        )


def _require_secure_output_directory_metadata(
    entry: os.stat_result, effective_uid: int
) -> None:
    if not stat.S_ISDIR(entry.st_mode):
        raise ReleaseBuildError("Release output must resolve to a real directory.")
    if entry.st_uid != effective_uid:
        raise ReleaseBuildError("Release output directory has an unsafe owner.")
    if entry.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ReleaseBuildError("Release output directory has unsafe permissions.")


def _checked_output_directory_metadata(directory_fd: int) -> os.stat_result:
    try:
        entry = os.fstat(directory_fd)
    except OSError as exc:
        raise ReleaseBuildError("Could not inspect the release output directory.") from exc
    _require_secure_output_directory_metadata(entry, os.geteuid())
    return entry


def _open_output_directory(output_dir: Path) -> tuple[Path, int, os.stat_result]:
    _require_secure_publish_primitives()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        resolved_output_dir = output_dir.resolve(strict=True)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        flags |= getattr(os, "O_CLOEXEC", 0)
        directory_fd = os.open(resolved_output_dir, flags)
    except (OSError, RuntimeError) as exc:
        raise ReleaseBuildError(
            "Could not prepare the release output directory."
        ) from exc

    try:
        entry = _checked_output_directory_metadata(directory_fd)
        os.fsync(directory_fd)
    except ReleaseBuildError:
        os.close(directory_fd)
        raise
    except OSError as exc:
        os.close(directory_fd)
        raise ReleaseBuildError(
            "Could not safely access the release output directory."
        ) from exc
    return resolved_output_dir, directory_fd, entry


def _destination_state(directory_fd: int, name: str) -> tuple[int, int] | None:
    try:
        entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReleaseBuildError("Could not inspect a release artifact destination.") from exc
    if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
        raise ReleaseBuildError("Refusing an unsafe existing release artifact.")
    return entry.st_dev, entry.st_ino


def _require_private_regular_file(descriptor: int) -> os.stat_result:
    try:
        entry = os.fstat(descriptor)
    except OSError as exc:
        raise ReleaseBuildError("Could not inspect a release temporary file.") from exc
    if (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_nlink != 1
        or entry.st_uid != os.geteuid()
        or entry.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise ReleaseBuildError("Release temporary file is not a private regular file.")
    return entry


def _unlink_staging_entry(directory_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ReleaseBuildError("Could not clean a release staging entry.") from exc


def _require_staging_directory_metadata(
    entry: os.stat_result,
    *,
    expected_device: int,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    identity = (entry.st_dev, entry.st_ino)
    if (
        not stat.S_ISDIR(entry.st_mode)
        or entry.st_uid != os.geteuid()
        or stat.S_IMODE(entry.st_mode) != 0o700
        or entry.st_dev != expected_device
        or (expected_identity is not None and identity != expected_identity)
    ):
        raise ReleaseBuildError("Release staging directory is unsafe.")


def _create_staging_directory(
    output_directory_fd: int, output_entry: os.stat_result
) -> tuple[str, int, tuple[int, int]]:
    staging_name = ""
    for _ in range(16):
        staging_name = f".release-stage-{secrets.token_hex(16)}"
        try:
            os.mkdir(staging_name, 0o700, dir_fd=output_directory_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            raise ReleaseBuildError("Could not create a release staging directory.") from exc
        break
    else:
        raise ReleaseBuildError("Could not allocate a unique release staging directory.")

    staging_fd = -1
    succeeded = False
    try:
        named_entry = os.stat(
            staging_name,
            dir_fd=output_directory_fd,
            follow_symlinks=False,
        )
        _require_staging_directory_metadata(
            named_entry,
            expected_device=output_entry.st_dev,
        )
        identity = (named_entry.st_dev, named_entry.st_ino)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        flags |= getattr(os, "O_CLOEXEC", 0)
        staging_fd = os.open(staging_name, flags, dir_fd=output_directory_fd)
        pinned_entry = os.fstat(staging_fd)
        _require_staging_directory_metadata(
            pinned_entry,
            expected_device=output_entry.st_dev,
            expected_identity=identity,
        )
        os.fsync(staging_fd)
        succeeded = True
        return staging_name, staging_fd, identity
    except ReleaseBuildError:
        raise
    except OSError as exc:
        raise ReleaseBuildError("Could not safely open the release staging directory.") from exc
    finally:
        if not succeeded:
            cleanup_error: OSError | None = None
            if staging_fd >= 0:
                try:
                    os.close(staging_fd)
                except OSError as exc:
                    cleanup_error = exc
            try:
                os.rmdir(staging_name, dir_fd=output_directory_fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                cleanup_error = cleanup_error or exc
            if cleanup_error is not None:
                raise ReleaseBuildError(
                    "Could not clean the release staging directory."
                ) from cleanup_error


def _cleanup_staging_directory(
    output_directory_fd: int,
    staging_name: str,
    staging_fd: int,
    staging_identity: tuple[int, int],
    staging_entries: tuple[str, ...],
) -> None:
    cleanup_error: OSError | None = None
    for name in dict.fromkeys(staging_entries):
        try:
            os.unlink(name, dir_fd=staging_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            cleanup_error = cleanup_error or exc
    try:
        os.fsync(staging_fd)
    except OSError as exc:
        cleanup_error = cleanup_error or exc

    remove_named_directory = False
    try:
        named_entry = os.stat(
            staging_name,
            dir_fd=output_directory_fd,
            follow_symlinks=False,
        )
        if (
            stat.S_ISDIR(named_entry.st_mode)
            and (named_entry.st_dev, named_entry.st_ino) == staging_identity
        ):
            remove_named_directory = True
        else:
            cleanup_error = cleanup_error or OSError("staging identity changed")
    except FileNotFoundError:
        pass
    except OSError as exc:
        cleanup_error = cleanup_error or exc

    try:
        os.close(staging_fd)
    except OSError as exc:
        cleanup_error = cleanup_error or exc
    if remove_named_directory:
        try:
            os.rmdir(staging_name, dir_fd=output_directory_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            cleanup_error = cleanup_error or exc
    try:
        os.fsync(output_directory_fd)
    except OSError as exc:
        cleanup_error = cleanup_error or exc
    if cleanup_error is not None:
        raise ReleaseBuildError(
            "Could not clean the release staging directory."
        ) from cleanup_error


def _stage_artifact(
    directory_fd: int, final_name: str, payload: bytes
) -> tuple[str, int, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    temporary_name = ""
    for _ in range(16):
        temporary_name = f".{final_name}.tmp-{secrets.token_hex(16)}"
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        except OSError as exc:
            raise ReleaseBuildError("Could not create a release temporary file.") from exc
        break
    else:
        raise ReleaseBuildError("Could not allocate a unique release temporary file.")

    succeeded = False
    try:
        _require_private_regular_file(descriptor)
        temporary = os.fdopen(descriptor, "wb")
        descriptor = -1
        with temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
            entry = _require_private_regular_file(temporary.fileno())
        succeeded = True
        return temporary_name, entry.st_dev, entry.st_ino
    except ReleaseBuildError:
        raise
    except OSError as exc:
        raise ReleaseBuildError("Could not write a release temporary file.") from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not succeeded:
            _unlink_staging_entry(directory_fd, temporary_name)


def _require_staged_name(
    staging_fd: int, staged: tuple[str, int, int]
) -> None:
    name, expected_device, expected_inode = staged
    try:
        entry = os.stat(name, dir_fd=staging_fd, follow_symlinks=False)
    except OSError as exc:
        raise ReleaseBuildError("Could not inspect a staged release artifact.") from exc
    if (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_nlink != 1
        or entry.st_uid != os.geteuid()
        or entry.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        or (entry.st_dev, entry.st_ino) != (expected_device, expected_inode)
    ):
        raise ReleaseBuildError("A staged release artifact changed during the build.")


def _verify_published_artifact(
    directory_fd: int,
    name: str,
    expected_identity: tuple[int, int],
    expected_digest: bytes,
) -> None:
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise ReleaseBuildError("Could not reopen a published release artifact.") from exc
    try:
        entry = os.fstat(descriptor)
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_nlink != 1
            or (entry.st_dev, entry.st_ino) != expected_identity
        ):
            raise ReleaseBuildError("Published release artifact is unsafe.")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        if not secrets.compare_digest(digest.digest(), expected_digest):
            raise ReleaseBuildError("Published release artifact failed verification.")
    except ReleaseBuildError:
        raise
    except OSError as exc:
        raise ReleaseBuildError("Could not verify a published release artifact.") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _publish_artifacts(
    output_dir: Path, artifacts: tuple[tuple[str, bytes], ...]
) -> Path:
    resolved_output_dir, directory_fd, output_entry = _open_output_directory(output_dir)
    staging_name = ""
    staging_fd = -1
    staging_identity = (0, 0)
    staged: dict[str, tuple[str, int, int]] = {}
    try:
        _checked_output_directory_metadata(directory_fd)
        original_states = {
            name: _destination_state(directory_fd, name) for name, _ in artifacts
        }
        staging_name, staging_fd, staging_identity = _create_staging_directory(
            directory_fd, output_entry
        )
        for name, payload in artifacts:
            staged[name] = _stage_artifact(staging_fd, name, payload)
        os.fsync(staging_fd)

        _checked_output_directory_metadata(directory_fd)
        for name, _ in artifacts:
            if _destination_state(directory_fd, name) != original_states[name]:
                raise ReleaseBuildError(
                    "A release artifact destination changed during the build."
                )
        for name, _ in artifacts:
            _require_staged_name(staging_fd, staged[name])

        for name, _ in artifacts:
            staged_artifact = staged[name]
            _require_staged_name(staging_fd, staged_artifact)
            os.replace(
                staged_artifact[0],
                name,
                src_dir_fd=staging_fd,
                dst_dir_fd=directory_fd,
            )
        os.fsync(directory_fd)
        for name, payload in artifacts:
            staged_artifact = staged[name]
            _verify_published_artifact(
                directory_fd,
                name,
                (staged_artifact[1], staged_artifact[2]),
                hashlib.sha256(payload).digest(),
            )
        os.fsync(directory_fd)
    except ReleaseBuildError:
        raise
    except (OSError, TypeError, NotImplementedError) as exc:
        raise ReleaseBuildError("Could not publish release artifacts safely.") from exc
    finally:
        try:
            if staging_fd >= 0:
                _cleanup_staging_directory(
                    directory_fd,
                    staging_name,
                    staging_fd,
                    staging_identity,
                    tuple(staged_artifact[0] for staged_artifact in staged.values()),
                )
        finally:
            try:
                os.close(directory_fd)
            except OSError:
                pass
    return resolved_output_dir


def build_release(output_dir: Path) -> tuple[Path, Path]:
    version = _manifest_version()
    archive_name = f"{ARCHIVE_ROOT}-{version}.tar.gz"
    checksum_name = f"{archive_name}.sha256"
    archive_bytes = _gzip_bytes(_tar_bytes(_release_inputs()))
    digest = hashlib.sha256(archive_bytes).hexdigest()
    checksum_bytes = f"{digest}  {archive_name}\n".encode("ascii")
    resolved_output_dir = _publish_artifacts(
        output_dir,
        (
            (archive_name, archive_bytes),
            (checksum_name, checksum_bytes),
        ),
    )
    return resolved_output_dir / archive_name, resolved_output_dir / checksum_name


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist"),
        help="Directory that receives the archive and detached checksum.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        archive_path, checksum_path = build_release(args.output_dir)
    except ReleaseBuildError as exc:
        print(f"Release build failed: {exc}", file=sys.stderr)
        return 1
    print(f"Built {archive_path.name}")
    print(f"Wrote {checksum_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
