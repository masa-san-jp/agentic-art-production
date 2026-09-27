"""Resolve immutable repository provenance in checkouts and Git archives."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ARCHIVE_COMMIT_FILE = ".archive-commit"
COMMIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


class CommitResolutionError(RuntimeError):
    """Raised when neither Git nor archive provenance provides a commit."""


def _git_commit(repository: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CommitResolutionError(f"git rev-parse HEAD failed: {exc}") from exc
    value = completed.stdout.strip()
    if not COMMIT_SHA_PATTERN.fullmatch(value):
        raise CommitResolutionError("git rev-parse HEAD did not return a 40-character lowercase SHA")
    return value


def _archive_commit(repository: Path) -> str:
    marker = repository / ARCHIVE_COMMIT_FILE
    if marker.is_symlink() or not marker.is_file():
        raise CommitResolutionError(
            "repository is not a Git checkout and .archive-commit is missing"
        )
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise CommitResolutionError(".archive-commit cannot be read") from exc
    if not COMMIT_SHA_PATTERN.fullmatch(value):
        raise CommitResolutionError(
            ".archive-commit must contain a 40-character lowercase SHA"
        )
    return value


def resolve_commit(repository: Path) -> str:
    """Resolve ``HEAD`` first, then the commit substituted into an archive.

    A malformed or missing fallback is reported to the caller; it is never
    replaced by an empty, synthetic, or otherwise guessed value.
    """

    repository = Path(repository)
    try:
        return _git_commit(repository)
    except CommitResolutionError as git_error:
        try:
            return _archive_commit(repository)
        except CommitResolutionError as archive_error:
            raise CommitResolutionError(
                f"could not resolve commit ({git_error}; {archive_error})"
            ) from archive_error
