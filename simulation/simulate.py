"""Runs 30 simulated days per scenario and seed, and reports what scraperwatch caught.

Usage: uv run python -m simulation.simulate [--seeds 10] [--output docs/evaluation.md]
"""

from __future__ import annotations

import argparse
import random
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scraperwatch import Config, Status, __version__
from scraperwatch.run import Run
from scraperwatch.store import open_store
from simulation.fakeshop import Fault, Shop
from simulation.scraper import parse_listing

DAYS = 30
HEALTHY_PREFIX = 10


@dataclass(frozen=True)
class Scenario:
    number: int
    name: str
    fault: Fault
    expected_checks: tuple[str, ...]


SCENARIOS = (
    Scenario(
        1,
        "Price selector renamed on 60% of cards",
        Fault.PRICE_CLASS_RENAMED,
        ("completeness", "required_field"),
    ),
    Scenario(
        2, "One of five categories returns an empty grid", Fault.EMPTY_CATEGORY, ("empty_pages",)
    ),
    Scenario(3, "New card template on 40% of pages", Fault.NEW_TEMPLATE, ("empty_pages",)),
    Scenario(
        4, "Challenge page with HTTP 200 on 30% of requests", Fault.CHALLENGE_PAGE, ("empty_pages",)
    ),
    Scenario(
        5, "Only half the listing pages are reachable", Fault.HALF_THE_PAGES, ("volume_drop",)
    ),
    Scenario(
        6, "Listings shift by 6 per page during the crawl", Fault.PAGINATION_SHIFT, ("duplicates",)
    ),
)


@dataclass
class Tally:
    incident_days: int = 0
    detected: int = 0
    quarantined: int = 0
    healthy_days: int = 0
    false_alarms: int = 0
    false_alarm_checks: Counter[str] = field(default_factory=Counter[str])
    missed_checks: Counter[str] = field(default_factory=Counter[str])


def simulate(scenario: Scenario, seed: int, workdir: Path) -> Tally:
    rng = random.Random(seed)
    shop = Shop(seed)
    start = rng.randrange(HEALTHY_PREFIX, DAYS - 3)
    incident = set(range(start, start + rng.randint(1, 3)))
    config = Config(store=str(workdir / f"s{scenario.number}-{seed}.db"), evidence=False)
    store = open_store(config.store)
    tally = Tally()
    day0 = datetime(2026, 1, 1, tzinfo=UTC)

    for day in range(DAYS):
        shop.advance_day(day)
        fault = scenario.fault if day in incident else Fault.NONE
        now = day0 + timedelta(days=day)
        run = Run(
            "fakeshop",
            config=config,
            store=store,
            required=["title", "price"],
            key=["id"],
            clock=lambda now=now: now,
        )
        page_rng = random.Random(seed * 100_000 + day)
        for cat, page in shop.pages(fault):
            status, html = shop.render(cat, page, fault, page_rng)
            url = f"https://fakeshop.test/{cat}?page={page}"
            run.record(url, parse_listing(html), status=status, body=html, page_type="listing")
        verdict = run.finish()

        checks = {f.check for f in verdict.findings}
        if day in incident:
            tally.incident_days += 1
            if checks & set(scenario.expected_checks):
                tally.detected += 1
            else:
                tally.missed_checks.update(checks or {"(none)"})
            if verdict.status is Status.QUARANTINE:
                tally.quarantined += 1
        else:
            tally.healthy_days += 1
            if verdict.status is not Status.PASS:
                tally.false_alarms += 1
                tally.false_alarm_checks.update(checks)
    return tally


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rows: list[tuple[Scenario, Tally]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for scenario in SCENARIOS:
            total = Tally()
            for seed in range(args.seeds):
                t = simulate(scenario, seed, Path(tmp))
                total.incident_days += t.incident_days
                total.detected += t.detected
                total.quarantined += t.quarantined
                total.healthy_days += t.healthy_days
                total.false_alarms += t.false_alarms
                total.false_alarm_checks.update(t.false_alarm_checks)
                total.missed_checks.update(t.missed_checks)
            rows.append((scenario, total))
            print(
                f"scenario {scenario.number}: detected {total.detected}/{total.incident_days}, "
                f"quarantined {total.quarantined}, false alarms {total.false_alarms}/{total.healthy_days}"
            )

    report = render(rows, args.seeds)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
        print(f"wrote {args.output}")
    else:
        print(report)


def render(rows: list[tuple[Scenario, Tally]], seeds: int) -> str:
    lines = [
        "# Evaluation",
        "",
        f"Generated by `python -m simulation.simulate --seeds {seeds}` with scraperwatch {__version__}.",
        "",
        f"Each scenario runs {DAYS} simulated days for each of {seeds} seeds (0 to {seeds - 1}). "
        f"The first {HEALTHY_PREFIX} days are healthy, then one incident lasts 1 to 3 days at a random point. "
        "Healthy days include normal noise: listings added and removed, missing ratings, optional discounts, "
        "and category sizes that change from day to day. The scraper is configured with "
        '`required=["title", "price"]` and `key=["id"]`.',
        "",
        "Detected means one of the expected checks fired on an incident day, at any severity. "
        "Quarantined means the run's verdict was quarantine. "
        "A false alarm is a warn or quarantine verdict on a healthy day, including the first days while the baseline warms up.",
        "",
        "| # | Scenario | Expected checks | Detected | Quarantined | False alarms (healthy days) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for s, t in rows:
        lines.append(
            f"| {s.number} | {s.name} | {', '.join(f'`{c}`' for c in s.expected_checks)} "
            f"| {t.detected}/{t.incident_days} "
            f"| {t.quarantined}/{t.incident_days} | {t.false_alarms}/{t.healthy_days} |"
        )
    total_incidents = sum(t.incident_days for _, t in rows)
    total_detected = sum(t.detected for _, t in rows)
    total_healthy = sum(t.healthy_days for _, t in rows)
    total_false = sum(t.false_alarms for _, t in rows)
    lines += [
        "",
        f"Overall: {total_detected}/{total_incidents} incident days detected "
        f"({total_detected / total_incidents:.0%}), {total_false} false alarms in {total_healthy} healthy days "
        f"({total_false / total_healthy * 30:.2f} per 30 runs).",
    ]
    notes = [(s, t) for s, t in rows if t.false_alarm_checks or t.missed_checks]
    if notes:
        lines += ["", "## Details", ""]
        for s, t in notes:
            if t.false_alarm_checks:
                fa = ", ".join(f"`{k}` x{v}" for k, v in t.false_alarm_checks.most_common())
                lines.append(f"- Scenario {s.number}, checks behind false alarms: {fa}")
            if t.missed_checks:
                mc = ", ".join(f"`{k}` x{v}" for k, v in t.missed_checks.most_common())
                lines.append(f"- Scenario {s.number}, what fired on missed incident days: {mc}")
    lines += [
        "",
        "Scenarios 3 and 4 have dedicated checks planned for 0.2 (template fingerprints and block detection). "
        "In 0.1 they are caught through their effect: pages that yield no items.",
        "",
        "Scenario 6 is detected as a warning, by design: unique items fall by about a quarter, "
        "which is under the volume threshold that would make it a quarantine.",
        "",
        "These numbers hold for the fake shop's noise levels: up to 5% of listings removed and "
        "5% added per category per day, 1% of prices and 3% of ratings missing. "
        "Real sites can be noisier, so a clean result here does not guarantee zero false alarms elsewhere.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
