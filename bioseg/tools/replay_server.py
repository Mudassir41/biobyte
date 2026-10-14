"""
BioSeg Twin — Replay Server
Plays back recorded session JSONL events over WebSocket with authentic timing,
injects 'replay: true' badges, and serves reconstructed state and ledger endpoints.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import datetime
import hashlib
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bioseg.replay")


def parse_iso_timestamp(ts_str: str) -> datetime.datetime:
    """Parse ISO8601 timestamp string into datetime."""
    clean_ts = ts_str.strip()
    if clean_ts.endswith("Z"):
        clean_ts = clean_ts[:-1] + "+00:00"
    return datetime.datetime.fromisoformat(clean_ts)


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts."""

    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, data: Dict[str, Any]) -> None:
        dead: List[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(data)
            except Exception:
                dead.append(connection)
        for conn in dead:
            self.disconnect(conn)


class ReplayEngine:
    """Handles reading events, timing calculation, state reconstruction, and broadcasting."""

    def __init__(
        self,
        session_file: Path,
        speed: float = 1.0,
        loop: bool = True,
    ) -> None:
        self.session_file = Path(session_file).resolve()
        self.speed = max(float(speed), 0.001)
        self.loop = loop
        self.session_id = self.session_file.stem
        self.raw_events: List[Dict[str, Any]] = []
        self.current_index: int = 0
        self.is_running: bool = False
        self.manager = ConnectionManager()
        self._load_events()

        # Reconstructed state
        self.current_state: str = "IDLE"
        self.last_event: Optional[Dict[str, Any]] = None
        self.last_classified: Optional[Dict[str, Any]] = None
        self.current_flap: str = "centre"
        self.bins: Dict[str, int] = {"yellow": 0, "red": 0, "hold": 0, "total": 0}
        self.held: bool = False
        self.held_item: Optional[Dict[str, Any]] = None
        self.held_reason: Optional[str] = None
        self.autoclave: Dict[str, Any] = {
            "t_c": 20.0,
            "elapsed_s": 0,
            "f0": 0.0,
            "phase": "IDLE",
            "result": None,
            "threshold_f0": 15.0,
            "release_blocked": False,
        }
        self.last_heartbeat: Optional[Dict[str, Any]] = None
        self.ledger: List[Dict[str, Any]] = []

    def _load_events(self) -> None:
        if not self.session_file.exists():
            raise FileNotFoundError(f"Session file not found: {self.session_file}")

        self.raw_events = []
        with open(self.session_file, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                clean = line.strip()
                if not clean:
                    continue
                try:
                    payload = json.loads(clean)
                    self.raw_events.append(payload)
                except json.JSONDecodeError as e:
                    logger.warning(f"Error parsing JSON on line {line_no}: {e}")

        logger.info(f"Loaded {len(self.raw_events)} events from {self.session_file}")

    def reset_state(self) -> None:
        """Reset state for looping."""
        self.current_state = "IDLE"
        self.last_event = None
        self.last_classified = None
        self.current_flap = "centre"
        self.bins = {"yellow": 0, "red": 0, "hold": 0, "total": 0}
        self.held = False
        self.held_item = None
        self.held_reason = None
        self.autoclave = {
            "t_c": 20.0,
            "elapsed_s": 0,
            "f0": 0.0,
            "phase": "IDLE",
            "result": None,
            "threshold_f0": 15.0,
            "release_blocked": False,
        }
        self.last_heartbeat = None
        self.ledger = []
        self.current_index = 0

    def apply_event_to_state(self, ev: Dict[str, Any]) -> None:
        """Update reconstructed state based on event type."""
        self.last_event = ev
        etype = ev.get("event")

        if etype == "state_change":
            new_state = ev.get("to", self.current_state)
            self.current_state = new_state
            if new_state == "RELEASE_BLOCKED":
                self.autoclave["release_blocked"] = True
            elif new_state == "HELD":
                self.held = True
            elif new_state in ("IDLE", "ROUTING", "CLASSIFYING"):
                if self.held and new_state == "IDLE":
                    self.held = False

        elif etype == "trigger":
            self.current_state = "TRIGGERED"

        elif etype == "classified":
            self.last_classified = ev
            if "flap" in ev:
                self.current_flap = ev["flap"]

        elif etype == "routed":
            flap = ev.get("flap", "centre")
            self.current_flap = flap
            if flap == "left":
                self.bins["yellow"] += 1
            elif flap == "right":
                self.bins["red"] += 1
            elif flap == "centre":
                self.bins["hold"] += 1
            self.bins["total"] += 1

        elif etype == "held":
            self.held = True
            self.held_item = ev
            self.held_reason = ev.get("reason")
            self.current_flap = "centre"
            self.current_state = "HELD"

        elif etype == "override":
            self.held = False
            self.held_reason = None

        elif etype == "autoclave_tick":
            self.autoclave["t_c"] = ev.get("t_c", self.autoclave["t_c"])
            self.autoclave["elapsed_s"] = ev.get("elapsed_s", self.autoclave["elapsed_s"])
            self.autoclave["f0"] = ev.get("f0", self.autoclave["f0"])
            self.autoclave["phase"] = ev.get("phase", self.autoclave["phase"])

        elif etype == "autoclave_result":
            res = ev.get("result")
            self.autoclave["result"] = res
            self.autoclave["f0"] = ev.get("f0", self.autoclave["f0"])
            self.autoclave["threshold_f0"] = ev.get("threshold_f0", 15.0)
            if res == "FAIL":
                self.autoclave["release_blocked"] = True

        elif etype == "heartbeat":
            self.last_heartbeat = ev

        elif etype == "ledger_append":
            self.ledger.append(ev)

    def get_state(self) -> Dict[str, Any]:
        """Return state snapshot reconstructed from replayed events."""
        return {
            "replay": True,
            "session_id": self.session_id,
            "state": self.current_state,
            "current_state": self.current_state,
            "last_event": self.last_event,
            "last_classified": self.last_classified,
            "flap": self.current_flap,
            "bins": self.bins,
            "held": self.held,
            "held_item": self.held_item,
            "held_reason": self.held_reason,
            "autoclave": self.autoclave,
            "heartbeat": self.last_heartbeat,
            "current_index": self.current_index,
            "total_events": len(self.raw_events),
            "speed": self.speed,
            "loop": self.loop,
        }

    def get_ledger(self) -> Dict[str, Any]:
        """Return reconstructed ledger up to current replay position."""
        return {
            "replay": True,
            "count": len(self.ledger),
            "entries": self.ledger,
            "ledger": self.ledger,
            "valid": True,
        }

    def verify_ledger(self) -> Dict[str, Any]:
        """Verify hash chain integrity of the ledger entries."""
        if not self.ledger:
            return {
                "verified": True,
                "status": "VALID",
                "chain_length": 0,
                "genesis_hash": None,
                "latest_hash": None,
                "replay": True,
                "errors": [],
            }

        errors: List[str] = []
        broken_at: Optional[int] = None
        for i, entry in enumerate(self.ledger):
            # Check chained prev_hash
            if i > 0:
                expected_prev = self.ledger[i - 1].get("hash")
                actual_prev = entry.get("prev_hash")
                if expected_prev != actual_prev:
                    if broken_at is None:
                        broken_at = entry.get("index", i)
                    errors.append(
                        f"Chain broken at index {entry.get('index')}: prev_hash {actual_prev} != expected {expected_prev}"
                    )

            # Check self-hash computation: supports both sha256(index:prev:pay) and sha256(prev+pay)
            idx = entry.get("index")
            prev_h = entry.get("prev_hash")
            pay_h = entry.get("payload_hash")
            recorded_h = entry.get("hash")

            if prev_h is not None and pay_h is not None:
                candidates = [hashlib.sha256(f"{prev_h}{pay_h}".encode("utf-8")).hexdigest()]
                if idx is not None:
                    candidates.append(hashlib.sha256(f"{idx}:{prev_h}:{pay_h}".encode("utf-8")).hexdigest())
                if recorded_h not in candidates:
                    if broken_at is None:
                        broken_at = entry.get("index", i)
                    errors.append(
                        f"Hash mismatch at index {idx}: recorded {recorded_h} not matching expected hash"
                    )

        is_valid = len(errors) == 0
        return {
            "verified": is_valid,
            "valid": is_valid,
            "broken_at": broken_at,
            "status": "VALID" if is_valid else "INVALID",
            "chain_length": len(self.ledger),
            "genesis_hash": self.ledger[0].get("hash"),
            "latest_hash": self.ledger[-1].get("hash"),
            "replay": True,
            "errors": errors,
        }

    async def run(self) -> None:
        """Main async playback loop."""
        self.is_running = True
        logger.info(f"Replay loop started (speed={self.speed}x, loop={self.loop})")

        while self.is_running:
            self.reset_state()
            total = len(self.raw_events)

            for i in range(total):
                if not self.is_running:
                    break

                self.current_index = i + 1
                raw_ev = self.raw_events[i]
                
                # Clone event and inject 'replay': True
                event_to_send = copy.deepcopy(raw_ev)
                event_to_send["replay"] = True

                # Apply event to local reconstructed state & ledger
                self.apply_event_to_state(event_to_send)

                # Broadcast to connected WebSocket clients
                await self.manager.broadcast(event_to_send)

                # Calculate timing delta to next event
                if i < total - 1:
                    try:
                        curr_ts = parse_iso_timestamp(raw_ev["ts"])
                        next_ts = parse_iso_timestamp(self.raw_events[i + 1]["ts"])
                        delta_s = (next_ts - curr_ts).total_seconds()
                        if delta_s < 0:
                            delta_s = 0.0
                    except Exception:
                        delta_s = 0.5

                    sleep_s = max(0.0, delta_s / self.speed)
                    if sleep_s > 0:
                        await asyncio.sleep(sleep_s)

            if not self.loop or not self.is_running:
                logger.info("Reached end of session (no-loop active or stopped).")
                break

            logger.info("Session complete. Looping again after 2.0s pause...")
            await asyncio.sleep(2.0 / self.speed)


def create_replay_app(
    session_file: Path,
    speed: float = 1.0,
    loop: bool = True,
    start_replay: bool = True,
) -> FastAPI:
    """FastAPI application factory for the replay server."""
    engine = ReplayEngine(session_file=session_file, speed=speed, loop=loop)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: launch replay loop in background task if requested
        replay_task = None
        if start_replay:
            replay_task = asyncio.create_task(engine.run())
        yield
        # Shutdown
        if replay_task is not None:
            engine.is_running = False
            replay_task.cancel()
            try:
                await replay_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="BioSeg Twin Replay Server",
        description="Replays recorded BioSeg telemetry events with original timing.",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Expose engine on app state
    app.state.engine = engine

    @app.get("/")
    async def get_root() -> Dict[str, Any]:
        return {
            "service": "BioSeg Twin Replay Server",
            "session_id": engine.session_id,
            "replay": True,
            "speed": engine.speed,
            "loop": engine.loop,
            "endpoints": [
                "/ws",
                "/state",
                "/ledger",
                "/ledger/verify",
                "/replay/{session_id}",
            ],
        }

    @app.get("/state")
    async def get_state() -> Dict[str, Any]:
        """Returns the current state reconstructed from replayed events."""
        return engine.get_state()

    @app.get("/ledger")
    async def get_ledger() -> Dict[str, Any]:
        """Returns the ledger entries rebuilt from replayed events."""
        return engine.get_ledger()

    @app.get("/ledger/verify")
    async def verify_ledger() -> Dict[str, Any]:
        """Verifies the hash chain integrity of the replayed ledger."""
        return engine.verify_ledger()

    @app.get("/replay/{session_id}")
    async def get_replay_session(session_id: str):
        """Serves the requested JSONL session file."""
        # 1. Match current engine session
        if session_id in (engine.session_id, engine.session_file.name):
            return FileResponse(engine.session_file, media_type="application/x-ndjson")

        # 2. Search search directories
        candidates = [
            Path(session_id),
            Path(f"{session_id}.jsonl"),
            Path("bioseg/data/sessions") / f"{session_id}.jsonl",
            Path("bioseg/data/sessions") / session_id,
            Path("data/sessions") / f"{session_id}.jsonl",
            Path("data/sessions") / session_id,
            Path("/home/mudassir/Projects/biobyte/bioseg/data/sessions") / f"{session_id}.jsonl",
            Path("/home/mudassir/Projects/biobyte/bioseg/data/sessions") / session_id,
        ]

        for p in candidates:
            if p.is_file():
                return FileResponse(p.resolve(), media_type="application/x-ndjson")

        raise HTTPException(status_code=404, detail=f"Session file '{session_id}' not found.")

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint serving replayed events with injected replay:true."""
        await engine.manager.connect(websocket)
        try:
            while True:
                # Keep socket alive and consume any incoming client messages
                await websocket.receive_text()
        except WebSocketDisconnect:
            engine.manager.disconnect(websocket)
        except Exception:
            engine.manager.disconnect(websocket)

    return app


def resolve_session_path(session_arg: Optional[str] = None) -> Path:
    """Find the session file from argument or default demo_synthetic.jsonl."""
    if session_arg:
        p = Path(session_arg)
        if p.is_file():
            return p.resolve()
        if not p.suffix:
            p_with_ext = p.with_suffix(".jsonl")
            if p_with_ext.is_file():
                return p_with_ext.resolve()

    # Search defaults
    defaults = [
        Path("/home/mudassir/Projects/biobyte/bioseg/data/sessions/demo_synthetic.jsonl"),
        Path("bioseg/data/sessions/demo_synthetic.jsonl"),
        Path("data/sessions/demo_synthetic.jsonl"),
    ]
    for d in defaults:
        if d.is_file():
            return d.resolve()

    if session_arg:
        return Path(session_arg).resolve()
    return Path("bioseg/data/sessions/demo_synthetic.jsonl").resolve()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bioseg.tools.replay_server",
        description="BioSeg Twin Replay Server — Replays JSONL events over WebSocket & REST.",
    )
    parser.add_argument(
        "session_file",
        nargs="?",
        default=None,
        help="Path to recorded session .jsonl file (defaults to demo_synthetic.jsonl)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Replay speed multiplier: 1.0 (real-time), 2.0 (2x fast), 0.5 (slow). Default: 1.0",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host address to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--no-loop",
        action="store_true",
        help="Stop replay after playing the session once (default: loop continuously)",
    )
    return parser.parse_args(argv)


# Module-level default application (loads demo_synthetic.jsonl for fast testing/mounting)
default_session = resolve_session_path()
if default_session.exists():
    app = create_replay_app(default_session, speed=1.0, loop=True)
else:
    app = FastAPI(title="BioSeg Replay Placeholder")


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    session_path = resolve_session_path(args.session_file)

    if not session_path.is_file():
        logger.error(f"Cannot find session file: {session_path}")
        sys.exit(1)

    logger.info(f"Starting BioSeg Replay Server on {args.host}:{args.port}")
    logger.info(f"Session File: {session_path}")
    logger.info(f"Speed: {args.speed}x | Loop: {not args.no_loop}")

    replay_app = create_replay_app(
        session_file=session_path,
        speed=args.speed,
        loop=not args.no_loop,
    )

    uvicorn.run(
        replay_app,
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
