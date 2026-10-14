"""Unit and integration tests for state machine, autoclave, db, and FastAPI server."""

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from bioseg.server.autoclave import AutoclaveSimulator
from bioseg.server.db import Database
from bioseg.server.main import app
from bioseg.server.state import BatchState, ItemState, StateManager


# -----------------------------------------------------------------------------
# State Machine Tests
# -----------------------------------------------------------------------------
def test_item_fsm_valid_lifecycle():
    """Item advances DETECTED -> CLASSIFIED -> ROUTED cleanly."""
    sm = StateManager()
    item_id = "ITEM-0001"

    ev1 = sm.transition_item(item_id, ItemState.DETECTED.value)
    assert ev1["from"] is None
    assert ev1["to"] == "DETECTED"
    assert sm.get_item_state(item_id) == "DETECTED"

    ev2 = sm.transition_item(item_id, ItemState.CLASSIFIED.value)
    assert ev2["from"] == "DETECTED"
    assert ev2["to"] == "CLASSIFIED"
    assert sm.get_item_state(item_id) == "CLASSIFIED"

    ev3 = sm.transition_item(item_id, ItemState.ROUTED.value)
    assert ev3["from"] == "CLASSIFIED"
    assert ev3["to"] == "ROUTED"
    assert sm.get_item_state(item_id) == "ROUTED"


def test_item_fsm_invalid_transition():
    """Item cannot jump directly from DETECTED to ROUTED or backtrack."""
    sm = StateManager()
    item_id = "ITEM-0002"

    sm.transition_item(item_id, ItemState.DETECTED.value)
    with pytest.raises(ValueError, match="Invalid item state transition"):
        sm.transition_item(item_id, ItemState.ROUTED.value)

    sm.transition_item(item_id, ItemState.CLASSIFIED.value)
    sm.transition_item(item_id, ItemState.ROUTED.value)
    with pytest.raises(ValueError, match="Invalid item state transition"):
        sm.transition_item(item_id, ItemState.CLASSIFIED.value)


def test_item_fsm_held_and_override():
    """Item can transition CLASSIFIED -> HELD -> ROUTED (override path)."""
    sm = StateManager()
    item_id = "ITEM-0003"

    sm.transition_item(item_id, ItemState.DETECTED.value)
    sm.transition_item(item_id, ItemState.CLASSIFIED.value)
    ev_held = sm.transition_item(item_id, ItemState.HELD.value)
    assert ev_held["to"] == "HELD"

    ev_routed = sm.transition_item(item_id, ItemState.ROUTED.value)
    assert ev_routed["from"] == "HELD"
    assert ev_routed["to"] == "ROUTED"


def test_batch_fsm_pass_path():
    """Normal batch lifecycle: PHYSICAL_PASS -> BI_PENDING -> RELEASED."""
    sm = StateManager(batch_id="BATCH-TEST")
    assert sm.get_batch_state() == BatchState.PHYSICAL_PASS.value

    ev1 = sm.transition_batch(BatchState.BI_PENDING.value)
    assert ev1["to"] == BatchState.BI_PENDING.value

    ev2 = sm.transition_batch(BatchState.RELEASED.value)
    assert ev2["to"] == BatchState.RELEASED.value
    assert sm.get_batch_state() == BatchState.RELEASED.value


def test_batch_fsm_autoclave_fail_blocks_released():
    """Autoclave FAIL permanently blocks RELEASED with a ValueError."""
    sm = StateManager(batch_id="BATCH-FAIL")
    sm.transition_batch(BatchState.BI_PENDING.value)

    # Autoclave reports failure
    sm.record_autoclave_result(result="FAIL", f0=3.8)
    assert sm.is_release_blocked() is True

    # Attempt to transition to RELEASED must raise ValueError
    with pytest.raises(ValueError, match="Release blocked"):
        sm.transition_batch(BatchState.RELEASED.value)


# -----------------------------------------------------------------------------
# Autoclave Simulation Tests
# -----------------------------------------------------------------------------
def test_autoclave_simulation_pass():
    """Fast-forward normal cycle completes with result PASS and F0 >= 15.0."""
    ticks = []
    results = []

    def handle_event(e):
        if e["event"] == "autoclave_tick":
            ticks.append(e)
        elif e["event"] == "autoclave_result":
            results.append(e)

    async def run():
        sim = AutoclaveSimulator(event_callback=handle_event, tick_interval_s=10.0)
        return await sim.run_cycle(force_fail=False, speed_multiplier=100000.0)

    res = asyncio.run(run())
    assert res["result"] == "PASS"
    assert res["f0"] >= 15.0
    assert len(ticks) > 0
    assert len(results) == 1


def test_autoclave_simulation_force_fail():
    """Force fail cycle completes with result FAIL and F0 < 15.0."""
    async def run():
        sim = AutoclaveSimulator(tick_interval_s=10.0)
        return await sim.run_cycle(force_fail=True, speed_multiplier=100000.0)

    res = asyncio.run(run())
    assert res["result"] == "FAIL"
    assert res["f0"] < 15.0


