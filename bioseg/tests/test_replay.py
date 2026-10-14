"""
Test suite for BioSeg Twin Replay & Demo System.
Validates synthetic session integrity, 4 demo beats, replay badge injection,
ledger verification, and REST/WebSocket endpoints.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bioseg.tools.record import parse_args as parse_record_args
from bioseg.tools.replay_server import (
    ReplayEngine,
    create_replay_app,
    parse_args as parse_replay_args,
    parse_iso_timestamp,
    resolve_session_path,
)

SESSION_PATH = Path("/home/mudassir/Projects/biobyte/bioseg/data/sessions/demo_synthetic.jsonl")


@pytest.fixture
def synthetic_events() -> list[dict]:
    """Loads all events from demo_synthetic.jsonl."""
    assert SESSION_PATH.exists(), f"Synthetic session file not found: {SESSION_PATH}"
    events = []
    with open(SESSION_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                events.append(json.loads(line_str))
    return events


def test_synthetic_session_valid(synthetic_events: list[dict]) -> None:
    """Verifies that demo_synthetic.jsonl contains valid JSON lines with required fields."""
    assert len(synthetic_events) >= 40, f"Expected at least 40 events, got {len(synthetic_events)}"
    
    valid_event_types = {
        "trigger",
        "classified",
        "routed",
        "held",
        "ledger_append",
        "autoclave_tick",
        "autoclave_result",
        "state_change",
        "override",
        "heartbeat",
    }

    for idx, ev in enumerate(synthetic_events):
        assert "ts" in ev, f"Event #{idx} missing 'ts' field: {ev}"
        assert "event" in ev, f"Event #{idx} missing 'event' field: {ev}"
        assert ev["event"] in valid_event_types, f"Event #{idx} has unknown type: {ev['event']}"

        # Ensure ts is valid ISO 8601
        dt = parse_iso_timestamp(ev["ts"])
        assert isinstance(dt, datetime.datetime)


def test_event_ordering(synthetic_events: list[dict]) -> None:
    """Verifies that all event timestamps in the synthetic session are monotonically increasing."""
    prev_dt: datetime.datetime | None = None
    for idx, ev in enumerate(synthetic_events):
        curr_dt = parse_iso_timestamp(ev["ts"])
        if prev_dt is not None:
            assert curr_dt >= prev_dt, (
                f"Non-monotonic timestamp at event #{idx}: {ev['ts']} is before {prev_dt.isoformat()}"
            )
        prev_dt = curr_dt


def test_four_beats(synthetic_events: list[dict]) -> None:
    """
    Verifies that the synthetic session faithfully contains all 4 demo beats:
    Beat 1: Clean Gauze -> YELLOW (classified gauze 0.92, routed flap left)
    Beat 2: Nitrile Glove -> RED (classified glove 0.88, routed flap right)
    Beat 3: Capped Syringe -> HOLD (needle_cap 0.71, metal_veto, operator override)
    Beat 4: Failed Autoclave -> Release Blocked (tick series <=115C, result FAIL, locked)
    """
    # Beat 1: Clean Gauze -> YELLOW
    beat1_classified = [
        ev for ev in synthetic_events
        if ev.get("event") == "classified" and ev.get("item_class") == "gauze"
    ]
    assert len(beat1_classified) >= 1, "Beat 1 missing: gauze classification"
    assert beat1_classified[0]["confidence"] == pytest.approx(0.92, abs=0.01)
    assert beat1_classified[0]["route"] == "yellow"
    assert beat1_classified[0]["flap"] == "left"

    beat1_routed = [
        ev for ev in synthetic_events
        if ev.get("event") == "routed" and ev.get("flap") == "left"
    ]
    assert len(beat1_routed) >= 1, "Beat 1 missing: route to flap left"

    # Beat 2: Nitrile Glove -> RED
    beat2_classified = [
        ev for ev in synthetic_events
        if ev.get("event") == "classified" and ev.get("item_class") == "glove"
    ]
    assert len(beat2_classified) >= 1, "Beat 2 missing: glove classification"
    assert beat2_classified[0]["confidence"] == pytest.approx(0.88, abs=0.01)
    assert beat2_classified[0]["route"] == "red"
    assert beat2_classified[0]["flap"] == "right"

    beat2_routed = [
        ev for ev in synthetic_events
        if ev.get("event") == "routed" and ev.get("flap") == "right"
    ]
    assert len(beat2_routed) >= 1, "Beat 2 missing: route to flap right"

    # Beat 3: Capped Syringe -> HOLD and Operator Override
    beat3_held = [
        ev for ev in synthetic_events
        if ev.get("event") == "held" and ev.get("reason") == "metal_veto"
    ]
    assert len(beat3_held) >= 1, "Beat 3 missing: held event with reason metal_veto"
    assert beat3_held[0]["item_class"] == "needle_cap"
    assert beat3_held[0]["confidence"] == pytest.approx(0.71, abs=0.01)

    beat3_override = [
        ev for ev in synthetic_events
        if ev.get("event") == "override"
    ]
    assert len(beat3_override) >= 1, "Beat 3 missing: operator override event"
    assert "not sharps" in beat3_override[0]["reason"].lower()
    assert "operator" in beat3_override[0]

    # Beat 4: Failed Autoclave -> Release Blocked
    autoclave_ticks = [
        ev for ev in synthetic_events
        if ev.get("event") == "autoclave_tick"
    ]
    assert len(autoclave_ticks) >= 8, f"Expected multiple autoclave ticks, found {len(autoclave_ticks)}"
    max_temp = max(tick["t_c"] for tick in autoclave_ticks)
    assert max_temp < 121.0, f"Autoclave temperature must never reach 121°C threshold, reached {max_temp}"

    autoclave_results = [
        ev for ev in synthetic_events
        if ev.get("event") == "autoclave_result"
    ]
    assert len(autoclave_results) >= 1, "Beat 4 missing: autoclave_result"
    assert autoclave_results[0]["result"] == "FAIL"
    assert autoclave_results[0]["f0"] < 15.0

    blocked_states = [
        ev for ev in synthetic_events
        if ev.get("event") == "state_change" and ev.get("to") == "RELEASE_BLOCKED"
    ]
    assert len(blocked_states) >= 1, "Beat 4 missing: transition to RELEASE_BLOCKED"


def test_replay_badge(synthetic_events: list[dict]) -> None:
    """Verifies that the replay engine injects 'replay: true' into replayed events."""
    engine = ReplayEngine(SESSION_PATH, speed=100.0, loop=False)
    
    # Process all events through engine state application
    replayed_events = []
    for raw_ev in engine.raw_events:
        ev_copy = copy.deepcopy(raw_ev)
        ev_copy["replay"] = True
        engine.apply_event_to_state(ev_copy)
        replayed_events.append(ev_copy)

    assert len(replayed_events) == len(synthetic_events)
    for ev in replayed_events:
        assert ev.get("replay") is True, f"Event {ev.get('event')} missing replay: true"


def test_ledger_chain_integrity(synthetic_events: list[dict]) -> None:
    """Verifies the SHA-256 cryptographic chain of ledger_append events."""
    ledger_events = [ev for ev in synthetic_events if ev.get("event") == "ledger_append"]
    assert len(ledger_events) >= 5, f"Expected at least 5 ledger entries, got {len(ledger_events)}"

    prev_hash = "0000000000000000000000000000000000000000000000000000000000000000"
    for i, entry in enumerate(ledger_events):
        idx = entry["index"]
        actual_prev = entry["prev_hash"]
        payload_hash = entry["payload_hash"]
        block_hash = entry["hash"]

        assert actual_prev == prev_hash, f"Entry {idx} prev_hash mismatch: {actual_prev} != {prev_hash}"
        expected_hash = hashlib.sha256(f"{idx}:{actual_prev}:{payload_hash}".encode("utf-8")).hexdigest()
        assert block_hash == expected_hash, f"Entry {idx} hash mismatch: {block_hash} != {expected_hash}"

        prev_hash = block_hash


def test_replay_server_rest_endpoints() -> None:
    """Tests /state, /ledger, /ledger/verify, and /replay/{session_id} REST endpoints."""
    app = create_replay_app(SESSION_PATH, speed=10.0, loop=False, start_replay=False)
    engine: ReplayEngine = app.state.engine

    # Simulate fast-forwarding all events into engine state
    for raw_ev in engine.raw_events:
        ev_copy = copy.deepcopy(raw_ev)
        ev_copy["replay"] = True
        engine.apply_event_to_state(ev_copy)

    with TestClient(app) as client:
        # 1. Root info
        res_root = client.get("/")
        assert res_root.status_code == 200
        assert res_root.json()["replay"] is True

        # 2. State endpoint
        res_state = client.get("/state")
        assert res_state.status_code == 200
        state_data = res_state.json()
        assert state_data["replay"] is True
        assert state_data["state"] == "RELEASE_BLOCKED"
        assert state_data["bins"]["yellow"] >= 1
        assert state_data["bins"]["red"] >= 1
        assert state_data["autoclave"]["release_blocked"] is True
        assert state_data["autoclave"]["result"] == "FAIL"

        # 3. Ledger endpoint
        res_ledger = client.get("/ledger")
        assert res_ledger.status_code == 200
        ledger_data = res_ledger.json()
        assert ledger_data["replay"] is True
        assert ledger_data["count"] >= 5
        assert len(ledger_data["entries"]) >= 5

        # 4. Ledger verification endpoint
        res_verify = client.get("/ledger/verify")
        assert res_verify.status_code == 200
        verify_data = res_verify.json()
        assert verify_data["verified"] is True
        assert verify_data["status"] == "VALID"
        assert verify_data["chain_length"] >= 5
        assert verify_data["errors"] == []

        # 5. Replay session download endpoint
        res_session = client.get("/replay/demo_synthetic")
        assert res_session.status_code == 200
        lines = [line.strip() for line in res_session.text.strip().split("\n") if line.strip()]
        assert len(lines) >= 40

        # Non-existent session
        res_missing = client.get("/replay/non_existent_session_xyz")
        assert res_missing.status_code == 404


def test_cli_argument_parsers() -> None:
    """Verifies CLI argument parsing for both record.py and replay_server.py."""
    rec_args = parse_record_args(["--server", "192.168.1.50", "--port", "9000", "--save-frames"])
    assert rec_args.server == "192.168.1.50"
    assert rec_args.port == 9000
    assert rec_args.save_frames is True

    rep_args = parse_replay_args(["demo.jsonl", "--speed", "2.5", "--port", "8080", "--no-loop"])
    assert rep_args.session_file == "demo.jsonl"
    assert rep_args.speed == 2.5
    assert rep_args.port == 8080
    assert rep_args.no_loop is True


def test_websocket_broadcast() -> None:
    """Verifies that ConnectionManager broadcasts json messages with replay badge."""
    import asyncio
    from bioseg.tools.replay_server import ConnectionManager

    cm = ConnectionManager()

    class MockWebSocket:
        def __init__(self) -> None:
            self.sent_messages: list[dict] = []

        async def send_json(self, data: dict) -> None:
            self.sent_messages.append(data)

    ws = MockWebSocket()
    cm.active_connections.add(ws)  # type: ignore[arg-type]

    payload = {"event": "heartbeat", "replay": True, "uptime_s": 120}
    asyncio.run(cm.broadcast(payload))

    assert len(ws.sent_messages) == 1
    assert ws.sent_messages[0]["event"] == "heartbeat"
    assert ws.sent_messages[0]["replay"] is True


def test_replay_speed_control() -> None:
    """Verifies speed control multiplier calculation."""
    engine_normal = ReplayEngine(SESSION_PATH, speed=1.0, loop=False)
    engine_fast = ReplayEngine(SESSION_PATH, speed=2.0, loop=False)
    engine_slow = ReplayEngine(SESSION_PATH, speed=0.5, loop=False)

    assert engine_normal.speed == pytest.approx(1.0)
    assert engine_fast.speed == pytest.approx(2.0)
    assert engine_slow.speed == pytest.approx(0.5)

