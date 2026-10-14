"""Append-only cryptographic hash chain for BioSeg Twin.

Maintains a tamper-evident event log where each block is linked to the previous
block via sha256(prev_hash + payload_hash). Genesis row (index 0) has
prev_hash = "0" * 64. Thread-safe using threading.Lock.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import threading
from typing import Any, Dict, List, Optional, Union


def canonical_json(payload: Union[Dict[str, Any], list, str, int, float, bool]) -> str:
    """Serialize payload to deterministic canonical JSON with sorted keys."""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class Ledger:
    """Thread-safe append-only hash chain ledger."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chain: List[Dict[str, Any]] = []

    def append(
        self,
        event_type: str,
        payload: Union[Dict[str, Any], str],
        ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append an event to the hash chain and return the new row.

        Args:
            event_type: The type of event (e.g. 'classified', 'routed', 'override').
            payload: The event payload as dict or JSON string.
            ts: Optional ISO timestamp. Defaults to current UTC time.

        Returns:
            Dict containing the newly created chain row:
            {index, ts, event_type, payload_json, payload_hash, prev_hash, hash}
        """
        with self._lock:
            index = len(self._chain)
            if ts is None:
                ts = datetime.now(timezone.utc).isoformat()

            payload_json = canonical_json(payload)
            payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

            if index == 0:
                prev_hash = "0" * 64
            else:
                prev_hash = self._chain[-1]["hash"]

            combined = prev_hash + payload_hash
            row_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()

            row: Dict[str, Any] = {
                "index": index,
                "ts": ts,
                "event_type": event_type,
                "payload_json": payload_json,
                "payload_hash": payload_hash,
                "prev_hash": prev_hash,
                "hash": row_hash,
            }
            self._chain.append(row)
            return dict(row)

    def verify(self) -> Dict[str, Any]:
        """Recompute and verify the entire chain from genesis.

        Returns:
            {"valid": True, "broken_at": None} if intact, or
            {"valid": False, "broken_at": index} if corrupted.
        """
        with self._lock:
            if not self._chain:
                return {"valid": True, "broken_at": None}

            for i, row in enumerate(self._chain):
                if row.get("index") != i:
                    return {"valid": False, "broken_at": i}

                expected_prev = "0" * 64 if i == 0 else self._chain[i - 1]["hash"]
                if row.get("prev_hash") != expected_prev:
                    return {"valid": False, "broken_at": i}

                payload_json = row.get("payload_json", "")
                recomputed_payload_hash = hashlib.sha256(
                    payload_json.encode("utf-8")
                ).hexdigest()
                if row.get("payload_hash") != recomputed_payload_hash:
                    return {"valid": False, "broken_at": i}

                combined = expected_prev + recomputed_payload_hash
                recomputed_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
                if row.get("hash") != recomputed_hash:
                    return {"valid": False, "broken_at": i}

            return {"valid": True, "broken_at": None}

    def get_chain(self) -> List[Dict[str, Any]]:
        """Return a copy of all rows currently in the chain."""
        with self._lock:
            return [dict(row) for row in self._chain]

    def get_last_row(self) -> Optional[Dict[str, Any]]:
        """Return the most recent row, or None if chain is empty."""
        with self._lock:
            if not self._chain:
                return None
            return dict(self._chain[-1])

    def load_rows(self, rows: List[Dict[str, Any]]) -> None:
        """Replace internal chain with existing rows (e.g. from database reload)."""
        with self._lock:
            self._chain = [dict(row) for row in rows]

    def tamper_row(self, index: int, new_payload: Union[Dict[str, Any], str]) -> None:
        """Directly mutate payload_json of a row to simulate tampering for testing."""
        with self._lock:
            if 0 <= index < len(self._chain):
                self._chain[index]["payload_json"] = canonical_json(new_payload)

    def __len__(self) -> int:
        with self._lock:
            return len(self._chain)
