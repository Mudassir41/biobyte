"""FastAPI Core Server for BioSeg Twin.

Provides REST and WebSocket endpoints for edge event ingestion, digital twin
synchronization, ledger verification, operator overrides, session replay,
MJPEG camera streaming, and continuous heartbeat broadcasts.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, AsyncGenerator, Dict, Optional, Set  # Any used by /demo/frame image path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from bioseg.server.autoclave import AutoclaveSimulator
from bioseg.server.db import Database
from bioseg.server.ledger import Ledger
from bioseg.server.state import BatchState, ItemState, StateManager

logger = logging.getLogger("bioseg.server.main")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# -----------------------------------------------------------------------------
# Global Singletons
# -----------------------------------------------------------------------------
SERVER_START_TIME: float = time.time()
DB_PATH = os.getenv("BIOSEG_DB_PATH", "bioseg/data/bioseg.db")
SESSIONS_DIR = os.getenv("BIOSEG_SESSIONS_DIR", "bioseg/data/sessions")
db = Database(db_path=DB_PATH, sessions_dir=SESSIONS_DIR)
ledger = Ledger()
_initial_rows = db.get_all_ledger_rows()
if _initial_rows:
    ledger.load_rows(_initial_rows)
state_manager = StateManager()
active_websockets: Set[WebSocket] = set()
ws_lock = asyncio.Lock()


async def broadcast_ws(event_dict: Dict[str, Any]) -> None:
    """Broadcast an event payload to all connected WebSocket clients."""
    async with ws_lock:
        if not active_websockets:
            return
        disconnected = set()
        for ws in active_websockets:
            try:
                await ws.send_json(event_dict)
            except Exception:
                disconnected.add(ws)
        active_websockets.difference_update(disconnected)


async def on_autoclave_event(event_dict: Dict[str, Any]) -> None:
    """Handle events emitted by the simulated autoclave cycle."""
    event_type = event_dict.get("event")
    db.save_event(event_dict)

    if event_type == "autoclave_result":
        result = event_dict.get("result", "PASS")
        f0 = event_dict.get("f0", 0.0)
        state_manager.record_autoclave_result(result, f0)

        # Append result to ledger
        row = ledger.append("autoclave_result", event_dict)
        db.save_ledger_row(row)

        ledger_evt = {
            "ts": row["ts"],
            "event": "ledger_append",
            "index": row["index"],
            "payload_hash": row["payload_hash"],
            "prev_hash": row["prev_hash"],
            "hash": row["hash"],
        }
        db.save_event(ledger_evt)
        await broadcast_ws(ledger_evt)

    await broadcast_ws(event_dict)


autoclave_sim = AutoclaveSimulator(event_callback=on_autoclave_event, tick_interval_s=5.0)
heartbeat_task: Optional[asyncio.Task] = None


async def heartbeat_loop() -> None:
    """Emit heartbeat events every 5 seconds per spec §3."""
    while True:
        try:
            await asyncio.sleep(5.0)
            uptime_s = round(time.time() - SERVER_START_TIME, 1)
            heartbeat_evt = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "heartbeat",
                "uptime_s": uptime_s,
                "model": "yolo11n-bioseg",
                "latency_ms": None,
            }
            await broadcast_ws(heartbeat_evt)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Error in heartbeat loop: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager: initial setup and graceful shutdown."""
    global heartbeat_task
    logger.info("Initializing BioSeg Twin Core Server...")

    # Load previously persisted ledger rows into memory
    persisted_rows = db.get_all_ledger_rows()
    if persisted_rows:
        ledger.load_rows(persisted_rows)
        logger.info(f"Loaded {len(persisted_rows)} ledger rows from SQLite.")

    # Start background heartbeat broadcaster
    heartbeat_task = asyncio.create_task(heartbeat_loop())

    yield

    logger.info("Shutting down BioSeg Twin Core Server...")
    if heartbeat_task and not heartbeat_task.done():
        heartbeat_task.cancel()
    autoclave_sim.stop()
    db.close()


