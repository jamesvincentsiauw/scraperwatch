"""Self-contained HTML report for one run."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from scraperwatch.evidence import field_rows
from scraperwatch.model import Finding, RunSummary, Status
from scraperwatch.store import StoredRun

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1d1f23; --muted: #555b66; --line: #d9dce1;
  --pass: #1b6b3a; --warn: #8a5300; --quarantine: #b3261e; --row: #f5f6f8;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #15171a; --fg: #e8eaed; --muted: #a8adb6; --line: #33373d;
    --pass: #6fd08f; --warn: #f0b35a; --quarantine: #ff8a80; --row: #1d2024;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; overflow-wrap: anywhere; }
h2 { font-size: 17px; margin: 32px 0 8px; }
.meta { color: var(--muted); margin: 0; }
.status { font-weight: 700; }
.status.pass { color: var(--pass); }
.status.warn { color: var(--warn); }
.status.quarantine { color: var(--quarantine); }
ol.findings { padding-left: 20px; }
ol.findings li { margin-bottom: 12px; }
.urls { margin: 4px 0 0; padding-left: 18px; color: var(--muted); font-size: 13px; }
.urls li { margin: 0; overflow-wrap: anywhere; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); font-weight: 600; }
tr.flagged td { background: var(--row); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
code { font-size: 13px; }
"""


def _pct(x: float | None) -> str:
    return "" if x is None else f"{x:.0%}"


def _finding(f: Finding) -> str:
    where = " · ".join(escape(p) for p in (f.page_type, f.field) if p)
    location = f" · {where}" if where else ""
    items = "".join(
        f'<li><a href="{escape(u)}" rel="noreferrer">{escape(u)}</a></li>' for u in f.sample_urls
    )
    urls = f'<ul class="urls">{items}</ul>' if items else ""
    return (
        f'<li><span class="status {f.severity.value}">{escape(f.severity.value)}</span> '
        f"<code>{escape(f.check)}</code>{location}<br>{escape(f.message)}{urls}</li>"
    )


def render_html(run: StoredRun, baseline: Sequence[RunSummary]) -> str:
    r = run.record
    flagged = {(f.page_type, f.field) for f in run.findings if f.field}
    status_text = r.status.value + (" (accepted into the baseline)" if r.accepted else "")

    if run.findings:
        findings_html = f'<ol class="findings">{"".join(_finding(f) for f in run.findings)}</ol>'
    elif r.status is Status.PASS:
        findings_html = "<p>No findings. The run matches its baseline.</p>"
    else:
        findings_html = "<p>No findings were recorded for this run.</p>"
    if r.warming_up:
        findings_html += (
            f'<p class="meta">The baseline has {r.baseline_runs} usable runs, so only absolute '
            "rules were checked. Comparisons start once the target has enough healthy runs.</p>"
        )

    rows: list[str] = []
    for row in field_rows(run.summary, baseline):
        cls = ' class="flagged"' if (row.page_type, row.field) in flagged else ""
        usual = (
            ""
            if row.baseline_median is None
            else f"{_pct(row.baseline_median)} ({_pct(row.baseline_min)} to {_pct(row.baseline_max)})"
        )
        rows.append(
            f"<tr{cls}><td>{escape(row.page_type)}</td><td><code>{escape(row.field)}</code></td>"
            f'<td class="num">{_pct(row.completeness)}</td><td class="num">{_pct(row.presence)}</td>'
            f"<td>{usual}</td><td>{escape(row.main_type or '')}</td></tr>"
        )
    fields_html = (
        '<div class="table-wrap"><table><thead><tr><th scope="col">Page type</th>'
        '<th scope="col">Field</th><th scope="col">Filled</th><th scope="col">Present</th>'
        '<th scope="col">Usually filled (range)</th><th scope="col">Main type</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
        if rows
        else "<p>No fields were extracted.</p>"
    )

    finished = escape(r.finished_at.strftime("%Y-%m-%d %H:%M UTC"))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(r.target)} run {escape(r.id)}</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<h1>{escape(r.target)}</h1>
<p class="meta">Run <code>{escape(r.id)}</code> · finished {finished}
· {r.pages} pages · {r.items} items · scraperwatch {escape(r.library_version)}</p>
<p>Verdict: <span class="status {r.status.value}">{escape(status_text)}</span></p>
<h2>Findings</h2>
{findings_html}
<h2>Fields</h2>
{fields_html}
</main>
</body>
</html>
"""
