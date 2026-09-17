"""Plain data passed between the run, the checks and the store."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from scraperwatch.stats.aggregate import RunStats


@dataclass(frozen=True)
class FieldSummary:
    present: int
    non_empty: int
    types: dict[str, int]


@dataclass(frozen=True)
class PageTypeSummary:
    pages: int
    items: int
    empty_pages: int
    error_pages: int
    keyed_items: int
    unique_items: int
    fields: dict[str, FieldSummary]

    def completeness(self, name: str) -> float:
        f = self.fields.get(name)
        if not self.items or f is None:
            return 0.0
        return f.non_empty / self.items

    def presence(self, name: str) -> float:
        f = self.fields.get(name)
        if not self.items or f is None:
            return 0.0
        return f.present / self.items

    def dominant_type(self, name: str) -> tuple[str, float] | None:
        f = self.fields.get(name)
        if f is None or not f.non_empty or not f.types:
            return None
        family, count = max(f.types.items(), key=lambda kv: kv[1])
        return family, count / f.non_empty

    def type_share(self, name: str, family: str) -> float:
        f = self.fields.get(name)
        if f is None or not f.non_empty:
            return 0.0
        return f.types.get(family, 0) / f.non_empty

    def duplicate_share(self) -> float:
        if not self.keyed_items:
            return 0.0
        return self.duplicate_items / self.keyed_items

    @property
    def duplicate_items(self) -> int:
        distinct = self.unique_items - (self.items - self.keyed_items)
        return self.keyed_items - distinct

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "items": self.items,
            "empty_pages": self.empty_pages,
            "error_pages": self.error_pages,
            "keyed_items": self.keyed_items,
            "unique_items": self.unique_items,
            "fields": {
                name: {"present": f.present, "non_empty": f.non_empty, "types": f.types}
                for name, f in self.fields.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PageTypeSummary:
        return cls(
            pages=data["pages"],
            items=data["items"],
            empty_pages=data["empty_pages"],
            error_pages=data["error_pages"],
            keyed_items=data["keyed_items"],
            unique_items=data["unique_items"],
            fields={
                name: FieldSummary(f["present"], f["non_empty"], dict(f["types"]))
                for name, f in data["fields"].items()
            },
        )


@dataclass(frozen=True)
class RunSummary:
    page_types: dict[str, PageTypeSummary]
    domains: dict[str, dict[int, int]] = field(default_factory=dict[str, dict[int, int]])

    @classmethod
    def from_stats(cls, stats: RunStats) -> RunSummary:
        page_types = {
            name: PageTypeSummary(
                pages=s.pages,
                items=s.items,
                empty_pages=s.empty_pages,
                error_pages=s.error_pages,
                keyed_items=s.keyed_items,
                unique_items=s.unique_items(),
                fields={
                    fname: FieldSummary(f.present, f.non_empty, dict(f.types))
                    for fname, f in s.fields.items()
                },
            )
            for name, s in stats.page_types.items()
        }
        domains = {name: dict(d.statuses) for name, d in stats.domains.items()}
        return cls(page_types, domains)

    @property
    def items(self) -> int:
        return sum(p.items for p in self.page_types.values())

    @property
    def pages(self) -> int:
        return sum(p.pages for p in self.page_types.values())


class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    QUARANTINE = "quarantine"


class Severity(StrEnum):
    WARN = "warn"
    QUARANTINE = "quarantine"


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    message: str
    page_type: str | None = None
    field: str | None = None
    observed: float | None = None
    expected_low: float | None = None
    expected_high: float | None = None
    sample_urls: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity.value,
            "message": self.message,
            "page_type": self.page_type,
            "field": self.field,
            "observed": self.observed,
            "expected_low": self.expected_low,
            "expected_high": self.expected_high,
            "sample_urls": list(self.sample_urls),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        return cls(
            check=data["check"],
            severity=Severity(data["severity"]),
            message=data["message"],
            page_type=data.get("page_type"),
            field=data.get("field"),
            observed=data.get("observed"),
            expected_low=data.get("expected_low"),
            expected_high=data.get("expected_high"),
            sample_urls=tuple(data.get("sample_urls", ())),
        )


@dataclass(frozen=True)
class Verdict:
    run_id: str
    target: str
    status: Status
    findings: tuple[Finding, ...]
    warming_up: bool
    baseline_runs: int

    @staticmethod
    def status_for(findings: tuple[Finding, ...]) -> Status:
        if any(f.severity is Severity.QUARANTINE for f in findings):
            return Status.QUARANTINE
        if findings:
            return Status.WARN
        return Status.PASS

    @property
    def quarantined(self) -> bool:
        return self.status is Status.QUARANTINE

    @property
    def summary(self) -> str:
        if not self.findings:
            return f"{self.target} {self.run_id}: pass"
        head = "; ".join(f.message for f in self.findings[:3])
        more = f" (+{len(self.findings) - 3} more)" if len(self.findings) > 3 else ""
        return f"{self.target} {self.run_id}: {self.status.value}: {head}{more}"


class Quarantined(Exception):
    """Raise this to stop a pipeline from publishing a quarantined run."""

    def __init__(self, verdict: Verdict) -> None:
        super().__init__(verdict.summary)
        self.verdict = verdict
