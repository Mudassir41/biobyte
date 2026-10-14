"""Unit tests for BioSeg Twin Ledger hash chain verification and tamper detection."""

import pytest
from bioseg.server.ledger import Ledger


def test_empty_chain_valid():
    """An empty chain should verify as valid with no broken index."""
    ledger = Ledger()
    assert len(ledger) == 0
    res = ledger.verify()
    assert res == {"valid": True, "broken_at": None}


def test_genesis_row():
    """Genesis row (index 0) must have prev_hash set to exactly 64 zeros."""
    ledger = Ledger()
    row = ledger.append("trigger", {"source": "ir", "channel": 1})
    assert row["index"] == 0
    assert row["prev_hash"] == "0" * 64
    assert len(row["hash"]) == 64
    res = ledger.verify()
    assert res["valid"] is True
    assert res["broken_at"] is None


def test_append_and_verify():
    """Building a 10-row chain must result in a continuous, cryptographically valid ledger."""
    ledger = Ledger()
    for i in range(10):
        row = ledger.append(
            event_type="classified",
            payload={
                "item_id": f"ITEM-{i:04d}",
                "item_class": "gauze" if i % 2 == 0 else "glove",
                "confidence": 0.90 + (i * 0.005),
                "route": "yellow" if i % 2 == 0 else "red",
            },
        )
        assert row["index"] == i
        if i > 0:
            assert row["prev_hash"] == ledger.get_chain()[i - 1]["hash"]

    assert len(ledger) == 10
    verification = ledger.verify()
    assert verification["valid"] is True
    assert verification["broken_at"] is None


def test_tamper_detection():
    """Mutating a payload at index k must cause verify() to fail exactly at k."""
    ledger = Ledger()
    for i in range(10):
        ledger.append(
            event_type="classified",
            payload={"item_id": f"ITEM-{i:04d}", "count": i},
        )

    assert ledger.verify()["valid"] is True

    # Tamper with index 4
    tamper_index = 4
    ledger.tamper_row(tamper_index, {"item_id": "ITEM-0004", "count": 99999})

    verification = ledger.verify()
    assert verification["valid"] is False
    assert verification["broken_at"] == tamper_index


def test_tamper_genesis_row():
    """Tampering with the genesis row must cause verification failure at index 0."""
    ledger = Ledger()
    for i in range(5):
        ledger.append("event", {"val": i})

    ledger.tamper_row(0, {"val": 42})
    verification = ledger.verify()
    assert verification["valid"] is False
    assert verification["broken_at"] == 0
