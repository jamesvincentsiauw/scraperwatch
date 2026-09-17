"""Classifying extracted values: empty or not, and which type family."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

DEFAULT_EMPTY_VALUES = ("N/A", "-")


class EmptyRule:
    """Decides whether an extracted value counts as missing data."""

    def __init__(self, placeholders: Iterable[str] = DEFAULT_EMPTY_VALUES) -> None:
        self._placeholders = frozenset(p.strip().casefold() for p in placeholders)

    def is_empty(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            stripped = value.strip()
            return not stripped or stripped.casefold() in self._placeholders
        if isinstance(value, Mapping | list | tuple | set | frozenset):
            return len(value) == 0  # pyright: ignore[reportUnknownArgumentType]
        return False


def type_family(value: Any) -> str:
    """Groups Python types so that int and float, or list and tuple, compare equal."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list | tuple | set | frozenset):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__
