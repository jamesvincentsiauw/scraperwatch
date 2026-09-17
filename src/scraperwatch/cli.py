"""The scraperwatch command."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import TextIO

from scraperwatch import __version__
from scraperwatch.config import load_config
from scraperwatch.evidence import field_rows
from scraperwatch.model import Status
from scraperwatch.report import render_html
from scraperwatch.store import SQLiteStore, StoredRun, open_store

EXIT_CODES = {Status.PASS: 0, Status.WARN: 1, Status.QUARANTINE: 2}
EXIT_NOT_FOUND = 3


def parse_duration(text: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([dhm])", text.strip())
    if not match:
        raise argparse.ArgumentTypeError(f"expected a duration like 90d, 12h or 30m, got {text!r}")
    amount, unit = int(match[1]), match[2]
    return {
        "d": timedelta(days=amount),
        "h": timedelta(hours=amount),
        "m": timedelta(minutes=amount),
    }[unit]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scraperwatch", description="Inspect scraper runs recorded by scraperwatch."
    )
    parser.add_argument("--version", action="version", version=f"scraperwatch {__version__}")
    parser.add_argument("--config", type=Path, help="path to scraperwatch.toml")
    parser.add_argument("--store", help="store to use instead of the configured one")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("runs", help="list recent runs")
    p.add_argument("target", nargs="?")
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser(
        "show", help="print a run's findings; exit code 0 pass, 1 warn, 2 quarantine"
    )
    p.add_argument("run_id")

    p = sub.add_parser("report", help="write a run's HTML report")
    p.add_argument("run_id")
    p.add_argument("-o", "--output", type=Path)

    p = sub.add_parser("accept", help="add a run to its target's baseline")
    p.add_argument("run_id")

    p = sub.add_parser("baseline", help="show the baseline a target's next run is compared with")
    p.add_argument("target")

    p = sub.add_parser("prune", help="delete runs older than a given age")
    p.add_argument("--older-than", type=parse_duration, required=True, metavar="AGE")
    return parser


def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None) -> int:
    out = stdout or sys.stdout
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    store = open_store(args.store or config.store)
    command: str = args.command

    if command == "runs":
        return _runs(store, args.target, args.limit, out)
    if command == "baseline":
        return _baseline(store, config.target_settings(args.target).baseline_runs, args.target, out)
    if command == "prune":
        removed = store.prune(args.older_than)
        print(f"Deleted {removed} runs.", file=out)
        return 0

    run = store.get(args.run_id)
    if run is None:
        print(f"No run with id {args.run_id}.", file=sys.stderr)
        return EXIT_NOT_FOUND
    if command == "show":
        _show(run, out)
        return EXIT_CODES[run.record.status]
    if command == "accept":
        store.accept(run.record.id)
        print(f"Run {run.record.id} is now part of the {run.record.target} baseline.", file=out)
        return 0
    if command == "report":
        target = run.record.target
        baseline = store.baseline(
            target, config.target_settings(target).baseline_runs, before=run.record.started_at
        )
        path: Path = args.output or Path(f"scraperwatch-{run.record.id}.html")
        path.write_text(render_html(run, baseline), encoding="utf-8")
        print(f"Wrote {path}", file=out)
        return 0
    raise AssertionError(f"unhandled command {command}")


def _runs(store: SQLiteStore, target: str | None, limit: int, out: TextIO) -> int:
    records = store.runs(target, limit)
    if not records:
        print("No runs recorded yet.", file=out)
        return 0
    print(
        f"{'RUN':<24} {'TARGET':<20} {'FINISHED (UTC)':<17} {'STATUS':<11} {'PAGES':>6} {'ITEMS':>8}",
        file=out,
    )
    for r in records:
        status = r.status.value + ("*" if r.accepted else "")
        print(
            f"{r.id:<24} {r.target[:20]:<20} {r.finished_at:%Y-%m-%d %H:%M} {status:<11} {r.pages:>6} {r.items:>8}",
            file=out,
        )
    if any(r.accepted for r in records):
        print("* accepted into the baseline", file=out)
    return 0


def _show(run: StoredRun, out: TextIO) -> None:
    r = run.record
    print(f"{r.target} {r.id}: {r.status.value}", file=out)
    print(f"{r.pages} pages, {r.items} items, baseline of {r.baseline_runs} runs", file=out)
    if r.warming_up:
        print("Warming up: only absolute rules were checked.", file=out)
    for f in run.findings:
        where = " / ".join(p for p in (f.page_type, f.field) if p)
        print(f"\n[{f.severity.value}] {f.check}{f' ({where})' if where else ''}", file=out)
        print(f"  {f.message}", file=out)
        for url in f.sample_urls:
            print(f"  - {url}", file=out)


def _baseline(store: SQLiteStore, limit: int, target: str, out: TextIO) -> int:
    history = store.baseline(target, limit)
    if not history:
        print(f"No passing or accepted runs for {target} yet.", file=out)
        return 0
    print(f"{target}: baseline of {len(history)} runs", file=out)
    print(f"{'PAGE TYPE':<14} {'FIELD':<24} {'USUALLY FILLED':>15} {'RANGE':>12}", file=out)
    for row in field_rows(history[0], history):
        if row.baseline_median is None or row.baseline_min is None or row.baseline_max is None:
            continue
        spread = f"{row.baseline_min:.0%}-{row.baseline_max:.0%}"
        print(
            f"{row.page_type[:14]:<14} {row.field[:24]:<24} {row.baseline_median:>15.0%} {spread:>12}",
            file=out,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
