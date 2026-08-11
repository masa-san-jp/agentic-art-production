"""Repository configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .yaml_io import load_yaml


def load_config(root: Path, name: str) -> dict[str, Any]:
    value = load_yaml(root / "config" / name)
    if not isinstance(value, dict):
        raise TypeError(f"config/{name} must contain a YAML object")
    return value
