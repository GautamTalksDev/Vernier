"""SQLite index over runs — index only, never the source of truth."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INDEX_PATH = Path(".vernier/index.sqlite")


@dataclass(frozen=True, slots=True)
class RunIndexRow:
    run_id: str
    path: str
    created_at: str
    event_count: int
    root_digest: str


def connect(index_path: Path) -> sqlite3.Connection:
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(index_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            event_count INTEGER NOT NULL,
            root_digest TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def upsert_run(
    index_path: Path,
    *,
    run_id: str,
    path: str,
    created_at: str,
    event_count: int,
    root_digest: str,
) -> None:
    conn = connect(index_path)
    try:
        conn.execute(
            """
            INSERT INTO runs (run_id, path, created_at, event_count, root_digest)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                path=excluded.path,
                created_at=excluded.created_at,
                event_count=excluded.event_count,
                root_digest=excluded.root_digest
            """,
            (run_id, path, created_at, event_count, root_digest),
        )
        conn.commit()
    finally:
        conn.close()


def list_runs(index_path: Path) -> list[RunIndexRow]:
    if not Path(index_path).exists():
        return []
    conn = connect(index_path)
    try:
        rows = conn.execute(
            """
            SELECT run_id, path, created_at, event_count, root_digest
            FROM runs
            ORDER BY created_at ASC, run_id ASC
            """
        ).fetchall()
        return [
            RunIndexRow(
                run_id=r[0],
                path=r[1],
                created_at=r[2],
                event_count=int(r[3]),
                root_digest=r[4],
            )
            for r in rows
        ]
    finally:
        conn.close()


def resolve_run_dir(run_id: str, *, runs_dir: Path, index_path: Path) -> Path:
    """Resolve a run directory from the index, falling back to runs_dir/run_id."""
    for row in list_runs(index_path):
        if row.run_id == run_id:
            return Path(row.path)
    candidate = Path(runs_dir) / run_id
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"unknown run_id: {run_id}")
