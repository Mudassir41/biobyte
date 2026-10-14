"""Finite State Machines (FSMs) for BioSeg Twin.

Implements two state machines:
1. Per-Item FSM: DETECTED -> CLASSIFIED -> ROUTED (also supports HELD -> ROUTED)
2. Batch FSM: PHYSICAL_PASS -> BI_PENDING -> RELEASED

Safety Invariant:
An autoclave_result event with result="FAIL" permanently and strictly blocks
transition to the RELEASED state, raising a ValueError on any attempt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import threading
from typing import Any, Dict, Optional


class ItemState(str, Enum):
    """Lifecycle states for individual waste items."""
    DETECTED = "DETECTED"
    CLASSIFIED = "CLASSIFIED"
    ROUTED = "ROUTED"
    HELD = "HELD"


class BatchState(str, Enum):
    """Lifecycle states for waste batches."""
    PHYSICAL_PASS = "PHYSICAL_PASS"
    BI_PENDING = "BI_PENDING"
    RELEASED = "RELEASED"


class StateManager:
    """Thread-safe state manager for item and batch state machines."""

    # Valid transitions: current_state -> set of valid target states
    ITEM_TRANSITIONS: Dict[Optional[str], set[str]] = {
        None: {ItemState.DETECTED.value},
        ItemState.DETECTED.value: {ItemState.CLASSIFIED.value},
        ItemState.CLASSIFIED.value: {ItemState.ROUTED.value, ItemState.HELD.value},
        ItemState.HELD.value: {ItemState.ROUTED.value},
        ItemState.ROUTED.value: set(),
    }

    BATCH_TRANSITIONS: Dict[str, set[str]] = {
        BatchState.PHYSICAL_PASS.value: {BatchState.BI_PENDING.value},
        BatchState.BI_PENDING.value: {BatchState.RELEASED.value},
        BatchState.RELEASED.value: set(),
    }

    def __init__(self, batch_id: str = "BATCH-001") -> None:
        self._lock = threading.Lock()
        self._batch_id = batch_id
        self._batch_state: str = BatchState.PHYSICAL_PASS.value
        self._item_states: Dict[str, str] = {}
        self._autoclave_failed: bool = False
        self._autoclave_f0: float = 0.0

    # -------------------------------------------------------------------------
    # Per-Item FSM
    # -------------------------------------------------------------------------
    def transition_item(self, item_id: str, new_state: str) -> Dict[str, Any]:
        """Advance an item through its lifecycle: DETECTED -> CLASSIFIED -> ROUTED.

        Args:
            item_id: Unique item identifier (e.g., 'ITEM-0001').
            new_state: Target state ('DETECTED', 'CLASSIFIED', 'ROUTED', or 'HELD').

        Returns:
            A state_change event dictionary:
            {"ts": ..., "event": "state_change", "item_id": ..., "from": ..., "to": ...}

        Raises:
            ValueError: If the transition is invalid.
        """
        with self._lock:
            current_state = self._item_states.get(item_id)
            valid_targets = self.ITEM_TRANSITIONS.get(current_state, set())

            if new_state not in valid_targets:
                raise ValueError(
                    f"Invalid item state transition for {item_id}: "
                    f"'{current_state}' -> '{new_state}'. "
                    f"Allowed transitions from '{current_state}': {sorted(valid_targets)}"
                )

            self._item_states[item_id] = new_state
            ts = datetime.now(timezone.utc).isoformat()
            return {
                "ts": ts,
                "event": "state_change",
                "item_id": item_id,
                "from": current_state,
                "to": new_state,
            }

    def get_item_state(self, item_id: str) -> Optional[str]:
        """Return the current state of an item, or None if unknown."""
        with self._lock:
            return self._item_states.get(item_id)

    def get_all_items(self) -> Dict[str, str]:
        """Return a copy of all current item states."""
        with self._lock:
            return dict(self._item_states)

    # -------------------------------------------------------------------------
    # Batch FSM
    # -------------------------------------------------------------------------
    def transition_batch(self, new_state: str) -> Dict[str, Any]:
        """Advance the batch state: PHYSICAL_PASS -> BI_PENDING -> RELEASED.

        Safety Rule:
        If autoclave_result was FAIL, transitioning to RELEASED is hard-blocked.

        Args:
            new_state: Target batch state.

        Returns:
            A state_change event dictionary:
            {"ts": ..., "event": "state_change", "batch_id": ..., "from": ..., "to": ...}

        Raises:
            ValueError: If autoclave failed and transition to RELEASED is attempted,
                        or if transition is otherwise invalid.
        """
        with self._lock:
            # Check hard safety block first
            if new_state == BatchState.RELEASED.value and self._autoclave_failed:
                raise ValueError(
                    f"Release blocked: autoclave cycle failed (F0={self._autoclave_f0:.2f} < 15.0). "
                    f"Batch {self._batch_id} is permanently blocked from RELEASED."
                )

            valid_targets = self.BATCH_TRANSITIONS.get(self._batch_state, set())
            if new_state not in valid_targets:
                raise ValueError(
                    f"Invalid batch state transition for {self._batch_id}: "
                    f"'{self._batch_state}' -> '{new_state}'. "
                    f"Allowed transitions from '{self._batch_state}': {sorted(valid_targets)}"
                )

            prev_state = self._batch_state
            self._batch_state = new_state
            ts = datetime.now(timezone.utc).isoformat()
            return {
                "ts": ts,
                "event": "state_change",
                "batch_id": self._batch_id,
                "from": prev_state,
                "to": new_state,
            }

    def record_autoclave_result(self, result: str, f0: float = 0.0) -> None:
        """Record autoclave cycle result. FAIL permanently blocks RELEASED.

        Args:
            result: 'PASS' or 'FAIL'.
            f0: Final computed F0 equivalent lethality value.
        """
        with self._lock:
            self._autoclave_f0 = f0
            if result.upper() == "FAIL":
                self._autoclave_failed = True
            elif result.upper() == "PASS":
                # Do not un-fail if it was previously failed
                pass

    def is_release_blocked(self) -> bool:
        """Return True if autoclave failure blocks batch release."""
        with self._lock:
            return self._autoclave_failed

    def get_batch_state(self) -> str:
        """Return the current batch state."""
        with self._lock:
            return self._batch_state

    def get_batch_id(self) -> str:
        """Return the current batch ID."""
        with self._lock:
            return self._batch_id

    def set_batch_id(self, batch_id: str) -> None:
        """Set active batch ID."""
        with self._lock:
            self._batch_id = batch_id

    def get_state_summary(self) -> Dict[str, Any]:
        """Return a summary of item states, batch state, and release block status."""
        with self._lock:
            return {
                "batch_id": self._batch_id,
                "batch_state": self._batch_state,
                "release_blocked": self._autoclave_failed,
                "autoclave_f0": self._autoclave_f0,
                "total_items": len(self._item_states),
                "items": dict(self._item_states),
            }
