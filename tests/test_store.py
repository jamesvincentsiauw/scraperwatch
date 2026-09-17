import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scraperwatch.model import Finding, RunSummary, Severity, Status
from scraperwatch.stats.aggregate import RunStats
from scraperwatch.stats.values import EmptyRule
from scraperwatch.store import SCHEMA_VERSION, RunRecord, SQLiteStore, StoredRun, open_store

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def summary(items: int) -> RunSummary:
    stats = RunStats(EmptyRule(), ("id",))
    stats.add_page("https://a.test/1", [{"id": i, "price": i} for i in range(items)], status=200)
    return RunSummary.from_stats(stats)


def stored(run_id: str, day: int, status: Status = Status.PASS, items: int = 10) -> StoredRun:
    finished = T0 + timedelta(days=day)
    findings = (
        (Finding("volume_drop", Severity.QUARANTINE, "dropped", "default", observed=1.0),)
        if status is Status.QUARANTINE
        else ()
    )
    record = RunRecord(
        run_id,
        "shop",
        finished - timedelta(minutes=5),
        finished,
        status,
        False,
        False,
        0,
        1,
        items,
        "0.1.0",
    )
    return StoredRun(record, summary(items), findings)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteStore:
    return open_store(str(tmp_path / "nested" / "sw.db"))


def test_save_and_get_round_trip(store: SQLiteStore) -> None:
    run = stored("r1", 0, Status.QUARANTINE)
    store.save(run)
    got = store.get("r1")
    assert got == run
    assert store.get("missing") is None


def test_baseline_uses_passing_and_accepted_runs_newest_first(store: SQLiteStore) -> None:
    store.save(stored("r1", 1, items=11))
    store.save(stored("r2", 2, Status.QUARANTINE, items=12))
    store.save(stored("r3", 3, Status.WARN, items=13))
    store.save(stored("r4", 4, items=14))
    assert store.accept("r3")
    assert not store.accept("nope")

    base = store.baseline("shop", limit=10, before=T0 + timedelta(days=30))
    assert [b.items for b in base] == [14, 13, 11]
    assert [b.items for b in store.baseline("shop", limit=2, before=T0 + timedelta(days=30))] == [
        14,
        13,
    ]
    assert store.baseline("other", limit=10) == []
    assert [
        b.items for b in store.baseline("shop", limit=10, before=T0 + timedelta(days=3, minutes=1))
    ] == [13, 11]


def test_runs_listing(store: SQLiteStore) -> None:
    for i in range(3):
        store.save(stored(f"r{i}", i))
    assert [r.id for r in store.runs("shop")] == ["r2", "r1", "r0"]
    assert [r.id for r in store.runs(limit=1)] == ["r2"]
    latest = store.latest("shop")
    assert latest is not None and latest.id == "r2"
    assert store.latest("other") is None


def test_prune_removes_old_runs_and_their_rows(store: SQLiteStore) -> None:
    store.save(stored("old", 0, Status.QUARANTINE))
    store.save(stored("new", 40))
    removed = store.prune(timedelta(days=30), now=T0 + timedelta(days=45))
    assert removed == 1
    assert store.get("old") is None
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM page_type_stats").fetchone()[0] == 1


def test_reopening_keeps_data_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "sw.db"
    SQLiteStore(path).save(stored("r1", 0))
    again = SQLiteStore(path)
    assert again.get("r1") is not None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == SCHEMA_VERSION


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sw.db"
    SQLiteStore(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO schema_version VALUES (99)")
    with pytest.raises(RuntimeError, match="newer"):
        SQLiteStore(path)


def test_store_urls(tmp_path: Path) -> None:
    assert open_store(f"sqlite:///{tmp_path}/a.db").path == tmp_path / "a.db"
    with pytest.raises(ValueError, match="only SQLite"):
        open_store("postgresql://localhost/db")
