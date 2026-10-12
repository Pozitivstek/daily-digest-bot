"""Множественный выбор: включить/выключить пункт, сохранив канонический порядок."""
from typing import Iterable


def toggle(selected: Iterable[str], item: str, allowed: Iterable[str]) -> tuple[str, ...]:
    allowed = tuple(allowed)
    chosen = set(selected)
    if item in allowed:
        chosen.symmetric_difference_update({item})
    return tuple(value for value in allowed if value in chosen)
