#!/usr/bin/env python3
"""Verify a Hermes T3 Control detached SHA-256 checksum portably."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import re
import sys
from pathlib import Path


CHECKSUM_RE = re.compile(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)\n?")
MAX_CHECKSUM_BYTES = 1024
READ_CHUNK_BYTES = 1024 * 1024


class ReleaseVerificationError(Exception):
    """A sanitized checksum-verification failure."""


def _parse_checksum(checksum_path: Path) -> tuple[str, str]:
    try:
        raw = checksum_path.read_bytes()
    except OSError as exc:
        raise ReleaseVerificationError("Checksum file could not be read.") from exc
    if len(raw) > MAX_CHECKSUM_BYTES:
        raise ReleaseVerificationError("Checksum file is oversized.")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ReleaseVerificationError("Checksum file is not portable ASCII.") from exc
    match = CHECKSUM_RE.fullmatch(text)
    if match is None:
        raise ReleaseVerificationError("Checksum file has an invalid format.")
    expected, archive_name = match.groups()
    if checksum_path.name != f"{archive_name}.sha256":
        raise ReleaseVerificationError("Checksum filename does not match its archive.")
    return expected, archive_name


def verify_release(checksum_path: Path) -> Path:
    expected, archive_name = _parse_checksum(checksum_path)
    archive_path = checksum_path.parent / archive_name
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ReleaseVerificationError("Named release archive is missing or unsafe.")
    digest = hashlib.sha256()
    try:
        with archive_path.open("rb") as archive:
            while chunk := archive.read(READ_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseVerificationError("Named release archive could not be read.") from exc
    if not hmac.compare_digest(digest.hexdigest(), expected):
        raise ReleaseVerificationError("Release archive checksum does not match.")
    return archive_path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checksum", type=Path, help="Detached .sha256 file to verify.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        archive_path = verify_release(args.checksum)
    except ReleaseVerificationError as exc:
        print(f"Checksum verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Checksum OK: {archive_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
