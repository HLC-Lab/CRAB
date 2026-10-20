"""The `{var}` placeholder test shared by the config checks."""

from __future__ import annotations

from typing import Any


def has_token(value: Any) -> bool:
    """True if a `{var}` placeholder (substituted later by SbatchMan) is anywhere in value."""
    if isinstance(value, str):
        return "{" in value
    if isinstance(value, dict):
        return any(has_token(v) for v in value.values())
    if isinstance(value, list):
        return any(has_token(v) for v in value)
    return False