# -----------------------------------------------------------------------------
# FastAPI Application Configuration
# -----------------------------------------------------------------------------
app = FastAPI(
    title="BioSeg Twin Server",
    description="Smart Biomedical Waste Segregation Digital Twin Core API",
    version="1.0.0",
    lifespan=lifespan,
)

# LAN-friendly CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Request Schemas
# -----------------------------------------------------------------------------
class OverrideRequest(BaseModel):
    item_id: str = Field(..., description="ID of held item being overridden")
    reason: str = Field(..., description="Operator justification for override")
    operator: Optional[str] = Field("demo", description="Operator identity")


class AutoclaveControlRequest(BaseModel):
    force_fail: bool = Field(False, description="Simulate under-temperature failure cycle")
    speed_multiplier: float = Field(1.0, description="Simulation acceleration factor")


DASHBOARD_PATH = Path(__file__).resolve().parent.parent / "web" / "dashboard.html"
_BATCH_ORDER = [
    BatchState.PHYSICAL_PASS.value,
    BatchState.BI_PENDING.value,
    BatchState.RELEASED.value,
]


# -----------------------------------------------------------------------------
# REST Endpoints
# -----------------------------------------------------------------------------
@app.get("/")
@app.get("/dashboard")
async def serve_dashboard() -> FileResponse:
    """The digital twin. One file, served from this process so the page, socket, and camera share an origin."""
    if not DASHBOARD_PATH.is_file():
        raise HTTPException(status_code=404, detail="dashboard.html is missing")
    return FileResponse(DASHBOARD_PATH)


@app.post("/batch/advance")
async def advance_batch() -> JSONResponse:
    """Move the batch one step. A failed autoclave makes RELEASED unreachable."""
    current = state_manager.get_batch_state()
    try:
        index = _BATCH_ORDER.index(current)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Unknown batch state {current}") from exc
    if index >= len(_BATCH_ORDER) - 1:
        return JSONResponse(
            status_code=409,
            content={
                "status": "blocked",
                "detail": "Batch is already released.",
                "batch": current,
                "release_blocked": state_manager.is_release_blocked(),
            },
        )
    nxt = _BATCH_ORDER[index + 1]
    try:
        event = state_manager.transition_batch(nxt)
    except ValueError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "status": "blocked",
                "detail": str(exc),
                "batch": state_manager.get_batch_state(),
                "release_blocked": True,
            },
        )
    db.save_event(event)
    await broadcast_ws(event)
    return JSONResponse({"status": "ok", "event": event, "release_blocked": state_manager.is_release_blocked()})


