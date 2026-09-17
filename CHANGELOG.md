# Changelog

## 0.1.0 (unreleased)

- `scraperwatch.run()` context manager with `record()` and a pass, warn or quarantine verdict.
- Checks against a baseline of recent healthy runs: field completeness and collapse, missing and new fields, type changes, item volume, page coverage, empty pages and duplicate keys.
- Absolute rules that apply before a baseline exists: required fields, fetch errors and runs with no items.
- SQLite store, evidence bundles and a self-contained HTML report.
- `scraperwatch` command: `runs`, `show`, `report`, `accept`, `baseline`, `prune`.
- Configuration through `scraperwatch.toml` and `SCRAPERWATCH_*` variables.
- Fake shop simulation and evaluation report.
