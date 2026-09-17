import argparse
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scraperwatch import Config
from scraperwatch.cli import main, parse_duration
from scraperwatch.run import Run
from scraperwatch.store import open_store


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scraperwatch.toml").write_text('store = "sw.db"\nevidence = false\n')
    return tmp_path


def record_runs(workdir: Path) -> tuple[str, str]:
    config = Config(store=str(workdir / "sw.db"), evidence=False)
    now = [datetime(2026, 9, 1, tzinfo=UTC)]
    ids: list[str] = []
    for day in range(4):
        run = Run(
            "shop",
            config=config,
            store=open_store(config.store),
            clock=lambda: now[0],
            required=["price"],
        )
        fill = 10 if day < 3 else 1
        for p in range(10):
            items = [{"price": 1 if i < fill else None, "title": "t"} for i in range(10)]
            run.record(f"https://shop.test/{p}", items)
        run.finish()
        ids.append(run.run_id)
        now[0] += timedelta(days=1)
    return ids[0], ids[-1]


def run_cli(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(list(argv), stdout=out)
    return code, out.getvalue()


def test_runs_and_show(workdir: Path) -> None:
    good, bad = record_runs(workdir)

    code, text = run_cli("runs")
    assert code == 0
    assert text.count("shop") == 4
    assert "quarantine" in text

    code, text = run_cli("show", good)
    assert code == 0
    assert "pass" in text

    code, text = run_cli("show", bad)
    assert code == 2
    assert "[quarantine] required_field (default / price)" in text
    assert "https://shop.test/0" in text


def test_accept_changes_the_baseline(workdir: Path) -> None:
    _, bad = record_runs(workdir)
    _, before = run_cli("baseline", "shop")
    assert "baseline of 3 runs" in before

    code, text = run_cli("accept", bad)
    assert code == 0
    assert "now part of the shop baseline" in text

    _, after = run_cli("baseline", "shop")
    assert "baseline of 4 runs" in after
    assert "price" in after

    _, listing = run_cli("runs", "shop")
    assert "quarantine*" in listing
    assert "accepted into the baseline" in listing


def test_report(workdir: Path) -> None:
    _, bad = record_runs(workdir)
    code, _ = run_cli("report", bad, "-o", "out.html")
    assert code == 0
    html = (workdir / "out.html").read_text()
    assert "Required field price" in html

    code, _ = run_cli("report", bad)
    assert code == 0
    assert (workdir / f"scraperwatch-{bad}.html").is_file()


def test_unknown_run(workdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = run_cli("show", "nope")
    assert code == 3
    assert "No run with id nope" in capsys.readouterr().err


def test_empty_store(workdir: Path) -> None:
    assert run_cli("runs") == (0, "No runs recorded yet.\n")
    assert run_cli("baseline", "shop") == (0, "No passing or accepted runs for shop yet.\n")


def test_prune(workdir: Path) -> None:
    record_runs(workdir)
    code, text = run_cli("prune", "--older-than", "1d")
    assert code == 0
    assert text == "Deleted 4 runs.\n"


def test_store_option_overrides_config(workdir: Path) -> None:
    record_runs(workdir)
    _, text = run_cli("--store", str(workdir / "other.db"), "runs")
    assert text == "No runs recorded yet.\n"


def test_parse_duration() -> None:
    assert parse_duration("90d") == timedelta(days=90)
    assert parse_duration("12h") == timedelta(hours=12)
    assert parse_duration("30m") == timedelta(minutes=30)
    with pytest.raises(argparse.ArgumentTypeError):
        parse_duration("soon")