# -----------------------------------------------------------------------------
# Database Tests
# -----------------------------------------------------------------------------
def test_database_persistence():
    """Database writes events to SQLite and session JSONL with WAL enabled."""
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "test.db")
        sess_dir = os.path.join(tmpdir, "sessions")
        db = Database(db_path=db_path, sessions_dir=sess_dir)

        evt = {
            "ts": "2026-10-14T10:00:00.000+05:30",
            "event": "trigger",
            "source": "ir",
        }
        eid = db.save_event(evt)
        assert eid > 0

        # Verify event retrieval
        events = db.get_events(limit=10)
        assert len(events) == 1
        assert events[0]["event_type"] == "trigger"

        # Verify item upsert
        db.upsert_item("ITEM-9999", item_class="gauze", route="yellow", status="CLASSIFIED")
        items = db.get_items()
        assert any(i["item_id"] == "ITEM-9999" for i in items)

        # Verify session JSONL file exists
        session_id = db.get_session_id()
        assert session_id is not None
        jsonl_path = os.path.join(sess_dir, f"{session_id}.jsonl")
        assert os.path.exists(jsonl_path)
        with open(jsonl_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            assert len(lines) == 1

        db.close()
    finally:
        shutil.rmtree(tmpdir)


# -----------------------------------------------------------------------------
# FastAPI Integration Tests
# -----------------------------------------------------------------------------
def test_fastapi_endpoints():
    """Verify core server endpoints."""
    with TestClient(app) as client:
        # 1. State endpoint
        resp = client.get("/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "batch" in data
        assert "autoclave" in data

        # 2. Ingest event
        evt_payload = {
            "ts": "2026-10-14T12:00:00.000+05:30",
            "event": "classified",
            "item_id": "ITEM-8888",
            "item_class": "gauze",
            "confidence": 0.95,
            "model": "yolo11n-cls",
            "device": "pi",
            "route": "yellow",
            "flap": "left",
            "reason": "rule: class gauze -> yellow",
            "ledger_hash": "",
        }
        post_resp = client.post("/events", json=evt_payload)
        assert post_resp.status_code == 200
        post_data = post_resp.json()
        assert post_data["status"] == "ok"
        assert "ledger_index" in post_data
        assert "hash" in post_data

        # 3. Read ledger
        ledger_resp = client.get("/ledger")
        assert ledger_resp.status_code == 200
        chain = ledger_resp.json()
        assert len(chain) > 0

        # 4. Verify ledger
        verify_resp = client.get("/ledger/verify")
        assert verify_resp.status_code == 200
        assert verify_resp.json()["valid"] is True

        # 5. Operator override
        override_resp = client.post(
            "/override",
            json={
                "item_id": "ITEM-8888",
                "reason": "checked manual bag",
                "operator": "test_operator",
            },
        )
        assert override_resp.status_code == 200
        assert override_resp.json()["status"] == "ok"

        page = client.get("/")
        assert page.status_code == 200
        assert "Yellow" in page.text
        assert "HOLD TRAY" not in page.text
        assert "Not processed" in page.text
        assert "Simulate a failed cycle" not in page.text
        assert "laptop webcam" not in page.text

        from bioseg.server import main as srv

        empty = client.get("/video_feed")
        assert empty.status_code == 503
        posted = client.post("/camera_frame", content=b"\xff\xd8\xff\xd9", headers={"Content-Type": "image/jpeg"})
        assert posted.status_code == 200
        srv._latest_jpeg = None

        demo = client.post("/demo/sample", json={"sample": "gauze"})
        assert demo.status_code == 200
        body = demo.json()
        assert body["status"] == "ok"
        assert body["classified"]["event"] == "classified"
        assert body["classified"]["item_class"] in {"gauze", "glove", "medical_glasses", "syringe", "test_tube", "urine_bag", "unknown"}
        assert body.get("class") == body["classified"]["item_class"]
        assert body.get("route") == body["classified"]["route"]
        assert "Digital twin" in page.text
        assert "Model: gauze" in page.text
        assert "Drop a photo through the model" in page.text

        val_gauze = Path(__file__).resolve().parents[1] / "data" / "datasets" / "cls_data" / "val" / "gauze"
        sample_img = next(val_gauze.glob("*.jpeg"), None) or next(val_gauze.glob("*.jpg"), None)
        if sample_img is not None:
            with sample_img.open("rb") as img:
                up = client.post(
                    "/demo/upload",
                    files={"file": (sample_img.name, img, "image/jpeg")},
                )
            assert up.status_code == 200
            up_body = up.json()
            assert up_body["status"] == "ok"
            assert up_body["classified"]["event"] == "classified"
            assert "confidence" in up_body
            assert "flap" in up_body

        sm = srv.state_manager
        saved = (sm._batch_state, sm._autoclave_failed, sm._autoclave_f0)
        sm._batch_state = BatchState.PHYSICAL_PASS.value
        sm._autoclave_failed = False
        sm._autoclave_f0 = 0.0
        try:
            step = client.post("/batch/advance")
            assert step.status_code == 200
            assert step.json()["event"]["to"] == BatchState.BI_PENDING.value
            sm.record_autoclave_result("FAIL", 4.2)
            blocked = client.post("/batch/advance")
            assert blocked.status_code == 409
            assert blocked.json()["status"] == "blocked"
            assert sm.get_batch_state() == BatchState.BI_PENDING.value
        finally:
            sm._batch_state, sm._autoclave_failed, sm._autoclave_f0 = saved
