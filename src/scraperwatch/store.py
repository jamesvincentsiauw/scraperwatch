"""SQLite storage for run summaries, findings and baseline lookups."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scraperwatch.model import Finding, PageTypeSummary, RunSummary, Status

SCHEMA_VERSION = 1

MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        target TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL,
        status TEXT NOT NULL,
        accepted INTEGER NOT NULL DEFAULT 0,
        warming_up INTEGER NOT NULL,
        baseline_runs INTEGER NOT NULL,
        pages INTEGER NOT NULL,
        items INTEGER NOT NULL,
        library_version TEXT NOT NULL
    );
    CREATE INDEX runs_by_target ON runs (target, finished_at);
    CREATE TABLE page_type_stats (
        run_id TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
        page_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        PRIMARY KEY (run_id, page_type)
    );
    CREATE TABLE domain_stats (
        run_id TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
        domain TEXT NOT NULL,
        status_counts TEXT NOT NULL,
        PRIMARY KEY (run_id, domain)
    );
    CREATE TABLE findings (
        run_id TEXT NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        check_name TEXT NOT NULL,
        severity TEXT NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY (run_id, position)
    );
    """,
}


@dataclass(frozen=True)
class RunRecord:
    id: str
    target: str
    started_at: datetime
    finished_at: datetime
    status: Status
    accepted: bool
    warming_up: bool
    baseline_runs: int
    pages: int
    items: int
    library_version: str


@dataclass(frozen=True)
class StoredRun:
    record: RunRecord
    summary: RunSummary
    findings: tuple[Finding, ...]


def open_store(url: str) -> SQLiteStore:
    """Accepts `sqlite:///relative.db`, `sqlite:////absolute.db` or a plain path."""
    if url.startswith("sqlite:///"):
        return SQLiteStore(Path(url.removeprefix("sqlite:///")))
    if "://" in url:
        raise ValueError(f"unsupported store URL {url!r}; only SQLite is available in this release")
    return SQLiteStore(Path(url))


class SQLiteStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            self._migrate(db)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        # A connection per call keeps the store safe to share across threads and processes.
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _migrate(self, db: sqlite3.Connection) -> None:
        db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] or 0
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"{self.path} uses schema {current}, newer than this scraperwatch ({SCHEMA_VERSION}); upgrade the package"
            )
        for version in range(current + 1, SCHEMA_VERSION + 1):
            db.executescript(MIGRATIONS[version])
            db.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))

    def save(self, run: StoredRun) -> None:
        r = run.record
        with self._connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r.id,
                    r.target,
                    r.started_at.isoformat(),
                    r.finished_at.isoformat(),
                    r.status.value,
                    int(r.accepted),
                    int(r.warming_up),
                    r.baseline_runs,
                    r.pages,
                    r.items,
                    r.library_version,
                ),
            )
            db.executemany(
                "INSERT INTO page_type_stats VALUES (?, ?, ?)",
                [
                    (r.id, name, json.dumps(page.to_dict()))
                    for name, page in run.summary.page_types.items()
                ],
            )
            db.executemany(
                "INSERT INTO domain_stats VALUES (?, ?, ?)",
                [
                    (r.id, domain, json.dumps({str(k): v for k, v in counts.items()}))
                    for domain, counts in run.summary.domains.items()
                ],
            )
            db.executemany(
                "INSERT INTO findings VALUES (?, ?, ?, ?, ?)",
                [
                    (r.id, i, f.check, f.severity.value, json.dumps(f.to_dict()))
                    for i, f in enumerate(run.findings)
                ],
            )

    def baseline(self, target: str, limit: int, before: datetime | None = None) -> list[RunSummary]:
        """The most recent passing or accepted runs, newest first."""
        cutoff = (before or datetime.now(UTC)).isoformat()
        with self._connect() as db:
            ids = [
                row["id"]
                for row in db.execute(
                    """SELECT id FROM runs
                       WHERE target = ? AND finished_at < ? AND (status = 'pass' OR accepted = 1)
                       ORDER BY finished_at DESC LIMIT ?""",
                    (target, cutoff, limit),
                )
            ]
            return [self._summary(db, run_id) for run_id in ids]

    def get(self, run_id: str) -> StoredRun | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            findings = tuple(
                Finding.from_dict(json.loads(f["data"]))
                for f in db.execute(
                    "SELECT data FROM findings WHERE run_id = ? ORDER BY position", (run_id,)
                )
            )
            return StoredRun(_record(row), self._summary(db, run_id), findings)

    def latest(self, target: str) -> RunRecord | None:
        runs = self.runs(target, limit=1)
        return runs[0] if runs else None

    def runs(self, target: str | None = None, limit: int = 20) -> list[RunRecord]:
        with self._connect() as db:
            if target is None:
                rows = db.execute("SELECT * FROM runs ORDER BY finished_at DESC LIMIT ?", (limit,))
            else:
                rows = db.execute(
                    "SELECT * FROM runs WHERE target = ? ORDER BY finished_at DESC LIMIT ?",
                    (target, limit),
                )
            return [_record(row) for row in rows]

    def accept(self, run_id: str) -> bool:
        with self._connect() as db:
            cur = db.execute("UPDATE runs SET accepted = 1 WHERE id = ?", (run_id,))
            return cur.rowcount == 1

    def prune(self, older_than: timedelta, now: datetime | None = None) -> int:
        cutoff = ((now or datetime.now(UTC)) - older_than).isoformat()
        with self._connect() as db:
            cur = db.execute("DELETE FROM runs WHERE finished_at < ?", (cutoff,))
            return cur.rowcount

    @staticmethod
    def _summary(db: sqlite3.Connection, run_id: str) -> RunSummary:
        pages = {
            row["page_type"]: PageTypeSummary.from_dict(json.loads(row["summary"]))
            for row in db.execute(
                "SELECT page_type, summary FROM page_type_stats WHERE run_id = ?", (run_id,)
            )
        }
        domains = {
            row["domain"]: {int(k): v for k, v in json.loads(row["status_counts"]).items()}
            for row in db.execute(
                "SELECT domain, status_counts FROM domain_stats WHERE run_id = ?", (run_id,)
            )
        }
        return RunSummary(pages, domains)


def _record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        target=row["target"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]),
        status=Status(row["status"]),
        accepted=bool(row["accepted"]),
        warming_up=bool(row["warming_up"]),
        baseline_runs=row["baseline_runs"],
        pages=row["pages"],
        items=row["items"],
        library_version=row["library_version"],
    )
