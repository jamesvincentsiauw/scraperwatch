"""Streaming aggregates for one run. Item values are counted, never kept."""

from __future__ import annotations

import dataclasses
import threading
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from scraperwatch.stats.distinct import DistinctCounter, key_hash
from scraperwatch.stats.values import EmptyRule, type_family

MAX_SAMPLES = 5
DEFAULT_PAGE_TYPE = "default"


class Samples:
    """Keeps the first few distinct URLs for a finding's evidence."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def add(self, url: str) -> None:
        if len(self.urls) < MAX_SAMPLES and url not in self.urls:
            self.urls.append(url)


@dataclass
class FieldCounts:
    present: int = 0
    non_empty: int = 0
    types: Counter[str] = field(default_factory=Counter[str])
    empty_samples: Samples = field(default_factory=Samples)


@dataclass
class PageTypeStats:
    pages: int = 0
    items: int = 0
    empty_pages: int = 0
    error_pages: int = 0
    keyed_items: int = 0
    fields: dict[str, FieldCounts] = field(default_factory=dict[str, FieldCounts])
    empty_page_samples: Samples = field(default_factory=Samples)
    error_samples: Samples = field(default_factory=Samples)
    duplicate_samples: Samples = field(default_factory=Samples)
    distinct: DistinctCounter = field(default_factory=DistinctCounter)

    def unique_items(self) -> int:
        return self.distinct.distinct() + (self.items - self.keyed_items)

    def duplicate_items(self) -> int:
        return self.keyed_items - self.distinct.distinct()


@dataclass(frozen=True)
class SampleIndex:
    """Sample URLs per page type, handed to the checks for evidence."""

    empty_pages: dict[str, tuple[str, ...]]
    errors: dict[str, tuple[str, ...]]
    duplicates: dict[str, tuple[str, ...]]
    empty_fields: dict[tuple[str, str], tuple[str, ...]]

    @classmethod
    def empty(cls) -> SampleIndex:
        return cls({}, {}, {}, {})


@dataclass
class DomainStats:
    pages: int = 0
    statuses: Counter[int] = field(default_factory=Counter[int])


def as_mapping(item: Any) -> Mapping[str, Any]:
    """Accepts dicts, dataclasses and objects with a Pydantic-style model_dump()."""
    if isinstance(item, Mapping):
        return item  # pyright: ignore[reportUnknownVariableType]
    if dataclasses.is_dataclass(item) and not isinstance(item, type):
        return dataclasses.asdict(item)
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        result = dump()
        if isinstance(result, Mapping):
            return result  # pyright: ignore[reportUnknownVariableType]
    raise TypeError(
        f"items must be mappings, dataclasses or Pydantic models, got {type(item).__name__}"
    )


class RunStats:
    def __init__(self, empty_rule: EmptyRule, key_fields: Sequence[str] = ()) -> None:
        self._empty = empty_rule
        self._key_fields = tuple(key_fields)
        self._lock = threading.Lock()
        self.page_types: dict[str, PageTypeStats] = {}
        self.domains: dict[str, DomainStats] = {}

    @property
    def key_fields(self) -> tuple[str, ...]:
        return self._key_fields

    def add_page(
        self,
        url: str,
        items: Iterable[Any],
        status: int | None = None,
        error: str | None = None,
        page_type: str | None = None,
    ) -> None:
        # Normalize outside the lock so a slow model_dump() does not block other threads.
        rows = [as_mapping(item) for item in items]
        domain = urlsplit(url).hostname or ""
        with self._lock:
            stats = self.page_types.setdefault(page_type or DEFAULT_PAGE_TYPE, PageTypeStats())
            stats.pages += 1
            stats.items += len(rows)
            if error is not None:
                stats.error_pages += 1
                stats.error_samples.add(url)
            elif not rows:
                stats.empty_pages += 1
                stats.empty_page_samples.add(url)

            for row in rows:
                self._add_item(stats, row, url)

            dom = self.domains.setdefault(domain, DomainStats())
            dom.pages += 1
            if status is not None:
                dom.statuses[status] += 1

    def _add_item(self, stats: PageTypeStats, row: Mapping[str, Any], url: str) -> None:
        for name, value in row.items():
            counts = stats.fields.setdefault(name, FieldCounts())
            counts.present += 1
            if self._empty.is_empty(value):
                counts.empty_samples.add(url)
            else:
                counts.non_empty += 1
                counts.types[type_family(value)] += 1
        # A field missing from this item is as empty as one set to None.
        for name, counts in stats.fields.items():
            if name not in row:
                counts.empty_samples.add(url)

        if self._key_fields:
            values = [row.get(k) for k in self._key_fields]
            if all(self._empty.is_empty(v) for v in values):
                return
            stats.keyed_items += 1
            if stats.distinct.add(key_hash(values)):
                stats.duplicate_samples.add(url)

    def samples(self) -> SampleIndex:
        with self._lock:
            return SampleIndex(
                empty_pages={
                    n: tuple(s.empty_page_samples.urls) for n, s in self.page_types.items()
                },
                errors={n: tuple(s.error_samples.urls) for n, s in self.page_types.items()},
                duplicates={n: tuple(s.duplicate_samples.urls) for n, s in self.page_types.items()},
                empty_fields={
                    (n, f): tuple(c.empty_samples.urls)
                    for n, s in self.page_types.items()
                    for f, c in s.fields.items()
                },
            )

    @property
    def total_items(self) -> int:
        return sum(s.items for s in self.page_types.values())

    @property
    def total_pages(self) -> int:
        return sum(s.pages for s in self.page_types.values())