@app.post("/events")
async def receive_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest events from edge devices, validate, persist, and broadcast."""
    if "event" not in event:
        raise HTTPException(status_code=400, detail="Missing required 'event' field")

    if "ts" not in event:
        event["ts"] = datetime.now(timezone.utc).isoformat()

    event_type = event["event"]
    item_id = event.get("item_id")

    # Update item and batch FSM states based on event
    try:
        if event_type == "classified" and item_id:
            # Transition item from None/DETECTED -> CLASSIFIED
            current = state_manager.get_item_state(item_id)
            if current is None:
                state_manager.transition_item(item_id, ItemState.DETECTED.value)
            state_manager.transition_item(item_id, ItemState.CLASSIFIED.value)
            db.upsert_item(
                item_id=item_id,
                item_class=event.get("item_class", ""),
                route=event.get("route", ""),
                status="CLASSIFIED",
            )

        elif event_type == "routed" and item_id:
            state_manager.transition_item(item_id, ItemState.ROUTED.value)
            db.upsert_item(item_id=item_id, status="ROUTED")

        elif event_type == "held" and item_id:
            state_manager.transition_item(item_id, ItemState.HELD.value)
            db.upsert_item(item_id=item_id, status="HELD")

        elif event_type == "autoclave_result":
            state_manager.record_autoclave_result(
                event.get("result", "PASS"),
                event.get("f0", 0.0),
            )

    except ValueError as exc:
        logger.warning(f"FSM transition warning for {event_type}: {exc}")

    # Append event to tamper-evident ledger
    row = ledger.append(event_type=event_type, payload=event, ts=event["ts"])
    db.save_ledger_row(row)

    # Persist the primary event to SQLite and active session JSONL
    db.save_event(event)

    # Emit ledger_append notification event
    ledger_append_event = {
        "ts": row["ts"],
        "event": "ledger_append",
        "index": row["index"],
        "payload_hash": row["payload_hash"],
        "prev_hash": row["prev_hash"],
        "hash": row["hash"],
    }
    db.save_event(ledger_append_event)

    # Broadcast both over WebSocket to digital twin clients
    await broadcast_ws(event)
    await broadcast_ws(ledger_append_event)

    return {
        "status": "ok",
        "ledger_index": row["index"],
        "hash": row["hash"],
    }


@app.get("/state")
async def get_state() -> Dict[str, Any]:
    """Return full state snapshot for digital twin initialization."""
    return {
        "items": db.get_items(),
        "item_states": state_manager.get_all_items(),
        "batch": {
            "batch_id": state_manager.get_batch_id(),
            "status": state_manager.get_batch_state(),
        },
        "autoclave": autoclave_sim.get_current_status(),
        "release_blocked": state_manager.is_release_blocked(),
        "ledger_length": len(ledger),
        "server_time": datetime.now(timezone.utc).isoformat(),
        "uptime_s": round(time.time() - SERVER_START_TIME, 2),
    }


@app.get("/ledger")
async def get_ledger() -> list:
    """Return entire append-only ledger chain as JSON array."""
    return ledger.get_chain()


@app.get("/ledger/verify")
async def verify_ledger() -> Dict[str, Any]:
    """Recompute all SHA-256 hashes and verify ledger integrity."""
    return ledger.verify()


class DemoSampleRequest(BaseModel):
    """Drop a known photo through the real ONNX model. Does not open a laptop camera."""

    sample: str = Field(
        ...,
        description="gauze, glove, syringe, urine_bag, test_tube, glasses/hold (twin variety)",
    )


# Twin digital samples → val folder + expected route. Hardware props are cotton/mask/glove.
_DEMO_SAMPLES: Dict[str, tuple[str, str]] = {
    "gauze": ("gauze", "yellow"),
    "cotton": ("gauze", "yellow"),  # hardware alias: cotton reads as gauze
    "glove": ("glove", "red"),
    "syringe": ("syringe", "red"),
    "urine_bag": ("urine_bag", "red"),
    "test_tube": ("test_tube", "red"),
    "glasses": ("medical_glasses", "hold"),
    "hold": ("medical_glasses", "hold"),
}


def _demo_sample_image(sample: str) -> Path:
    """Pick a validation photo the model actually gets right for the twin demo."""
    from bioseg.edge.infer import ModelInferenceEngine, apply_fusion_rules, reset_disagreement_counter

    root = Path(__file__).resolve().parents[1] / "data" / "datasets" / "cls_data" / "val"
    key = sample.strip().lower()
    if key not in _DEMO_SAMPLES:
        raise HTTPException(
            status_code=400,
            detail="sample must be one of: " + ", ".join(sorted(_DEMO_SAMPLES)),
        )
    folder, want_route = _DEMO_SAMPLES[key]
    directory = root / folder
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail=f"No sample folder at {directory}")
    candidates = [
        path for path in sorted(directory.iterdir())
        if path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    ]
    if not candidates:
        raise HTTPException(status_code=404, detail=f"No image in {directory}")

    engine = ModelInferenceEngine()
    want_class = folder
    best_path = candidates[0]
    best_score = -1.0
    for path in candidates[:40]:
        reset_disagreement_counter()
        pred, conf = engine.predict(path)
        route, _flap, _reason = apply_fusion_rules(pred, conf, ir_present=True, consecutive_low_conf=0)
        if want_route == "hold":
            ok = route == "hold"
        else:
            ok = pred == want_class and route == want_route
        if ok and conf > best_score:
            best_path = path
            best_score = conf
            if conf >= 0.9:
                break
    if best_score < 0:
        logger.warning("No clean %s demo photo found; using %s", sample, best_path.name)
    return best_path


async def _ingest_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Same path as POST /events, for internal demo drops."""
    return await receive_event(event)


