"""Evidence bundles: what an engineer needs to debug a warned or quarantined run."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scraperwatch.model import RunSummary
from scraperwatch.stats.robust import median
from scraperwatch.store import StoredRun


@dataclass(frozen=True)
class FieldRow:
    page_type: str
    field: str
    completeness: float
    presence: float
    main_type: str | None
    baseline_median: float | None
    baseline_min: float | None
    baseline_max: float | None


def field_rows(run: RunSummary, baseline: Sequence[RunSummary]) -> list[FieldRow]:
    rows: list[FieldRow] = []
    for page_type, page in sorted(run.page_types.items()):
        history = [b.page_types[page_type] for b in baseline if page_type in b.page_types]
        names = set(page.fields) | {f for h in history for f in h.fields}
        for name in sorted(names):
            past = [h.completeness(name) for h in history]
            dominant = page.dominant_type(name)
            rows.append(
                FieldRow(
                    page_type=page_type,
                    field=name,
                    completeness=page.completeness(name),
                    presence=page.presence(name),
                    main_type=dominant[0] if dominant else None,
                    baseline_median=median(past) if past else None,
                    baseline_min=min(past) if past else None,
                    baseline_max=max(past) if past else None,
                )
            )
    return rows


def summary_document(run: StoredRun) -> dict[str, Any]:
    r = run.record
    return {
        "run_id": r.id,
        "target": r.target,
        "status": r.status.value,
        "accepted": r.accepted,
        "warming_up": r.warming_up,
        "baseline_runs": r.baseline_runs,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat(),
        "pages": r.pages,
        "items": r.items,
        "library_version": r.library_version,
        "findings": [f.to_dict() for f in run.findings],
    }


def write_bundle(
    root: Path, run: StoredRun, baseline: Sequence[RunSummary], html: bool = False
) -> Path:
    """Writes the bundle under root/<target>/<run_id>/ and returns that directory."""
    from scraperwatch.report import render_html

    directory = root / _safe(run.record.target) / _safe(run.record.id)
    directory.mkdir(parents=True, exist_ok=True)
    _dump(directory / "summary.json", summary_document(run))
    _dump(
        directory / "fields.json",
        [row.__dict__ for row in field_rows(run.summary, baseline)],
    )
    _dump(
        directory / "samples.json",
        [
            {
                "check": f.check,
                "page_type": f.page_type,
                "field": f.field,
                "urls": list(f.sample_urls),
            }
            for f in run.findings
            if f.sample_urls
        ],
    )
    if html:
        (directory / "report.html").write_text(render_html(run, baseline), encoding="utf-8")
    return directory


def _dump(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _safe(name: str) -> str:
    """Keeps target names from escaping the evidence directory."""
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in name).strip(".")
    return cleaned or "_"
