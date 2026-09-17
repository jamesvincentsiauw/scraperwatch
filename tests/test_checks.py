import random
from dataclasses import dataclass, field

from scraperwatch.checks import evaluate
from scraperwatch.model import RunSummary, Severity
from scraperwatch.settings import TargetSettings
from scraperwatch.stats.aggregate import RunStats, SampleIndex
from scraperwatch.stats.values import EmptyRule


@dataclass
class Shape:
    pages: int = 20
    per_page: int = 10
    price_fill: float = 0.97
    price_as_text: float = 0.0
    empty_pages: int = 0
    error_pages: int = 0
    repeat_share: float = 0.0
    drop_field: str | None = None
    extra_field: str | None = None
    seed: int = 0
    stats_kwargs: dict[str, object] = field(default_factory=dict[str, object])


def build(shape: Shape, key: tuple[str, ...] = ("id",)) -> tuple[RunSummary, SampleIndex]:
    rng = random.Random(shape.seed)
    stats = RunStats(EmptyRule(), key)
    next_id = 0
    for p in range(shape.pages):
        url = f"https://shop.test/list/{p}"
        if p < shape.error_pages:
            stats.add_page(url, [], status=500, error="HTTP 500")
            continue
        if p < shape.error_pages + shape.empty_pages:
            stats.add_page(url, [], status=200)
            continue
        items: list[dict[str, object]] = []
        for _ in range(shape.per_page):
            if next_id and rng.random() < shape.repeat_share:
                item_id = rng.randrange(next_id)
            else:
                item_id = next_id
                next_id += 1
            price: object = rng.randint(1, 100) if rng.random() < shape.price_fill else None
            if price is not None and rng.random() < shape.price_as_text:
                price = f"Rp {price}"
            item: dict[str, object] = {"id": item_id, "title": f"item {item_id}", "price": price}
            if shape.drop_field:
                item.pop(shape.drop_field)
            if shape.extra_field:
                item[shape.extra_field] = "x"
            items.append(item)
        stats.add_page(url, items, status=200)
    return RunSummary.from_stats(stats), stats.samples()


def history(n: int = 10) -> list[RunSummary]:
    return [build(Shape(seed=i, pages=20 + i % 3))[0] for i in range(n)]


def checks(findings: object) -> list[str]:
    return [f.check for f in findings]  # type: ignore[attr-defined]


SETTINGS = TargetSettings(key=("id",))


def test_healthy_runs_pass() -> None:
    base = history()
    for seed in range(100, 130):
        run, samples = build(Shape(seed=seed))
        result = evaluate(run, base, SETTINGS, samples)
        assert result.findings == (), result.findings
        assert not result.warming_up
        assert result.baseline_runs == 10


def test_warm_up_skips_relative_checks() -> None:
    run, samples = build(Shape(price_fill=0.2))
    result = evaluate(run, history(2), SETTINGS, samples)
    assert result.warming_up
    assert result.findings == ()


def test_warm_up_still_applies_absolute_rules() -> None:
    settings = TargetSettings(required=("price",), key=("id",))
    run, samples = build(Shape(price_fill=0.2))
    result = evaluate(run, [], settings, samples)
    assert checks(result.findings) == ["required_field"]
    assert result.findings[0].severity is Severity.QUARANTINE
    assert result.findings[0].sample_urls


def test_completeness_drop_is_a_warning_for_optional_fields() -> None:
    run, samples = build(Shape(price_fill=0.6))
    [finding] = evaluate(run, history(), SETTINGS, samples).findings
    assert finding.check == "completeness"
    assert finding.field == "price"
    assert finding.severity is Severity.WARN
    assert finding.observed is not None and finding.observed < 0.7
    assert finding.sample_urls


def test_completeness_drop_quarantines_required_fields() -> None:
    settings = TargetSettings(required=("price",), key=("id",))
    run, samples = build(Shape(price_fill=0.6))
    [finding] = evaluate(run, history(), settings, samples).findings
    assert (finding.check, finding.severity) == ("completeness", Severity.QUARANTINE)


def test_small_drop_is_ignored() -> None:
    run, samples = build(Shape(price_fill=0.9))
    assert evaluate(run, history(), SETTINGS, samples).findings == ()


def exact_fill(items: int, filled: int) -> tuple[RunSummary, SampleIndex]:
    stats = RunStats(EmptyRule())
    for i in range(items):
        stats.add_page(
            f"https://shop.test/{i}", [{"title": "t", "price": 1 if i < filled else None}]
        )
    return RunSummary.from_stats(stats), stats.samples()


def test_small_runs_need_a_larger_drop() -> None:
    base = [exact_fill(200, 194)[0] for _ in range(10)]
    settings = TargetSettings(min_items=5)

    small, small_samples = exact_fill(10, 8)
    small_checks = checks(evaluate(small, base, settings, small_samples).findings)
    assert "completeness" not in small_checks

    large, large_samples = exact_fill(200, 160)
    assert "completeness" in checks(evaluate(large, base, settings, large_samples).findings)


def test_collapse() -> None:
    run, samples = build(Shape(price_fill=0.0))
    assert checks(evaluate(run, history(), SETTINGS, samples).findings) == ["field_collapse"]


def test_missing_field() -> None:
    run, samples = build(Shape(drop_field="price"))
    [finding] = evaluate(run, history(), SETTINGS, samples).findings
    assert (finding.check, finding.field) == ("missing_field", "price")


