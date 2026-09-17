"""Compare one run with its baseline and turn differences into findings."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from scraperwatch.model import Finding, PageTypeSummary, RunSummary, Severity
from scraperwatch.settings import TargetSettings
from scraperwatch.stats.aggregate import SampleIndex
from scraperwatch.stats.robust import median, robust_spread, wilson_interval


@dataclass(frozen=True)
class Band:
    center: float
    spread: float

    @classmethod
    def of(cls, values: Sequence[float]) -> Band:
        return cls(median(values), robust_spread(values))


@dataclass(frozen=True)
class Evaluation:
    findings: tuple[Finding, ...]
    warming_up: bool
    baseline_runs: int


def pct(x: float) -> str:
    return f"{x:.0%}"


def evaluate(
    run: RunSummary,
    baseline: Sequence[RunSummary],
    settings: TargetSettings,
    samples: SampleIndex | None = None,
) -> Evaluation:
    samples = samples or SampleIndex.empty()
    findings: list[Finding] = []

    if run.items == 0:
        what = "no pages were recorded" if run.pages == 0 else f"{run.pages} pages yielded no items"
        findings.append(
            Finding("no_items", Severity.QUARANTINE, f"The run produced no items: {what}")
        )

    warming = False
    used = 0
    for name, page in run.page_types.items():
        history = [
            b.page_types[name]
            for b in baseline
            if name in b.page_types and b.page_types[name].items >= settings.min_items
        ]
        used = max(used, len(history))
        checker = _PageChecker(name, page, history, settings, samples)
        findings.extend(checker.absolute())
        if len(history) < settings.thresholds.min_baseline_runs:
            warming = True
            continue
        findings.extend(checker.relative())

    order = {Severity.QUARANTINE: 0, Severity.WARN: 1}
    findings.sort(key=lambda f: order[f.severity])
    return Evaluation(tuple(findings), warming, used)


class _PageChecker:
    def __init__(
        self,
        name: str,
        page: PageTypeSummary,
        history: list[PageTypeSummary],
        settings: TargetSettings,
        samples: SampleIndex,
    ) -> None:
        self.name = name
        self.page = page
        self.history = history
        self.s = settings
        self.t = settings.thresholds
        self.samples = samples

    def severity(self, field: str) -> Severity:
        return Severity.QUARANTINE if field in self.s.required else Severity.WARN

    def field_samples(self, field: str) -> tuple[str, ...]:
        return self.samples.empty_fields.get((self.name, field), ())

    def label(self) -> str:
        return "items" if self.name == "default" else f"{self.name} items"

    # Rules that need no history.

    def absolute(self) -> list[Finding]:
        out: list[Finding] = []
        page = self.page
        if page.pages and page.error_pages / page.pages > self.t.error_share:
            share = page.error_pages / page.pages
            out.append(
                Finding(
                    "fetch_errors",
                    Severity.QUARANTINE,
                    f"{pct(share)} of {self.name} pages failed to fetch or parse",
                    self.name,
                    observed=share,
                    expected_high=self.t.error_share,
                    sample_urls=self.samples.errors.get(self.name, ()),
                )
            )
        if page.items:
            for field in self.s.required:
                share = page.completeness(field)
                if share < self.t.required_floor:
                    out.append(
                        Finding(
                            "required_field",
                            Severity.QUARANTINE,
                            f"Required field {field} is filled in only {pct(share)} of {self.label()}",
                            self.name,
                            field,
                            observed=share,
                            expected_low=self.t.required_floor,
                            sample_urls=self.field_samples(field),
                        )
                    )
        return out

    # Rules that compare with the baseline.

    def relative(self) -> list[Finding]:
        return [
            *self.fields(),
            *self.new_fields(),
            *self.types(),
            *self.volume(),
            *self.empty_pages(),
            *self.duplicates(),
        ]

    def share_floor(self, band: Band) -> float:
        return band.center - max(self.t.k * band.spread, self.t.min_share_change)

    def share_ceiling(self, band: Band) -> float:
        return band.center + max(self.t.k * band.spread, self.t.min_share_change)

    def count_is_low(self, value: int, band: Band) -> bool:
        return (
            value < band.center * self.t.volume_drop
            and value < band.center - self.t.k * band.spread
        )

    def fields(self) -> list[Finding]:
        out: list[Finding] = []
        page = self.page
        if not page.items:
            return out
        known = {f for h in self.history for f in h.fields} | set(self.s.required)
        for field in sorted(known - self.s.ignored):
            if field in self.s.required and page.completeness(field) < self.t.required_floor:
                continue  # already reported by the absolute rule
            presence = Band.of([h.presence(field) for h in self.history])
            if presence.center >= self.t.missing_field_presence and page.presence(field) == 0:
                out.append(
                    Finding(
                        "missing_field",
                        self.severity(field),
                        f"Field {field} disappeared from {self.label()}; it was present in {pct(presence.center)} before",
                        self.name,
                        field,
                        observed=0.0,
                        expected_low=presence.center,
                    )
                )
                continue

            band = Band.of([h.completeness(field) for h in self.history])
            share = page.completeness(field)
            non_empty = page.fields[field].non_empty if field in page.fields else 0
            floor = self.share_floor(band)
            if share <= self.t.collapse_share and band.center >= self.t.collapse_baseline:
                check = "field_collapse"
            elif wilson_interval(non_empty, page.items)[1] < floor:
                check = "completeness"
            else:
                continue
            out.append(
                Finding(
                    check,
                    self.severity(field),
                    f"{field} is filled in {pct(share)} of {self.label()}, usually {pct(band.center)}",
                    self.name,
                    field,
                    observed=share,
                    expected_low=max(0.0, floor),
                    expected_high=1.0,
                    sample_urls=self.field_samples(field),
                )
            )
        return out

    def new_fields(self) -> list[Finding]:
        known = {f for h in self.history for f in h.fields}
        out: list[Finding] = []
        for field in sorted(set(self.page.fields) - known - self.s.ignored):
            share = self.page.presence(field)
            if share > self.t.new_field_presence:
                out.append(
                    Finding(
                        "new_field",
                        Severity.WARN,
                        f"New field {field} appears in {pct(share)} of {self.label()}",
                        self.name,
                        field,
                        observed=share,
                    )
                )
        return out

    def types(self) -> list[Finding]:
        out: list[Finding] = []
        for field in sorted(set(self.page.fields) - self.s.ignored):
            if self.page.fields[field].non_empty < self.t.min_typed_values:
                continue
            families: Counter[str] = Counter()
            for h in self.history:
                dominant = h.dominant_type(field)
                if dominant:
                    families[dominant[0]] += 1
            if not families:
                continue
            family = families.most_common(1)[0][0]
            usual = median([h.type_share(field, family) for h in self.history])
            now = self.page.type_share(field, family)
            if usual >= self.t.stable_type_share and now < self.t.changed_type_share:
                current = self.page.dominant_type(field)
                became = f", now mostly {current[0]}" if current else ""
                out.append(
                    Finding(
                        "type_change",
                        self.severity(field),
                        f"{field} is a {family} in {pct(now)} of {self.label()}, usually {pct(usual)}{became}",
                        self.name,
                        field,
                        observed=now,
                        expected_low=self.t.changed_type_share,
                    )
                )
        return out

    def volume(self) -> list[Finding]:
        out: list[Finding] = []
        items = Band.of([h.items for h in self.history])
        pages = Band.of([h.pages for h in self.history])
        if self.count_is_low(self.page.items, items):
            out.append(
                Finding(
                    "volume_drop",
                    Severity.QUARANTINE,
                    f"{self.page.items} {self.label()} scraped, usually about {items.center:.0f}",
                    self.name,
                    observed=self.page.items,
                    expected_low=items.center * self.t.volume_drop,
                )
            )
        elif self.page.items > items.center * self.t.volume_spike:
            out.append(
                Finding(
                    "volume_spike",
                    Severity.WARN,
                    f"{self.page.items} {self.label()} scraped, more than {self.t.volume_spike:g}x the usual {items.center:.0f}; check for a repeated crawl",
                    self.name,
                    observed=self.page.items,
                    expected_high=items.center * self.t.volume_spike,
                )
            )
        if self.count_is_low(self.page.pages, pages):
            out.append(
                Finding(
                    "page_coverage",
                    Severity.WARN,
                    f"{self.page.pages} {self.name} pages recorded, usually about {pages.center:.0f}",
                    self.name,
                    observed=self.page.pages,
                    expected_low=pages.center * self.t.volume_drop,
                )
            )
        return out

    def empty_pages(self) -> list[Finding]:
        page = self.page
        if not page.pages:
            return []
        band = Band.of([h.empty_pages / h.pages if h.pages else 0.0 for h in self.history])
        ceiling = self.share_ceiling(band)
        share = page.empty_pages / page.pages
        if wilson_interval(page.empty_pages, page.pages)[0] <= ceiling:
            return []
        return [
            Finding(
                "empty_pages",
                Severity.QUARANTINE,
                f"{pct(share)} of {self.name} pages yielded no items, usually {pct(band.center)}",
                self.name,
                observed=share,
                expected_high=min(1.0, ceiling),
                sample_urls=self.samples.empty_pages.get(self.name, ()),
            )
        ]

    def duplicates(self) -> list[Finding]:
        page = self.page
        if not self.s.key or not page.keyed_items:
            return []
        band = Band.of([h.duplicate_share() for h in self.history])
        ceiling = self.share_ceiling(band)
        dups = max(0, page.duplicate_items)
        share = dups / page.keyed_items
        if wilson_interval(dups, page.keyed_items)[0] <= ceiling:
            return []
        unique = Band.of([h.unique_items for h in self.history])
        shifted = self.count_is_low(page.unique_items, unique)
        message = f"{pct(share)} of {self.label()} repeat a key seen earlier in this run, usually {pct(band.center)}"
        if shifted:
            message += (
                f"; unique items fell to {page.unique_items} from about {unique.center:.0f}, "
                "which suggests pagination shifted and some items were skipped"
            )
        return [
            Finding(
                "duplicates",
                Severity.QUARANTINE if shifted else Severity.WARN,
                message,
                self.name,
                observed=share,
                expected_high=min(1.0, ceiling),
                sample_urls=self.samples.duplicates.get(self.name, ()),
            )
        ]
