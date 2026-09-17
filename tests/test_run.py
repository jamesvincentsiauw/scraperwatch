import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import scraperwatch
from scraperwatch import Config, Quarantined, Status
from scraperwatch.run import Run
from scraperwatch.store import open_store


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def next_day(self) -> None:
        self.now += timedelta(days=1)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(store=str(tmp_path / "sw.db"), evidence_dir=tmp_path / "evidence")


def scrape(run: Run, price_fill: float = 1.0, pages: int = 10) -> None:
    for p in range(pages):
        items = [
            {"id": p * 10 + i, "title": "t", "price": 5 if i < price_fill * 10 else None}
            for i in range(10)
        ]
        run.record(f"https://shop.test/{p}", items, status=200)


def make_run(config: Config, clock: Clock, **kwargs: object) -> Run:
    return Run("shop", config=config, store=open_store(config.store), clock=clock, **kwargs)  # type: ignore[arg-type]


def healthy_days(config: Config, clock: Clock, days: int = 5) -> None:
    for _ in range(days):
        with make_run(config, clock, key=["id"]) as run:
            scrape(run)
        assert run.verdict.status is Status.PASS
        clock.next_day()


def test_first_runs_warm_up_then_catch_a_broken_field(config: Config) -> None:
    clock = Clock()
    with make_run(config, clock) as first:
        scrape(first)
    assert first.verdict.warming_up
    assert first.verdict.baseline_runs == 0
    clock.next_day()

    healthy_days(config, clock)
    with make_run(config, clock, key=["id"]) as broken:
        scrape(broken, price_fill=0.3)

    verdict = broken.verdict
    assert not verdict.warming_up
    assert verdict.baseline_runs == 6
    assert verdict.status is Status.WARN
    assert [f.check for f in verdict.findings] == ["completeness"]
    assert broken.evidence_path is not None
    assert (broken.evidence_path / "summary.json").is_file()
    assert not (broken.evidence_path / "report.html").exists()


def test_required_field_quarantines_and_writes_a_report(config: Config) -> None:
    clock = Clock()
    healthy_days(config, clock)
    with make_run(config, clock, required=["price"]) as run:
        scrape(run, price_fill=0.3)

    assert run.verdict.quarantined
    assert run.evidence_path is not None
    assert (run.evidence_path / "report.html").is_file()
    with pytest.raises(Quarantined) as excinfo:
        raise Quarantined(run.verdict)
    assert "required" in str(excinfo.value).lower()


def test_quarantined_runs_stay_out_of_the_baseline(config: Config) -> None:
    clock = Clock()
    healthy_days(config, clock, days=3)
    quarantined: list[str] = []
    for _ in range(3):
        with make_run(config, clock) as run:
            scrape(run, pages=2)
        assert run.verdict.quarantined
        quarantined.append(run.run_id)
        clock.next_day()

    store = open_store(config.store)
    assert len(store.baseline("shop", 10, before=clock.now)) == 3
    assert store.accept(quarantined[-1])
    assert len(store.baseline("shop", 10, before=clock.now)) == 4


def test_a_crash_is_recorded_and_re_raised(config: Config) -> None:
    run = make_run(config, Clock())
    with pytest.raises(ZeroDivisionError), run:
        scrape(run)
        _ = 1 / 0
    assert run.verdict.quarantined
    assert run.verdict.findings[0].check == "run_crashed"
    stored = open_store(config.store).get(run.run_id)
    assert stored is not None and stored.record.status is Status.QUARANTINE


def test_run_lifecycle_guards(config: Config) -> None:
    run = make_run(config, Clock())
    with pytest.raises(RuntimeError, match="not finished"):
        _ = run.verdict
    scrape(run)
    first = run.finish()
    assert run.finish() is first
    with pytest.raises(RuntimeError, match="already finished"):
        run.record("https://shop.test/late", [])
    with pytest.raises(ValueError):
        Run("", config=config, store=open_store(config.store))


def test_store_failure_does_not_lose_the_verdict(
    config: Config, caplog: pytest.LogCaptureFixture
) -> None:
    run = make_run(config, Clock())
    scrape(run)

    def broken(_: object) -> None:
        raise OSError("disk full")

    run.store.save = broken  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="scraperwatch"):
        verdict = run.finish()
    assert verdict.status is Status.PASS
    assert "could not save run" in caplog.text


def test_evidence_can_be_turned_off(tmp_path: Path) -> None:
    config = Config(store=str(tmp_path / "sw.db"), evidence_dir=tmp_path / "ev", evidence=False)
    with make_run(config, Clock()) as run:
        run.record("https://shop.test/0", [])
    assert run.verdict.quarantined
    assert run.evidence_path is None
    assert not (tmp_path / "ev").exists()


def test_public_run_reads_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scraperwatch.toml").write_text(
        'store = "data/sw.db"\n[targets.shop]\nrequired = ["price"]\n'
    )
    with scraperwatch.run("shop") as run:
        run.record("https://shop.test/0", [{"price": None}])
    assert run.settings.required == ("price",)
    assert run.verdict.quarantined
    assert (tmp_path / "data" / "sw.db").is_file()
    assert (tmp_path / ".scraperwatch" / "evidence" / "shop").is_dir()


def test_accepts_pydantic_style_items(config: Config) -> None:
    class Item:
        def __init__(self, price: int) -> None:
            self.price = price

        def model_dump(self) -> dict[str, int]:
            return {"price": self.price}

    factory: Callable[[int], Item] = Item
    with make_run(config, Clock()) as run:
        run.record("https://shop.test/0", [factory(1), factory(2)], page_type="product")
    assert run.verdict.status is Status.PASS