def test_missing_required_field_is_reported_once() -> None:
    settings = TargetSettings(required=("price",), key=("id",))
    run, samples = build(Shape(drop_field="price"))
    assert checks(evaluate(run, history(), settings, samples).findings) == ["required_field"]


def test_ignored_fields_never_alert() -> None:
    settings = TargetSettings(key=("id",), ignored=frozenset({"price"}))
    run, samples = build(Shape(drop_field="price"))
    assert evaluate(run, history(), settings, samples).findings == ()


def test_new_field() -> None:
    run, samples = build(Shape(extra_field="badge"))
    [finding] = evaluate(run, history(), SETTINGS, samples).findings
    assert (finding.check, finding.field, finding.severity) == ("new_field", "badge", Severity.WARN)


def test_type_change() -> None:
    run, samples = build(Shape(price_as_text=0.6))
    [finding] = evaluate(run, history(), SETTINGS, samples).findings
    assert (finding.check, finding.field) == ("type_change", "price")
    assert "now mostly string" in finding.message


def test_volume_drop_and_page_coverage() -> None:
    run, samples = build(Shape(pages=8))
    result = evaluate(run, history(), SETTINGS, samples)
    assert checks(result.findings) == ["volume_drop", "page_coverage"]
    assert result.findings[0].severity is Severity.QUARANTINE


def test_volume_spike() -> None:
    run, samples = build(Shape(pages=70))
    assert checks(evaluate(run, history(), SETTINGS, samples).findings) == ["volume_spike"]


def test_empty_pages() -> None:
    run, samples = build(Shape(pages=21, empty_pages=6))
    [finding] = evaluate(run, history(), SETTINGS, samples).findings
    assert finding.check == "empty_pages"
    assert finding.severity is Severity.QUARANTINE
    assert len(finding.sample_urls) == 5


def test_fetch_errors_are_absolute() -> None:
    run, samples = build(Shape(pages=20, error_pages=11))
    result = evaluate(run, [], SETTINGS, samples)
    assert checks(result.findings) == ["fetch_errors"]


def test_no_items() -> None:
    run, samples = build(Shape(pages=5, empty_pages=5))
    result = evaluate(run, [], SETTINGS, samples)
    assert checks(result.findings) == ["no_items"]
    assert "5 pages yielded no items" in result.findings[0].message


def test_duplicates_warn() -> None:
    run, samples = build(Shape(pages=21, repeat_share=0.3))
    [finding] = evaluate(run, history(), SETTINGS, samples).findings
    assert (finding.check, finding.severity) == ("duplicates", Severity.WARN)
    assert finding.sample_urls


def test_duplicates_with_fewer_unique_items_suggest_pagination_shift() -> None:
    run, samples = build(Shape(pages=21, repeat_share=0.6))
    result = evaluate(run, history(), SETTINGS, samples)
    finding = next(f for f in result.findings if f.check == "duplicates")
    assert finding.severity is Severity.QUARANTINE
    assert "pagination" in finding.message


def test_duplicates_need_a_key() -> None:
    run, samples = build(Shape(pages=21, repeat_share=0.6), key=())
    base = [build(Shape(seed=i), key=())[0] for i in range(10)]
    assert evaluate(run, base, TargetSettings(), samples).findings == ()


def test_baseline_skips_small_runs() -> None:
    tiny = [build(Shape(pages=1, per_page=5, seed=i))[0] for i in range(10)]
    run, samples = build(Shape(price_fill=0.2))
    result = evaluate(run, tiny, SETTINGS, samples)
    assert result.warming_up
    assert result.baseline_runs == 0


def test_page_types_use_their_own_baseline() -> None:
    stats = RunStats(EmptyRule())
    for i in range(40):
        stats.add_page(f"https://s.test/p/{i}", [{"title": "t", "price": 1}], page_type="product")
    for i in range(40):
        stats.add_page(f"https://s.test/l/{i}", [{"url": "u"}], page_type="listing")
    base = [RunSummary.from_stats(stats)] * 5

    broken = RunStats(EmptyRule())
    for i in range(40):
        broken.add_page(
            f"https://s.test/p/{i}", [{"title": "t", "price": None}], page_type="product"
        )
    for i in range(40):
        broken.add_page(f"https://s.test/l/{i}", [{"url": "u"}], page_type="listing")

    result = evaluate(RunSummary.from_stats(broken), base, TargetSettings(), broken.samples())
    [finding] = result.findings
    assert (finding.page_type, finding.field, finding.check) == (
        "product",
        "price",
        "field_collapse",
    )
    assert "product items" in finding.message


def test_quarantine_findings_come_first() -> None:
    run, samples = build(Shape(pages=8, extra_field="badge"))
    severities = [f.severity for f in evaluate(run, history(), SETTINGS, samples).findings]
    assert severities == sorted(severities, key=lambda s: s is not Severity.QUARANTINE)


def test_empty_pages_in_a_small_run_are_caught() -> None:
    # 6 of 30 pages empty against a baseline that never had any: the case scenario 2 used to miss.
    run, samples = build(Shape(pages=30, empty_pages=6))
    base = [build(Shape(seed=i, pages=30))[0] for i in range(10)]
    assert "empty_pages" in checks(evaluate(run, base, SETTINGS, samples).findings)


def test_one_empty_page_is_not_an_incident() -> None:
    run, samples = build(Shape(pages=30, empty_pages=1))
    base = [build(Shape(seed=i, pages=30))[0] for i in range(10)]
    assert evaluate(run, base, SETTINGS, samples).findings == ()
