"""SQLite and JSONL persistence layer for BioSeg Twin.

Provides crash-resilient storage using SQLite in WAL mode and synchronized JSONL
session recording. Survives power loss gracefully through WAL journaling and
frequent fsync flushes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("bioseg.server.db")


class Database:
    """Thread-safe SQLite database manager with WAL mode and JSONL streaming."""

    def __init__(
        self,
        db_path: Union[str, Path] = "bioseg/data/bioseg.db",
        sessions_dir: Union[str, Path] = "bioseg/data/sessions",
    ) -> None:
        self.db_path = Path(db_path)
        self.sessions_dir = Path(sessions_dir)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._session_id: Optional[str] = None
        self._session_file = None

        self._ensure_directories()
        self._init_sqlite()

    def _ensure_directories(self) -> None:
        """Create required data directories if they do not exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _init_sqlite(self) -> None:
        """Initialize SQLite database connection and create tables."""
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            isolation_level=None,  # Autocommit mode for granular control
        )
        self._conn.row_factory = sqlite3.Row

        with self._lock:
            # Enable WAL mode for high concurrency and crash resilience
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")

            # Table: events (id, ts, event_type, payload JSON)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload JSON NOT NULL
                );
                """
            )

            # Table: ledger (mirrors ledger.py rows)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger (
                    index_num INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    hash TEXT NOT NULL
                );
                """
            )

            # Table: items (item_id, class, route, status)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    item_id TEXT PRIMARY KEY,
                    class TEXT,
                    route TEXT,
                    status TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )

            # Table: batches (batch_id, status)
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def _get_or_create_session(self, first_event_ts: str) -> str:
        """Establish session ID and open JSONL session log file."""
        if self._session_id is not None:
            return self._session_id

        # Format session ID: e.g. "2026-10-14T09-00-00"
        try:
            clean_ts = first_event_ts.replace(":", "-").split(".")[0]
            # Ensure it starts with ISO date format
            if len(clean_ts) >= 19:
                session_id = clean_ts[:19]
            else:
                session_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        except Exception:
            session_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")

        self._session_id = session_id
        session_file_path = self.sessions_dir / f"{session_id}.jsonl"
        self._session_file = open(session_file_path, "a", encoding="utf-8")
        logger.info(f"Initialized session log: {session_file_path}")
        return self._session_id

    def save_event(self, event: Dict[str, Any]) -> int:
        """Persist event to SQLite and append to active session JSONL file.

        Args:
            event: Full event dictionary.

        Returns:
            The inserted event ID.
        """
        ts = event.get("ts", datetime.now(timezone.utc).isoformat())
        event_type = event.get("event", "unknown")
        payload_str = json.dumps(event, separators=(",", ":"))

        with self._lock:
            # 1. SQLite write
            cursor = self._conn.execute(
                "INSERT INTO events (ts, event_type, payload) VALUES (?, ?, ?);",
                (ts, event_type, payload_str),
            )
            event_id = cursor.lastrowid

            # 2. JSONL stream write with fsync
            self._get_or_create_session(ts)
            if self._session_file:
                self._session_file.write(payload_str + "\n")
                self._session_file.flush()
                try:
                    os.fsync(self._session_file.fileno())
                except OSError:
                    pass

            return event_id

    def save_ledger_row(self, row: Dict[str, Any]) -> None:
        """Persist a ledger chain row into the ledger table."""
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO ledger (
                    index_num, ts, event_type, payload_json, payload_hash, prev_hash, hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    row["index"],
                    row["ts"],
                    row["event_type"],
                    row["payload_json"],
                    row["payload_hash"],
                    row["prev_hash"],
                    row["hash"],
                ),
            )

    def upsert_item(
        self,
        item_id: str,
        item_class: str = "",
        route: str = "",
        status: str = "",
    ) -> None:
        """Update or insert an item tracking record."""
        now_ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO items (item_id, class, route, status, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    class = CASE WHEN ? != '' THEN ? ELSE items.class END,
                    route = CASE WHEN ? != '' THEN ? ELSE items.route END,
                    status = CASE WHEN ? != '' THEN ? ELSE items.status END,
                    updated_at = ?;
                """,
                (
                    item_id, item_class, route, status, now_ts,
                    item_class, item_class,
                    route, route,
                    status, status,
                    now_ts,
                ),
            )

    def upsert_batch(self, batch_id: str, status: str) -> None:
        """Update or insert a batch tracking record."""
        now_ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO batches (batch_id, status, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at;
                """,
                (batch_id, status, now_ts),
            )

    def get_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve recent events from the database."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, ts, event_type, payload FROM events ORDER BY id DESC LIMIT ?;",
                (limit,),
            )
            rows = cursor.fetchall()
            result = []
            for r in reversed(rows):
                try:
                    payload = json.loads(r["payload"])
                except Exception:
                    payload = {}
                result.append({
                    "id": r["id"],
                    "ts": r["ts"],
                    "event_type": r["event_type"],
                    "payload": payload,
                })
            return result

    def get_all_ledger_rows(self) -> List[Dict[str, Any]]:
        """Retrieve all rows from the ledger table in index order."""
        with self._lock:
            cursor = self._conn.execute(
                """
                SELECT index_num, ts, event_type, payload_json, payload_hash, prev_hash, hash
                FROM ledger ORDER BY index_num ASC;
                """
            )
            rows = cursor.fetchall()
            return [
                {
                    "index": r["index_num"],
                    "ts": r["ts"],
                    "event_type": r["event_type"],
                    "payload_json": r["payload_json"],
                    "payload_hash": r["payload_hash"],
                    "prev_hash": r["prev_hash"],
                    "hash": r["hash"],
                }
                for r in rows
            ]

    def get_items(self) -> List[Dict[str, Any]]:
        """Retrieve all tracked items."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT item_id, class, route, status, updated_at FROM items ORDER BY updated_at ASC;"
            )
            return [
                {
                    "item_id": r["item_id"],
                    "class": r["class"],
                    "route": r["route"],
                    "status": r["status"],
                    "updated_at": r["updated_at"],
                }
                for r in cursor.fetchall()
            ]

    def get_batch(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a batch record by ID."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT batch_id, status, updated_at FROM batches WHERE batch_id = ?;",
                (batch_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "batch_id": row["batch_id"],
                "status": row["status"],
                "updated_at": row["updated_at"],
            }

    def get_session_id(self) -> Optional[str]:
        """Return the current active session ID."""
        with self._lock:
            return self._session_id

    def close(self) -> None:
        """Close SQLite connection and flush JSONL file handle."""
        with self._lock:
            if self._session_file and not self._session_file.closed:
                self._session_file.flush()
                self._session_file.close()
                self._session_file = None
            if self._conn:
                self._conn.close()
                self._conn = None
