"""SQLite-backed tracking of processed Gmail messages."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional


class State:
    """Tracks which Gmail messages have already been processed.

    Records are only written in apply mode after a successful Gmail
    modification. Dry-run results are never persisted.
    """

    def __init__(self, db_path: str = "state.db") -> None:
        """Open (or create) the SQLite database at ``db_path``."""
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the ``processed_messages`` table if it does not exist."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_messages (
                gmail_id TEXT PRIMARY KEY,
                thread_id TEXT,
                category TEXT,
                confidence REAL,
                should_archive INTEGER,
                reason TEXT,
                processed_at TEXT
            )
            """
        )
        self._conn.commit()

    def is_processed(self, gmail_id: str) -> bool:
        """Return ``True`` if ``gmail_id`` has already been recorded."""
        cur = self._conn.execute(
            "SELECT 1 FROM processed_messages WHERE gmail_id = ? LIMIT 1",
            (gmail_id,),
        )
        return cur.fetchone() is not None

    def record(
        self,
        gmail_id: str,
        thread_id: str,
        category: str,
        confidence: float,
        should_archive: bool,
        reason: str,
        processed_at: Optional[str] = None,
    ) -> None:
        """Persist the classification result for a processed message."""
        if processed_at is None:
            processed_at = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO processed_messages
                (gmail_id, thread_id, category, confidence,
                 should_archive, reason, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gmail_id,
                thread_id,
                category,
                float(confidence),
                1 if should_archive else 0,
                reason,
                processed_at,
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> "State":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
