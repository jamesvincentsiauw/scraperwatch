"""The public entry point: a run that records pages and returns a verdict."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from scraperwatch import __version__
from scraperwatch.checks import evaluate
from scraperwatch.config import Config, load_config
from scraperwatch.evidence import write_bundle
from scraperwatch.model import Finding, RunSummary, Severity, Status, Verdict
from scraperwatch.stats.aggregate import RunStats
from scraperwatch.stats.values import EmptyRule
from scraperwatch.store import RunRecord, SQLiteStore, StoredRun, open_store

log = logging.getLogger("scraperwatch")


def _now() -> datetime:
    return datetime.now(UTC)


class Run:
    """Collects what one scraper run extracted and judges it when the run ends.

    Use it as a context manager, or call `finish()` yourself.
    """

    def __init__(
        self,
        target: str,
        *,
        config: Config,
        store: SQLiteStore,
        required: Sequence[str] | None = None,
        key: Sequence[str] | None = None,
        ignored: Sequence[str] | None = None,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if not target:
            raise ValueError("target must be a non-empty name")
        self.target = target
        self.settings = config.target_settings(target, required=required, key=key, ignored=ignored)
        self.config = config
        self.store = store
        self._clock = clock
        self.started_at = clock()
        self.run_id = f"{self.started_at:%Y%m%dT%H%M%SZ}-{secrets.token_hex(3)}"
        self.evidence_path: Path | None = None
        self._stats = RunStats(EmptyRule(self.settings.empty_values), self.settings.key)
        self._baseline = store.baseline(target, self.settings.baseline_runs, before=self.started_at)
        self._verdict: Verdict | None = None

    def record(
        self,
        url: str,
        items: Iterable[Any] = (),
        *,
        status: int | None = None,
        body: bytes | str | None = None,
        page_type: str | None = None,
        error: str | None = None,
    ) -> None:
        """Records one fetched page and the items extracted from it.

        An empty `items` list is meaningful: it counts as a page that yielded nothing.
        `body` is accepted now and used by template and block detection in a later release.
        """
        if self._verdict is not None:
            raise RuntimeError("this run has already finished")
        del body
        self._stats.add_page(url, items, status=status, error=error, page_type=page_type)

    @property
    def verdict(self) -> Verdict:
        if self._verdict is None:
            raise RuntimeError("the run has not finished yet")
        return self._verdict

    def finish(self, error: BaseException | None = None) -> Verdict:
        if self._verdict is not None:
            return self._verdict

        summary = RunSummary.from_stats(self._stats)
        result = evaluate(summary, self._baseline, self.settings, self._stats.samples())
        findings = result.findings
        if error is not None:
            crash = Finding(
                "run_crashed",
                Severity.QUARANTINE,
                f"The scraper raised {type(error).__name__}: {error}",
            )
            findings = (crash, *findings)

        status = Verdict.status_for(findings)
        self._verdict = Verdict(
            self.run_id, self.target, status, findings, result.warming_up, result.baseline_runs
        )
        stored = StoredRun(
            RunRecord(
                id=self.run_id,
                target=self.target,
                started_at=self.started_at,
                finished_at=max(self._clock(), self.started_at),
                status=status,
                accepted=False,
                warming_up=result.warming_up,
                baseline_runs=result.baseline_runs,
                pages=summary.pages,
                items=summary.items,
                library_version=__version__,
            ),
            summary,
            findings,
        )
        # A monitoring failure must not throw away a scrape that already happened.
        try:
            self.store.save(stored)
        except Exception:
            log.exception("scraperwatch could not save run %s", self.run_id)
        if status is not Status.PASS and self.config.evidence:
            try:
                self.evidence_path = write_bundle(
                    self.config.evidence_dir,
                    stored,
                    self._baseline,
                    html=status is Status.QUARANTINE,
                )
            except OSError:
                log.exception("scraperwatch could not write evidence for run %s", self.run_id)

        log_level = logging.INFO if status is Status.PASS else logging.WARNING
        log.log(log_level, self._verdict.summary)
        return self._verdict

    def __enter__(self) -> Run:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.finish(exc)


def run(
    target: str,
    *,
    required: Sequence[str] | None = None,
    key: Sequence[str] | None = None,
    ignored: Sequence[str] | None = None,
    config: Config | None = None,
    config_path: Path | None = None,
) -> Run:
    """Starts a run for `target`, reading scraperwatch.toml and SCRAPERWATCH_* variables."""
    cfg = config or load_config(config_path)
    return Run(
        target,
        config=cfg,
        store=open_store(cfg.store),
        required=required,
        key=key,
        ignored=ignored,
    )
