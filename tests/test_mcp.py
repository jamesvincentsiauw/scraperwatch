import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scraperwatch import Config
from scraperwatch.mcp_server import main, open_queries
from scraperwatch.queries import Queries
from scraperwatch.run import Run
from scraperwatch.store import open_store


def record_runs(db: Path) -> list[str]:
    """Three healthy runs, then one where price is filled in 1 of 10 items."""
    config = Config(store=str(db), evidence=False)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    ids: list[str] = []
    for day in range(4):
        run = Run(
            "shop",
            config=config,
            store=open_store(config.store),
            clock=lambda t=now + timedelta(days=day): t,
            required=["price"],
        )
        fill = 10 if day < 3 else 1
        for p in range(10):
            items = [{"price": 1 if i < fill else None, "title": "t"} for i in range(10)]
            run.record(f"https://shop.test/{p}", items)
        run.finish()
        ids.append(run.run_id)
    return ids


@pytest.fixture
def queries(tmp_path: Path) -> tuple[Queries, list[str]]:
    db = tmp_path / "sw.db"
    ids = record_runs(db)
    return open_queries(Config(store=str(db), evidence=False)), ids


def test_list_runs(queries: tuple[Queries, list[str]]) -> None:
    q, ids = queries
    rows = q.list_runs("shop")
    assert [r["run_id"] for r in rows] == list(reversed(ids))
    assert rows[0]["status"] == "quarantine"
    assert q.list_runs("other") == []
    assert len(q.list_runs(limit=2)) == 2
    assert len(q.list_runs(limit=-5)) == 1


def test_get_run_has_findings_and_samples(queries: tuple[Queries, list[str]]) -> None:
    q, ids = queries
    doc = q.get_run(ids[-1])
    assert doc["status"] == "quarantine"
    finding = next(f for f in doc["findings"] if f["check"] == "required_field")
    assert finding["field"] == "price"
    assert finding["sample_urls"]


def test_compare_run_shows_the_drop(queries: tuple[Queries, list[str]]) -> None:
    q, ids = queries
    doc = q.compare_run(ids[-1])
    assert doc["baseline_runs"] == 3
    price = next(f for f in doc["fields"] if f["field"] == "price")
    assert price["completeness"] == pytest.approx(0.1)
    assert price["baseline_median"] == 1.0


def test_compare_run_uses_the_baseline_from_before_the_run(
    queries: tuple[Queries, list[str]],
) -> None:
    q, ids = queries
    assert q.compare_run(ids[1])["baseline_runs"] == 1


def test_get_baseline(queries: tuple[Queries, list[str]]) -> None:
    q, _ = queries
    doc = q.get_baseline("shop")
    assert doc["baseline_runs"] == 3
    price = next(f for f in doc["fields"] if f["field"] == "price")
    assert price["usually_filled"] == 1.0
    assert q.get_baseline("other") == {"target": "other", "baseline_runs": 0, "fields": []}


def test_unknown_run_is_an_error(queries: tuple[Queries, list[str]]) -> None:
    q, _ = queries
    with pytest.raises(ValueError, match="No run with id nope"):
        q.get_run("nope")
    with pytest.raises(ValueError, match="No run with id nope"):
        q.compare_run("nope")


def test_missing_store_is_not_created(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "typo.db"
    assert main(["--store", str(missing)]) == 1
    assert "No scraperwatch store" in capsys.readouterr().err
    assert not missing.exists()


def test_stdio_session(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    db = tmp_path / "sw.db"
    ids = record_runs(db)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "scraperwatch.mcp_server", "--store", str(db)],
    )

    async def session() -> tuple[set[str], dict[str, object], bool]:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            tools = {t.name for t in (await client.list_tools()).tools}
            ok = await client.call_tool("get_run", {"run_id": ids[-1]})
            bad = await client.call_tool("get_run", {"run_id": "nope"})
            first = ok.content[0]
            assert first.type == "text"
            return tools, json.loads(first.text), bad.isError

    tools, doc, bad_is_error = asyncio.run(asyncio.wait_for(session(), timeout=30))
    assert tools == {"list_runs", "get_run", "compare_run", "get_baseline"}
    assert doc["status"] == "quarantine"
    assert bad_is_error
