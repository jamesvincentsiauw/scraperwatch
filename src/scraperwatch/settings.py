"""Per-target settings and check thresholds."""

from __future__ import annotations

from dataclasses import dataclass, field

from scraperwatch.stats.values import DEFAULT_EMPTY_VALUES


@dataclass(frozen=True)
class Thresholds:
    """Defaults are starting values; the simulation in scripts/simulate.py is what tunes them."""

    k: float = 4.0
    min_share_change: float = 0.10
    min_baseline_runs: int = 3
    volume_drop: float = 0.5
    volume_spike: float = 3.0
    collapse_share: float = 0.05
    collapse_baseline: float = 0.5
    required_floor: float = 0.5
    error_share: float = 0.5
    missing_field_presence: float = 0.9
    new_field_presence: float = 0.5
    stable_type_share: float = 0.95
    changed_type_share: float = 0.8
    min_typed_values: int = 10


@dataclass(frozen=True)
class TargetSettings:
    required: tuple[str, ...] = ()
    key: tuple[str, ...] = ()
    ignored: frozenset[str] = frozenset()
    baseline_runs: int = 10
    min_items: int = 30
    empty_values: tuple[str, ...] = DEFAULT_EMPTY_VALUES
    thresholds: Thresholds = field(default_factory=Thresholds)

    def __post_init__(self) -> None:
        if self.baseline_runs < 1:
            raise ValueError("baseline_runs must be at least 1")
        if self.min_items < 0:
            raise ValueError("min_items must not be negative")
        overlap = set(self.required) & self.ignored
        if overlap:
            raise ValueError(f"fields cannot be both required and ignored: {sorted(overlap)}")
