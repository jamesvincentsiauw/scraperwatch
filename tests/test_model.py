from scraperwatch.model import (
    Finding,
    PageTypeSummary,
    Quarantined,
    RunSummary,
    Severity,
    Status,
    Verdict,
)
from scraperwatch.stats.aggregate import RunStats
from scraperwatch.stats.values import EmptyRule


def summary_of(pages: list[list[dict[str, object]]]) -> RunSummary:
    stats = RunStats(EmptyRule(), ("id",))
    for i, items in enumerate(pages):
        stats.add_page(f"https://a.test/{i}", items, status=200)
    return RunSummary.from_stats(stats)


def test_summary_ratios() -> None:
    summary = summary_of(
        [[{"id": 1, "price": 10}, {"id": 2, "price": None}], [{"id": 2, "price": "12"}]]
    )
    page = summary.page_types["default"]
    assert page.completeness("price") == 2 / 3
    assert page.presence("price") == 1
    assert page.completeness("missing") == 0
    assert page.dominant_type("price") == ("number", 0.5)
    assert page.type_share("price", "string") == 0.5
    assert page.unique_items == 2
    assert summary.items == 3
    assert summary.pages == 2
    assert summary.domains == {"a.test": {200: 2}}


def test_page_summary_round_trips() -> None:
    page = summary_of([[{"id": 1, "title": "x"}]]).page_types["default"]
    assert PageTypeSummary.from_dict(page.to_dict()) == page


def test_finding_round_trips() -> None:
    finding = Finding(
        "completeness",
        Severity.QUARANTINE,
        "price empty",
        "default",
        "price",
        0.2,
        0.8,
        1.0,
        ("u",),
    )
    assert Finding.from_dict(finding.to_dict()) == finding


def test_verdict_status_and_summary() -> None:
    warn = Finding("new_field", Severity.WARN, "new field color")
    stop = Finding("volume", Severity.QUARANTINE, "items dropped")
    assert Verdict.status_for(()) is Status.PASS
    assert Verdict.status_for((warn,)) is Status.WARN
    assert Verdict.status_for((warn, stop)) is Status.QUARANTINE

    verdict = Verdict("r1", "shop", Status.QUARANTINE, (warn, stop, warn, warn), False, 10)
    assert verdict.quarantined
    assert verdict.summary == (
        "shop r1: quarantine: new field color; items dropped; new field color (+1 more)"
    )
    error = Quarantined(verdict)
    assert error.verdict is verdict
    assert str(error) == verdict.summary
