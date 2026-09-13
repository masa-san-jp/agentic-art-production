"""Canonicalize explicit external paths without admitting caller symlinks."""
from __future__ import annotations

from pathlib import Path


# macOS presents the system temporary directory lexically as /var.  The
# canonical path is /private/var; accepting this OS-owned alias keeps the
# external-store guard portable without allowing arbitrary symlinks.
SYSTEM_ALIASES = frozenset({Path("/var"), Path("/tmp")})


def _has_unapproved_symlink(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink() and current not in SYSTEM_ALIASES:
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def external_path(value: object) -> Path:
    """Return a canonical absolute path while rejecting caller symlinks."""
    if not isinstance(value, (str, Path)) or not str(value) or "\x00" in str(value):
        raise ValueError("STORE_PATH_INVALID")
    path = Path(value).expanduser()
    if not path.is_absolute() or _has_unapproved_symlink(path):
        raise ValueError("STORE_PATH_INVALID")
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("STORE_PATH_INVALID") from exc
    if resolved.is_symlink():
        raise ValueError("STORE_PATH_INVALID")
    return resolved