async def _classify_and_broadcast(
    image: Any,
    *,
    source: str,
    device: str = "twin",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """VisionOps-style closed loop: image in → real model → events out on the WebSocket."""
    from bioseg.edge.infer import classify_item, reset_disagreement_counter

    reset_disagreement_counter()
    trigger = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "trigger",
        "source": source,
        "device": device,
    }
    if extra:
        trigger.update(extra)
    await _ingest_event(trigger)

    classified = classify_item(image=image, ir_present=True, post=False)
    classified["device"] = device
    classified["source"] = source
    if extra:
        classified.update({k: v for k, v in extra.items() if k not in classified})
    await _ingest_event(classified)

    item_id = classified["item_id"]
    flap = classified.get("flap", "centre")
    if classified.get("route") == "hold" or flap == "centre":
        follow = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "held",
            "item_id": item_id,
            "reason": classified.get("reason", "hold"),
            "device": device,
        }
    else:
        follow = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "routed",
            "flap": flap,
            "item_id": item_id,
            "device": device,
        }
    await _ingest_event(follow)
    return {"status": "ok", "classified": classified, "follow": follow}


@app.post("/demo/sample")
async def demo_sample(req: DemoSampleRequest) -> Dict[str, Any]:
    """Run the real classifier on a stock photo and broadcast the result to the twin."""
    image_path = _demo_sample_image(req.sample)
    result = await _classify_and_broadcast(
        image_path,
        source="demo",
        extra={"sample": req.sample, "image": image_path.name},
    )
    result["sample"] = req.sample
    result["image"] = image_path.name
    return _demo_classify_response(result)


