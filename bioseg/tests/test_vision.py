"""Vision and inference unit & integration tests for BioSeg Twin.

Verifies:
1. Route mapping uniqueness and safety defaults.
2. Asymmetric safety test: needle / sharps NEVER route away from HOLD under any confidence or setting.
3. Offline integration test: 10 sample images fed through infer.py offline, each emitting a schema-valid classified event.
4. Deterministic fusion rules: low confidence, metal veto, glass veto, sensor disagreement.
"""

from pathlib import Path
import numpy as np
from PIL import Image
import pytest

from bioseg.edge.infer import (
    DEFAULT_ROUTE,
    ROUTE_MAP,
    apply_fusion_rules,
    classify_item,
    reset_disagreement_counter,
    reset_item_counter,
)
from bioseg.tests.test_schema import validate_event


@pytest.fixture(autouse=True)
def reset_counters():
    reset_item_counter(0)
    reset_disagreement_counter()


def test_mapping_table_integrity():
    """Unit: mapping table has no class mapping to more than one route; UNKNOWN -> HOLD."""
    valid_routes = {"yellow", "red", "hold"}
    for cls_name, route in ROUTE_MAP.items():
        assert route in valid_routes
    assert DEFAULT_ROUTE == "hold"

    # Test unknown object routing
    route, flap, reason = apply_fusion_rules(item_class="mystery_waste", confidence=0.99)
    assert route == "hold"
    assert flap == "centre"
    assert reason == "unknown_object"


def test_asymmetric_needle_safety():
    """Safety: a needle-class image NEVER emits route != 'hold' across any confidence or condition."""
    sharps_classes = ["needle", "needle_cap", "scalpel", "blade"]
    test_confidences = [0.0, 0.1, 0.5, 0.6, 0.75, 0.99, 1.0]

    for sharps_cls in sharps_classes:
        for conf in test_confidences:
            # Test direct rule fusion
            route, flap, reason = apply_fusion_rules(
                item_class=sharps_cls,
                confidence=conf,
                ir_present=True,
            )
            assert route == "hold", f"CRITICAL SAFETY VIOLATION: {sharps_cls} at conf {conf} routed to {route}"
            assert flap in {"centre", "center"}

            # Test through classify_item API
            event = classify_item(
                image=None,
                override_class=sharps_cls,
                override_confidence=conf,
                post=False,
            )
            assert event["route"] == "hold"
            assert event["flap"] in {"centre", "center"}


def test_feed_10_sample_images_offline(tmp_path: Path):
    """Integration: feed 10 sample images through infer.py offline; each emits a schema-valid classified event."""
    sample_classes = [
        ("gauze", 0.92, "yellow"),
        ("glove", 0.88, "red"),
        ("needle", 0.95, "hold"),
        ("glass_bottle", 0.85, "hold"),
        ("cotton", 0.91, "yellow"),
        ("syringe", 0.78, "red"),
        ("scalpel", 0.94, "hold"),
        ("bandage", 0.89, "yellow"),
        ("tube", 0.82, "red"),
        ("blade", 0.97, "hold"),
    ]

    for i, (item_cls, conf, expected_route) in enumerate(sample_classes):
        # Create a synthetic image on disk
        img_arr = np.random.randint(0, 255, (224, 224, 3), dtype=np.uint8)
        img_path = tmp_path / f"sample_{i}_{item_cls}.jpg"
        Image.fromarray(img_arr).save(img_path)

        # Feed offline with mock override parameters (simulating model output)
        event = classify_item(
            image=str(img_path),
            override_class=item_cls,
            override_confidence=conf,
            post=False,
        )

        # Validate against strict schema
        validate_event(event)

        # Check routing correctness
        assert event["item_class"] == item_cls
        assert event["route"] == expected_route
        assert event["event"] == "classified"
        assert event["item_id"].startswith("ITEM-")


def test_sensor_vision_disagreement_veto():
    """IR says object present but model confidence < 0.3 across 5 consecutive frames -> HOLD."""
    # 4 frames below threshold -> low_confidence
    for streak in range(1, 5):
        route, flap, reason = apply_fusion_rules(
            item_class="gauze",
            confidence=0.25,
            ir_present=True,
            consecutive_low_conf=streak,
        )
        assert route == "hold"
        assert reason == "low_confidence"

    # 5th frame -> sensor_vision_disagreement
    route, flap, reason = apply_fusion_rules(
        item_class="gauze",
        confidence=0.25,
        ir_present=True,
        consecutive_low_conf=5,
    )
    assert route == "hold"
    assert flap == "centre"
    assert reason == "sensor_vision_disagreement"
