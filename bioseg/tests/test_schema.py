"""Schema validation tests for BioSeg Twin event envelopes and route mapping tables."""

from datetime import datetime
import pytest

from bioseg.edge.infer import DEFAULT_ROUTE, ROUTE_MAP

# -----------------------------------------------------------------------------
# Event Schema Definitions & Validator
# -----------------------------------------------------------------------------
VALID_EVENT_TYPES = {
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

# Required fields per event type according to specification §3
EVENT_REQUIRED_FIELDS = {
    "trigger": {"ts", "event", "source"},
    "classified": {
        "ts",
        "event",
        "item_id",
        "item_class",
        "confidence",
        "model",
        "device",
        "route",
        "flap",
        "reason",
    },
    "routed": {"ts", "event", "flap", "item_id"},
    "held": {"ts", "event", "reason"},
    "ledger_append": {"ts", "event", "index", "payload_hash", "prev_hash", "hash"},
    "autoclave_tick": {"ts", "event", "t_c", "elapsed_s", "f0", "phase"},
    "autoclave_result": {"ts", "event", "f0", "result", "threshold_f0"},
    "state_change": {"ts", "event", "from", "to"},
    "override": {"ts", "event", "item_id", "reason"},
    "heartbeat": {"ts", "event", "uptime_s", "model", "latency_ms"},
}


def validate_event(event: dict) -> None:
    """Validate that an event dictionary conforms to the BioSeg schema specification.

    Raises:
        ValueError: If any envelope or event-specific validation rule fails.
    """
    if not isinstance(event, dict):
        raise ValueError("Event must be a dictionary")

    if "ts" not in event:
        raise ValueError("Event missing required timestamp 'ts'")

    try:
        # Validate timestamp parseability (ISO8601)
        datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError(f"Invalid ISO8601 timestamp in 'ts': {event['ts']}") from exc

    event_type = event.get("event")
    if not event_type:
        raise ValueError("Event missing required 'event' type field")

    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(f"Unknown event type: '{event_type}'")

    required_fields = EVENT_REQUIRED_FIELDS[event_type]
    missing = required_fields - set(event.keys())
    if missing:
        raise ValueError(
            f"Event type '{event_type}' is missing required fields: {sorted(missing)}"
        )

    # Specific field value validations
    if event_type == "classified":
        if not (0.0 <= event["confidence"] <= 1.0):
            raise ValueError(f"Confidence {event['confidence']} out of range [0.0, 1.0]")
        if event["route"] not in {"yellow", "red", "hold"}:
            raise ValueError(f"Invalid route '{event['route']}'")
        if event["flap"] not in {"left", "right", "centre", "center"}:
            raise ValueError(f"Invalid flap '{event['flap']}'")

    elif event_type == "routed":
        if event["flap"] not in {"left", "right", "centre", "center"}:
            raise ValueError(f"Invalid flap '{event['flap']}'")

    elif event_type == "autoclave_result":
        if event["result"] not in {"PASS", "FAIL"}:
            raise ValueError(f"Invalid autoclave result '{event['result']}'")


# -----------------------------------------------------------------------------
# Canonical Event Fixtures
# -----------------------------------------------------------------------------
CANONICAL_EVENTS = {
    "trigger": {
        "ts": "2026-10-14T14:32:00.000+05:30",
        "event": "trigger",
        "source": "ir",
    },
    "classified": {
        "ts": "2026-10-14T14:32:05.213+05:30",
        "event": "classified",
        "item_id": "ITEM-0007",
        "item_class": "gauze",
        "confidence": 0.93,
        "model": "yolo11n-cls",
        "device": "pi",
        "route": "yellow",
        "flap": "left",
        "reason": "rule: class gauze -> yellow",
        "ledger_hash": "a1b2c3d4e5",
    },
    "routed": {
        "ts": "2026-10-14T14:32:05.513+05:30",
        "event": "routed",
        "flap": "left",
        "item_id": "ITEM-0007",
    },
    "held": {
        "ts": "2026-10-14T14:32:10.123+05:30",
        "event": "held",
        "item_id": "ITEM-0008",
        "reason": "metal_veto",
    },
    "ledger_append": {
        "ts": "2026-10-14T14:32:10.150+05:30",
        "event": "ledger_append",
        "index": 42,
        "payload_hash": "abc123",
        "prev_hash": "def456",
        "hash": "789ghi",
    },
    "autoclave_tick": {
        "ts": "2026-10-14T14:32:15.000+05:30",
        "event": "autoclave_tick",
        "t_c": 121.4,
        "elapsed_s": 900,
        "f0": 6.2,
        "phase": "hold",
    },
    "autoclave_result": {
        "ts": "2026-10-14T14:47:00.000+05:30",
        "event": "autoclave_result",
        "f0": 15.8,
        "result": "PASS",
        "threshold_f0": 15.0,
    },
    "state_change": {
        "ts": "2026-10-14T14:47:01.000+05:30",
        "event": "state_change",
        "from": "PHYSICAL_PASS",
        "to": "BI_PENDING",
    },
    "override": {
        "ts": "2026-10-14T14:48:00.000+05:30",
        "event": "override",
        "item_id": "ITEM-0008",
        "operator": "demo",
        "reason": "not sharps - confirmed blunt",
    },
    "heartbeat": {
        "ts": "2026-10-14T14:48:05.000+05:30",
        "event": "heartbeat",
        "uptime_s": 120.0,
        "model": "yolo11n-cls",
        "latency_ms": 14.5,
    },
}


# -----------------------------------------------------------------------------
# Test Cases
# -----------------------------------------------------------------------------
def test_classified_event_valid():
    """A properly formed classified event matches the schema without error."""
    valid_classified = CANONICAL_EVENTS["classified"]
    validate_event(valid_classified)


def test_missing_field_rejected():
    """Missing a required schema field must raise a ValueError."""
    incomplete_classified = dict(CANONICAL_EVENTS["classified"])
    del incomplete_classified["route"]

    with pytest.raises(ValueError, match="missing required fields"):
        validate_event(incomplete_classified)

    missing_ts = dict(CANONICAL_EVENTS["trigger"])
    del missing_ts["ts"]
    with pytest.raises(ValueError, match="missing required timestamp"):
        validate_event(missing_ts)


def test_all_event_types():
    """Each of the 10 defined event types has a valid canonical fixture that passes validation."""
    assert len(CANONICAL_EVENTS) == len(VALID_EVENT_TYPES)
    for event_type, sample in CANONICAL_EVENTS.items():
        validate_event(sample)


def test_route_mapping_unique():
    """Every class in ROUTE_MAP must map deterministically to exactly one valid route."""
    valid_routes = {"yellow", "red", "hold"}
    assert isinstance(ROUTE_MAP, dict)
    assert len(ROUTE_MAP) > 0

    for cls_name, route in ROUTE_MAP.items():
        assert isinstance(cls_name, str) and len(cls_name) > 0
        assert route in valid_routes, f"Class '{cls_name}' maps to invalid route '{route}'"

    # Verify default route fallback is HOLD
    assert DEFAULT_ROUTE == "hold"


def test_needle_always_hold():
    """Safety critical invariant: sharps/needle classes MUST always map to the HOLD route."""
    assert ROUTE_MAP.get("needle") == "hold"
    assert ROUTE_MAP.get("needle_cap") == "hold"
    assert ROUTE_MAP.get("scalpel") == "hold"
    assert ROUTE_MAP.get("blade") == "hold"
