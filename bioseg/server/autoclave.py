"""Simulated Autoclave Sterilization Cycle for BioSeg Twin.

Honest labelling: "temperature trace demo (simulated)"
Simulates a medical autoclave temperature curve, computes continuous F0 lethality,
and emits autoclave_tick events every 5s and an autoclave_result event at cycle completion.

F0 Sterilization Lethality Formula:
----------------------------------
F0 = Σ 10^((T - 121.0) / 10.0) × Δt
Where:
    T = Chamber temperature in °C at each interval
    T_ref = 121.0 °C (reference saturated steam sterilization temperature)
    z = 10.0 °C (thermal destruction resistance coefficient for Geobacillus stearothermophilus spores)
    Δt = Interval time in minutes (Δt_seconds / 60.0)
    Threshold F0 = 15.0 minutes (standard pharmacopeial criteria for complete sterility release)

Phases:
1. 'ramp': 25°C -> 121°C over 5 minutes (300s) (or 25°C -> 115°C if force_fail)
2. 'hold': 121°C for 15 minutes (900s) (or 115°C if force_fail)
3. 'cool': 121°C -> 60°C over 5 minutes (300s)
Total cycle duration: 1500 seconds (25 minutes).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import math
from typing import Any, Awaitable, Callable, Dict, Optional, Union

logger = logging.getLogger("bioseg.server.autoclave")

LABEL_SIMULATED: str = "temperature trace demo (simulated)"
THRESHOLD_F0: float = 15.0
RAMP_DURATION_S: float = 300.0    # 5 min
HOLD_DURATION_S: float = 900.0    # 15 min
COOL_DURATION_S: float = 300.0    # 5 min
TOTAL_CYCLE_S: float = RAMP_DURATION_S + HOLD_DURATION_S + COOL_DURATION_S  # 1500s (25 min)


class AutoclaveSimulator:
    """Asyncio task-based autoclave cycle simulator."""

    def __init__(
        self,
        event_callback: Optional[
            Callable[[Dict[str, Any]], Union[None, Awaitable[None]]]
        ] = None,
        tick_interval_s: float = 5.0,
    ) -> None:
        """Initialize simulator.

        Args:
            event_callback: Sync or async callable invoked for every emitted event.
            tick_interval_s: Simulation step size in seconds (default 5.0).
        """
        self.event_callback = event_callback
        self.tick_interval_s = tick_interval_s
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._force_fail: bool = False

        # Current telemetry state
        self._t_c: float = 25.0
        self._elapsed_s: float = 0.0
        self._f0: float = 0.0
        self._phase: str = "idle"
        self._last_result: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self._running

    def get_current_status(self) -> Dict[str, Any]:
        """Return snapshot of current autoclave state."""
        return {
            "running": self._running,
            "phase": self._phase,
            "t_c": round(self._t_c, 2),
            "elapsed_s": int(self._elapsed_s),
            "total_s": int(TOTAL_CYCLE_S),
            "f0": round(self._f0, 2),
            "threshold_f0": THRESHOLD_F0,
            "force_fail": self._force_fail,
            "last_result": self._last_result,
            "label": LABEL_SIMULATED,
        }

    async def _emit(self, event_dict: Dict[str, Any]) -> None:
        """Dispatch event to callback if configured."""
        if self.event_callback is not None:
            try:
                res = self.event_callback(event_dict)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                logger.error(f"Error in autoclave event callback: {exc}")

    def _compute_temperature(self, elapsed_s: float, force_fail: bool) -> tuple[float, str]:
        """Compute temperature and phase for a given elapsed time."""
        target_hold_temp = 115.0 if force_fail else 121.2

        if elapsed_s < RAMP_DURATION_S:
            # Phase: RAMP (25°C -> target)
            phase = "ramp"
            fraction = elapsed_s / RAMP_DURATION_S
            t_c = 25.0 + (target_hold_temp - 25.0) * fraction
        elif elapsed_s < (RAMP_DURATION_S + HOLD_DURATION_S):
            # Phase: HOLD (target temperature with minor thermal fluctuations)
            phase = "hold"
            # Small sinusoidal ripple for realistic thermal inertia
            ripple = 0.25 * math.sin(elapsed_s / 25.0)
            t_c = target_hold_temp + ripple
        elif elapsed_s <= TOTAL_CYCLE_S:
            # Phase: COOL (target -> 60°C)
            phase = "cool"
            cool_elapsed = elapsed_s - (RAMP_DURATION_S + HOLD_DURATION_S)
            fraction = cool_elapsed / COOL_DURATION_S
            t_c = target_hold_temp - (target_hold_temp - 60.0) * fraction
        else:
            phase = "complete"
            t_c = 60.0

        return t_c, phase

    async def run_cycle(
        self,
        force_fail: bool = False,
        speed_multiplier: float = 1.0,
    ) -> Dict[str, Any]:
        """Execute full simulated cycle.

        Args:
            force_fail: When True, hold temperature is limited to 115°C, yielding F0 < 15.0 (FAIL).
            speed_multiplier: Speed up factor (e.g. 50.0 for fast testing, 1.0 for real-time).

        Returns:
            autoclave_result event dict.
        """
        self._running = True
        self._force_fail = force_fail
        self._elapsed_s = 0.0
        self._f0 = 0.0
        self._last_result = None

        dt_sim = self.tick_interval_s  # simulation advance per step (5.0s)
        dt_sleep = dt_sim / max(0.001, speed_multiplier)  # real sleep per step

        logger.info(
            f"Starting autoclave cycle (force_fail={force_fail}, speed={speed_multiplier}x)"
        )

        try:
            while self._elapsed_s <= TOTAL_CYCLE_S and self._running:
                t_c, phase = self._compute_temperature(self._elapsed_s, force_fail)
                self._t_c = t_c
                self._phase = phase

                # F0 lethality accumulation formula:
                # F0 = Σ 10^((T - 121.0) / 10.0) * Δt_minutes
                dt_minutes = dt_sim / 60.0
                lethality_rate = 10.0 ** ((t_c - 121.0) / 10.0)
                self._f0 += lethality_rate * dt_minutes

                tick_event = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "autoclave_tick",
                    "t_c": round(self._t_c, 2),
                    "elapsed_s": int(self._elapsed_s),
                    "f0": round(self._f0, 2),
                    "phase": self._phase,
                    "label": LABEL_SIMULATED,
                }
                await self._emit(tick_event)

                await asyncio.sleep(dt_sleep)
                self._elapsed_s += dt_sim

            # Determine final PASS/FAIL result
            result_str = "PASS" if self._f0 >= THRESHOLD_F0 else "FAIL"
            self._last_result = result_str
            self._phase = "complete"

            result_event = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "autoclave_result",
                "f0": round(self._f0, 2),
                "result": result_str,
                "threshold_f0": THRESHOLD_F0,
                "label": LABEL_SIMULATED,
            }
            await self._emit(result_event)
            logger.info(f"Autoclave cycle finished with result={result_str}, F0={self._f0:.2f}")
            return result_event

        except asyncio.CancelledError:
            self._phase = "cancelled"
            logger.warning("Autoclave cycle cancelled.")
            raise
        finally:
            self._running = False

    def start(
        self,
        force_fail: bool = False,
        speed_multiplier: float = 1.0,
    ) -> asyncio.Task:
        """Start the simulated cycle in the background as an asyncio Task."""
        if self._running and self._task and not self._task.done():
            self._task.cancel()

        loop = asyncio.get_event_loop()
        self._task = loop.create_task(
            self.run_cycle(force_fail=force_fail, speed_multiplier=speed_multiplier)
        )
        return self._task

    def stop(self) -> None:
        """Stop any active simulation task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
