import json
from datetime import UTC, datetime
from pathlib import Path

from scraperwatch.evidence import field_rows, write_bundle
from scraperwatch.model import Finding, RunSummary, Severity, Status
from scraperwatch.report import render_html
from scraperwatch.stats.aggregate import RunStats
from scraperwatch.stats.values import EmptyRule
from scraperwatch.store import RunRecord, StoredRun


def summary(price_fill: int) -> RunSummary:
    stats = RunStats(EmptyRule())
    items = [{"title": "t", "price": 1 if i < price_fill else None} for i in range(10)]
    stats.add_page("https://shop.test/1", items, status=200)
    return RunSummary.from_stats(stats)


def stored(
    target: str = "shop",
    status: Status = Status.QUARANTINE,
    accepted: bool = False,
    warming_up: bool = False,
    baseline_runs: int = 5,
) -> StoredRun:
    finding = Finding(
        "completeness",
        Severity.QUARANTINE,
        'price is filled in 20% of items, usually 100% <script>alert("x")</script>',
        "default",
        "price",
        0.2,
        0.9,
        1.0,
        ("https://shop.test/1?q=<b>",),
    )
    now = datetime(2026, 9, 17, 3, 0, tzinfo=UTC)
    record = RunRecord(
        "20260917T030000Z-abc123",
        target,
        now,
        now,
        status,
        accepted,
        warming_up,
        baseline_runs,
        1,
        10,
        "0.1.0",
    )
    findings = (finding,) if status is not Status.PASS else ()
    return StoredRun(record, summary(2), findings)


def test_field_rows_compare_with_baseline() -> None:
    rows = field_rows(summary(2), [summary(10), summary(9)])
    price = next(r for r in rows if r.field == "price")
    assert price.completeness == 0.2
    assert price.baseline_median == 0.95
    assert (price.baseline_min, price.baseline_max) == (0.9, 1.0)
    assert price.main_type == "number"


def test_bundle_files(tmp_path: Path) -> None:
    directory = write_bundle(tmp_path, stored(), [summary(10)], html=True)
    assert directory == tmp_path / "shop" / "20260917T030000Z-abc123"

    doc = json.loads((directory / "summary.json").read_text())
    assert doc["status"] == "quarantine"
    assert doc["findings"][0]["field"] == "price"

    fields = json.loads((directory / "fields.json").read_text())
    assert {f["field"] for f in fields} == {"title", "price"}

    samples = json.loads((directory / "samples.json").read_text())
    assert samples == [
        {
            "check": "completeness",
            "page_type": "default",
            "field": "price",
            "urls": ["https://shop.test/1?q=<b>"],
        }
    ]
    assert (directory / "report.html").is_file()


def test_bundle_without_html(tmp_path: Path) -> None:
    directory = write_bundle(tmp_path, stored(), [])
    assert not (directory / "report.html").exists()


def test_target_names_cannot_escape_the_evidence_directory(tmp_path: Path) -> None:
    directory = write_bundle(tmp_path / "ev", stored(target="../../etc"), [])
    assert directory.resolve().is_relative_to((tmp_path / "ev").resolve())


def test_report_escapes_page_derived_text() -> None:
    html = render_html(stored(), [summary(10)])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "?q=&lt;b&gt;" in html
    assert 'class="flagged"' in html
    assert "<link" not in html
    assert "<script" not in html.lower()


def test_report_for_passing_and_warming_up_runs() -> None:
    html = render_html(stored(status=Status.PASS, warming_up=True, baseline_runs=1), [])
    assert "No findings. The run matches its baseline." in html
    assert "only absolute rules were checked" in html
    accepted = render_html(stored(accepted=True), [])
    assert "accepted into the baseline" in accepted
