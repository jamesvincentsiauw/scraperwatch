# Contributing

Thanks for looking. Issues with a concrete failure are the most useful contribution: what the scraper saw, what scraperwatch reported, and what you expected.

## Setup

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

## Adding or changing a check

1. Put the rule in `src/scraperwatch/checks.py` and its default thresholds in `Thresholds` in `src/scraperwatch/settings.py`.
2. Add unit tests in `tests/test_checks.py` for the case it catches and for a healthy run it must leave alone.
3. If the check targets a new kind of incident, add a fault to `simulation/fakeshop.py` and a scenario to `simulation/simulate.py`.
4. Run `uv run python -m simulation.simulate --seeds 30 --output docs/evaluation.md` and commit the updated report. A change that adds false alarms needs a reason in the pull request.

## Guidelines

- The core has no runtime dependencies. Optional features go behind extras.
- `record()` must stay cheap and must not do I/O.
- Never store item values. Counts, type names and sample URLs are fine.
- scraperwatch detects blocks and challenge pages; it will not include code that bypasses them.
- Test data comes from the fake shop. No test or example scrapes a real site.