def _demo_classify_response(result: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten classified fields for demo clients (dashboard, curl)."""
    classified = result.get("classified") or {}
    result["route"] = classified.get("route")
    result["flap"] = classified.get("flap")
    result["class"] = classified.get("item_class")
    result["confidence"] = classified.get("confidence")
    result["reason"] = classified.get("reason")
    return result


_ALLOWED_UPLOAD_SUFFIX = {".jpg", ".jpeg", ".png", ".webp"}


@app.post("/demo/upload")
async def demo_upload(file: UploadFile = File(...)) -> Dict[str, Any]:
    """Classify an uploaded image with the same path as /demo/frame (no laptop webcam)."""
    import tempfile

    name = (file.filename or "upload.jpg").strip()
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_UPLOAD_SUFFIX:
        suffix = ".jpg"
    raw = await file.read()
    if len(raw) < 32:
        raise HTTPException(status_code=400, detail="Image file is too small or empty")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        path = Path(tmp.name)
    try:
        result = await _classify_and_broadcast(
            path,
            source="demo_upload",
            extra={"filename": name, "bytes": len(raw)},
        )
        result["filename"] = name
        return _demo_classify_response(result)
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


class DemoFrameRequest(BaseModel):
    """JPEG from the Godot chute camera, base64-encoded (HTTPRequest only sends strings)."""

    jpeg_b64: str
    kind: str = ""


@app.post("/demo/frame")
async def demo_frame(req: DemoFrameRequest) -> Dict[str, Any]:
    """Classify one JPEG from the Godot chute camera (VisionOps closed-loop style)."""
    import base64
    import tempfile

    try:
        raw = base64.b64decode(req.jpeg_b64, validate=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Bad jpeg_b64: {exc}") from exc
    if len(raw) < 32 or raw[:2] != b"\xff\xd8":
        raise HTTPException(status_code=400, detail="jpeg_b64 must decode to a JPEG")
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(raw)
        path = Path(tmp.name)
    try:
        return await _classify_and_broadcast(
            path,
            source="twin_cam",
            extra={"bytes": len(raw), "kind": req.kind},
        )
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@app.post("/override")
async def create_override(req: OverrideRequest) -> Dict[str, Any]:
    """Operator override for a held item, logged into hash chain."""
    override_event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "override",
        "item_id": req.item_id,
        "operator": req.operator or "demo",
        "reason": req.reason,
    }

    # Transition item to ROUTED if held
    try:
        current = state_manager.get_item_state(req.item_id)
        if current == ItemState.HELD.value:
            state_manager.transition_item(req.item_id, ItemState.ROUTED.value)
    except Exception as exc:
        logger.warning(f"Could not transition item on override: {exc}")

    db.upsert_item(item_id=req.item_id, status="OVERRIDDEN")

    row = ledger.append("override", override_event, ts=override_event["ts"])
    db.save_ledger_row(row)
    db.save_event(override_event)

    ledger_append_event = {
        "ts": row["ts"],
        "event": "ledger_append",
        "index": row["index"],
        "payload_hash": row["payload_hash"],
        "prev_hash": row["prev_hash"],
        "hash": row["hash"],
    }
    db.save_event(ledger_append_event)

    await broadcast_ws(override_event)
    await broadcast_ws(ledger_append_event)

    return {
        "status": "ok",
        "event": override_event,
        "ledger_index": row["index"],
        "hash": row["hash"],
    }


@app.get("/replay/{session_id}")
async def replay_session(session_id: str) -> StreamingResponse:
    """Stream recorded session JSONL as server-sent events."""
    sessions_dir = Path("bioseg/data/sessions")
    target_path = sessions_dir / f"{session_id}.jsonl"

    if not target_path.exists():
        # Fallback to direct name if .jsonl was provided in session_id
        target_path = sessions_dir / session_id
        if not target_path.exists():
            raise HTTPException(status_code=404, detail=f"Session log '{session_id}' not found")

    async def event_generator() -> AsyncGenerator[str, None]:
        with open(target_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    yield f"data: {stripped}\n\n"
                    await asyncio.sleep(0.01)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


_frame_lock = threading.Lock()
_latest_jpeg: Optional[bytes] = None


@app.post("/camera_frame")
async def camera_frame(request: Request) -> Dict[str, Any]:
    """Accept one JPEG from the Pi camera. This server never opens a local webcam."""
    global _latest_jpeg
    body = await request.body()
    if len(body) < 4:
        raise HTTPException(status_code=400, detail="Empty camera frame")
    with _frame_lock:
        _latest_jpeg = body
    return {"status": "ok", "bytes": len(body)}


@app.get("/video_feed")
async def video_feed() -> Response:
    """MJPEG of the latest Pi camera frame. 503 until the Pi has sent one."""
    with _frame_lock:
        if _latest_jpeg is None:
            return Response(status_code=503, content="Pi camera not connected")

    async def mjpeg_generator() -> AsyncGenerator[bytes, None]:
        last: Optional[bytes] = None
        while True:
            with _frame_lock:
                frame = _latest_jpeg
            if frame is not None and frame is not last:
                last = frame
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
            await asyncio.sleep(0.1)

    return StreamingResponse(
        mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.post("/autoclave/start")
async def start_autoclave(req: AutoclaveControlRequest) -> Dict[str, Any]:
    """Trigger the simulated autoclave cycle for demonstration."""
    task = autoclave_sim.start(
        force_fail=req.force_fail,
        speed_multiplier=req.speed_multiplier,
    )
    return {
        "status": "started",
        "force_fail": req.force_fail,
        "speed_multiplier": req.speed_multiplier,
    }


@app.post("/autoclave/stop")
async def stop_autoclave() -> Dict[str, Any]:
    """Halt active autoclave simulation."""
    autoclave_sim.stop()
    return {"status": "stopped"}


# -----------------------------------------------------------------------------
# WebSocket Endpoint
# -----------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Live telemetry stream for twin dashboards and recorders."""
    await websocket.accept()
    async with ws_lock:
        active_websockets.add(websocket)
    logger.info("New WebSocket client connected")

    try:
        while True:
            # Keep connection open and await any inbound client frames
            data = await websocket.receive_text()
            # Respond to ping or client queries if needed
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as exc:
        logger.warning(f"WebSocket client error: {exc}")
    finally:
        async with ws_lock:
            active_websockets.discard(websocket)


# Standalone runner
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bioseg.server.main:app", host="0.0.0.0", port=8000, reload=False)
