"""Read-only questions about recorded runs, answered as plain dicts for the MCP server."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from scraperwatch.config import Config
from scraperwatch.evidence import field_rows, summary_document
from scraperwatch.store import RunRecord, SQLiteStore

MAX_RUNS = 100


class Queries:
    def __init__(self, store: SQLiteStore, config: Config) -> None:
        self._store = store
        self._config = config

    def list_runs(self, target: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        limit = min(max(limit, 1), MAX_RUNS)
        return [_run_row(r) for r in self._store.runs(target, limit)]

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._store.get(run_id)
        if run is None:
            raise ValueError(f"No run with id {run_id}.")
        return summary_document(run)

    def compare_run(self, run_id: str) -> dict[str, Any]:
        """Each field's fill rate in this run next to the baseline the run was judged against."""
        run = self._store.get(run_id)
        if run is None:
            raise ValueError(f"No run with id {run_id}.")
        target = run.record.target
        baseline = self._store.baseline(
            target, self._config.target_settings(target).baseline_runs, before=run.record.started_at
        )
        return {
            "run_id": run_id,
            "target": target,
            "status": run.record.status.value,
            "baseline_runs": len(baseline),
            "fields": [asdict(row) for row in field_rows(run.summary, baseline)],
        }

    def get_baseline(self, target: str) -> dict[str, Any]:
        """What the next run of this target will be compared with."""
        history = self._store.baseline(target, self._config.target_settings(target).baseline_runs)
        if not history:
            return {"target": target, "baseline_runs": 0, "fields": []}
        rows = field_rows(history[0], history)
        return {
            "target": target,
            "baseline_runs": len(history),
            "fields": [
                {
                    "page_type": r.page_type,
                    "field": r.field,
                    "usually_filled": r.baseline_median,
                    "min": r.baseline_min,
                    "max": r.baseline_max,
                }
                for r in rows
                if r.baseline_median is not None
            ],
        }


def _run_row(r: RunRecord) -> dict[str, Any]:
    return {
        "run_id": r.id,
        "target": r.target,
        "status": r.status.value,
        "accepted": r.accepted,
        "warming_up": r.warming_up,
        "finished_at": r.finished_at.isoformat(),
        "pages": r.pages,
        "items": r.items,
    }
