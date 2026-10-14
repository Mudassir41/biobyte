"""Classification inference engine with biomedical waste fusion rules.

Runs classification on captured frames using NCNN (primary) or ONNX Runtime
(fallback) backbones. Applies deterministic rule-based fusion logic with
safety vetoes for sharps, glass, low confidence, and sensor disagreements.
Outputs compliant event payloads and posts them to the BioSeg Twin server.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import requests

from bioseg.edge.config import (
    CONFIDENCE_THRESHOLD,
    DEFAULT_FLAP,
    DEFAULT_ROUTE,
    DEVICE_ID,
    DISAGREEMENT_CONFIDENCE_THRESHOLD,
    EVENTS_ENDPOINT,
    INFERENCE_IMAGE_SIZE,
    LOW_CONFIDENCE_FRAMES,
    MODEL_NAME,
    MODEL_PATH,
    SERVER_IP,
    SERVER_PORT,
)

logger = logging.getLogger("bioseg.edge.infer")

# Optional framework imports wrapped for graceful laptop execution
try:
    import ncnn
    NCNN_AVAILABLE = True
except ImportError:
    ncnn = None
    NCNN_AVAILABLE = False

try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ort = None
    ONNX_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    cv2 = None
    CV2_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False

# -----------------------------------------------------------------------------
# Classification & Routing Mapping Tables
# -----------------------------------------------------------------------------
ROUTE_MAP: Dict[str, str] = {
    # YELLOW - non-infectious, non-sharp
    "gauze": "yellow",
    "cotton": "yellow",
    "bandage": "yellow",
    "cotton_swab": "yellow",
    "unused_gauze": "yellow",
    "paper": "yellow",
    "dressing": "yellow",
    "mask": "red",  # default red (soiling detection not feasible)
    # RED - infectious, non-sharp
    "glove": "red",
    "gloves": "red",
    "tube": "red",
    "iv_set": "red",
    "drip": "red",
    "syringe": "red",
    "used_syringe": "red",
    "fluid_bottle": "red",
    "plastic_bottle": "red",
    "plastic_bag": "red",
    "test_tube": "red",
    "urine_bag": "red",
    "scrub": "red",
    # HOLD - sharps, glass, hazardous
    "needle": "hold",
    "needle_cap": "hold",
    "scalpel": "hold",
    "blade": "hold",
    "glass": "hold",
    "glass_bottle": "hold",
    "medical_glasses": "hold",
    "mercury_thermometer": "hold",
    "radioactive_objects": "hold",
}

# Route -> flap servo position mapping
ROUTE_TO_FLAP: Dict[str, str] = {
    "yellow": "left",
    "red": "right",
    "hold": "centre",
}

# Veto class groupings
METAL_VETO_CLASSES = {"needle", "needle_cap", "scalpel", "blade"}
GLASS_VETO_CLASSES = {"glass", "glass_bottle", "medical_glasses"}
HAZARD_VETO_CLASSES = {"mercury_thermometer", "radioactive_objects"}

# ONNX yolo11n-bioseg trained classes (index order). ROUTE_MAP may list aliases too.
CLASS_LABELS = [
    "gauze",
    "glove",
    "medical_glasses",
    "syringe",
    "test_tube",
    "urine_bag",
]

# -----------------------------------------------------------------------------
# Global State & Thread Synchronization
# -----------------------------------------------------------------------------
_item_counter: int = 0
_item_counter_lock = threading.Lock()

_consecutive_low_conf_counter: int = 0
_disagreement_lock = threading.Lock()


def get_next_item_id() -> str:
    """Generate an auto-incrementing item ID formatted as ITEM-NNNN."""
    global _item_counter
    with _item_counter_lock:
        _item_counter += 1
        return f"ITEM-{_item_counter:04d}"


def reset_item_counter(start: int = 0) -> None:
    """Reset the auto-incrementing item counter (useful for unit testing)."""
    global _item_counter
    with _item_counter_lock:
        _item_counter = start


def reset_disagreement_counter() -> None:
    """Reset the consecutive low confidence disagreement frame counter."""
    global _consecutive_low_conf_counter
    with _disagreement_lock:
        _consecutive_low_conf_counter = 0


def update_disagreement_counter(ir_present: bool, confidence: float) -> int:
    """Update and return the consecutive low confidence frame count.

    Tracks whether an object is sensed physically by IR but vision cannot
    produce confident classification (< 0.30) across consecutive frames.
    """
    global _consecutive_low_conf_counter
    with _disagreement_lock:
        if ir_present and confidence < DISAGREEMENT_CONFIDENCE_THRESHOLD:
            _consecutive_low_conf_counter += 1
        else:
            _consecutive_low_conf_counter = 0
        return _consecutive_low_conf_counter


# -----------------------------------------------------------------------------
# Event Dispatcher
# -----------------------------------------------------------------------------
def post_event(
    event_data: Dict[str, Any],
    server_ip: str = SERVER_IP,
    server_port: int = SERVER_PORT,
    timeout: float = 2.0,
) -> bool:
    """POST an event JSON dictionary to the BioSeg Twin server.

    Args:
        event_data: Serialized event dictionary.
        server_ip: Target server IPv4 address.
        server_port: Target server TCP port.
        timeout: Request timeout in seconds.

    Returns:
        True if the event was accepted (HTTP 2xx), False otherwise.
    """
    endpoint = f"http://{server_ip}:{server_port}/events"
    try:
        response = requests.post(endpoint, json=event_data, timeout=timeout)
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Could not dispatch event to %s: %s", endpoint, exc)
        return False


# -----------------------------------------------------------------------------
# Model Inference Engine
# -----------------------------------------------------------------------------
class ModelInferenceEngine:
    """Encapsulates model loading and inference for NCNN and ONNX Runtime."""

    def __init__(self, model_path: str = MODEL_PATH):
        self.model_path = model_path
        self.backend: Optional[str] = None
        self.ncnn_net = None
        self.onnx_session = None
        self.is_loaded = False
        self._load_model()

    def _load_model(self) -> None:
        """Attempt loading NCNN first, then ONNX runtime fallback."""
        model_p = Path(self.model_path)

        # 1. Try NCNN backend
        if NCNN_AVAILABLE:
            param_file: Optional[Path] = None
            bin_file: Optional[Path] = None

            if model_p.is_dir():
                for p in model_p.glob("*.param"):
                    param_file = p
                    break
                for b in model_p.glob("*.bin"):
                    bin_file = b
                    break
            elif model_p.with_suffix(".param").exists() and model_p.with_suffix(".bin").exists():
                param_file = model_p.with_suffix(".param")
                bin_file = model_p.with_suffix(".bin")

            if param_file and bin_file and param_file.exists() and bin_file.exists():
                try:
                    net = ncnn.Net()
                    net.load_param(str(param_file))
                    net.load_model(str(bin_file))
                    self.ncnn_net = net
                    self.backend = "ncnn"
                    self.is_loaded = True
                    logger.info("Loaded NCNN model from %s, %s", param_file, bin_file)
                    return
                except Exception as exc:
                    logger.warning("Failed initializing NCNN model: %s", exc)

        # 2. Try ONNX Runtime backend fallback
        if ONNX_AVAILABLE:
            onnx_file: Optional[Path] = None
            if model_p.is_file() and model_p.suffix == ".onnx":
                onnx_file = model_p
            elif model_p.with_suffix(".onnx").exists():
                onnx_file = model_p.with_suffix(".onnx")
            elif model_p.is_dir():
                for o in model_p.glob("*.onnx"):
                    onnx_file = o
                    break

            if onnx_file and onnx_file.exists():
                try:
                    providers = ["CPUExecutionProvider"]
                    self.onnx_session = ort.InferenceSession(str(onnx_file), providers=providers)
                    self.backend = "onnx"
                    self.is_loaded = True
                    # Dynamically read class names from ONNX metadata if available
                    try:
                        import ast
                        meta = self.onnx_session.get_modelmeta().custom_metadata_map
                        if "names" in meta:
                            parsed_names = ast.literal_eval(meta["names"])
                            if isinstance(parsed_names, dict):
                                self.class_names = [parsed_names[k] for k in sorted(parsed_names.keys())]
                            elif isinstance(parsed_names, list):
                                self.class_names = parsed_names
                            logger.info("Loaded custom model classes: %s", self.class_names)
                    except Exception:
                        self.class_names = CLASS_LABELS
                    logger.info("Loaded ONNX model from %s", onnx_file)
                    return
                except Exception as exc:
                    logger.warning("Failed initializing ONNX model: %s", exc)

        logger.info(
            "No valid NCNN or ONNX model artifact found at '%s'. Engine running in uninitialized/fallback mode.",
            self.model_path,
        )
        self.is_loaded = False
        self.backend = None

    def predict(self, image_input: Union[str, Path, Any]) -> Tuple[str, float]:
        """Run inference on an image file path or numpy array.

        Args:
            image_input: File path (str/Path) or numpy array (H, W, C).

        Returns:
            Tuple of (predicted_class_name, confidence_score).
        """
        if not self.is_loaded:
            return "unknown", 0.0

        if not NUMPY_AVAILABLE:
            logger.warning("NumPy is not installed; cannot execute model inference.")
            return "unknown", 0.0

        img = self._prepare_image(image_input)
        if img is None:
            return "unknown", 0.0

        try:
            if self.backend == "onnx" and self.onnx_session is not None:
                return self._predict_onnx(img)
            elif self.backend == "ncnn" and self.ncnn_net is not None:
                return self._predict_ncnn(img)
        except Exception as exc:
            logger.error("Error during %s model inference: %s", self.backend, exc)

        return "unknown", 0.0

    def _prepare_image(self, image_input: Union[str, Path, Any]) -> Optional[Any]:
        """Load and resize image to the target inference resolution."""
        if isinstance(image_input, (str, Path)):
            path_str = str(image_input)
            if not os.path.exists(path_str):
                logger.warning("Image path does not exist: %s", path_str)
                return None
            if CV2_AVAILABLE:
                bgr = cv2.imread(path_str)
                if bgr is None:
                    return None
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                return cv2.resize(rgb, INFERENCE_IMAGE_SIZE)
            return None

        # Already a numpy array
        if NUMPY_AVAILABLE and isinstance(image_input, np.ndarray):
            if CV2_AVAILABLE:
                if image_input.shape[:2] != (INFERENCE_IMAGE_SIZE[1], INFERENCE_IMAGE_SIZE[0]):
                    return cv2.resize(image_input, INFERENCE_IMAGE_SIZE)
            return image_input

        return None

    def _predict_onnx(self, img_rgb: Any) -> Tuple[str, float]:
        """Execute inference using ONNX Runtime."""
        # Convert HWC to NCHW normalized float32
        tensor = img_rgb.astype(np.float32) / 255.0
        tensor = np.transpose(tensor, (2, 0, 1))
        tensor = np.expand_dims(tensor, axis=0)

        input_name = self.onnx_session.get_inputs()[0].name
        outputs = self.onnx_session.run(None, {input_name: tensor})
        raw_out = outputs[0][0]

        # Check if output is already normalized softmax probabilities
        if np.all(raw_out >= 0.0) and np.isclose(float(np.sum(raw_out)), 1.0, atol=1e-2):
            probs = raw_out
        else:
            exp_logits = np.exp(raw_out - np.max(raw_out))
            probs = exp_logits / np.sum(exp_logits)

        idx = int(np.argmax(probs))
        confidence = float(probs[idx])
        labels = getattr(self, "class_names", CLASS_LABELS)
        item_class = labels[idx] if idx < len(labels) else "unknown"
        return item_class, confidence

    def _predict_ncnn(self, img_rgb: Any) -> Tuple[str, float]:
        """Execute inference using NCNN."""
        h, w, _ = img_rgb.shape
        in_mat = ncnn.Mat.from_pixels(
            img_rgb.tobytes(),
            ncnn.Mat.PixelType.PIXEL_RGB,
            w,
            h,
        )

        # Normalization (0 - 1.0)
        mean_vals = [0.0, 0.0, 0.0]
        norm_vals = [1.0 / 255.0, 1.0 / 255.0, 1.0 / 255.0]
        in_mat.substract_mean_normalize(mean_vals, norm_vals)

        ex = self.ncnn_net.create_extractor()
        input_name = self.ncnn_net.input_names()[0] if hasattr(self.ncnn_net, "input_names") and self.ncnn_net.input_names() else "in0"
        ex.input(input_name, in_mat)
        output_name = self.ncnn_net.output_names()[-1] if hasattr(self.ncnn_net, "output_names") and self.ncnn_net.output_names() else "out0"
        ret, out_mat = ex.extract(output_name)
        if ret != 0:
            return "unknown", 0.0

        scores = [out_mat[i] for i in range(out_mat.w)]
        if not scores:
            return "unknown", 0.0

        scores_arr = np.array(scores, dtype=np.float32)
        exp_scores = np.exp(scores_arr - np.max(scores_arr))
        probs = exp_scores / np.sum(exp_scores)

        idx = int(np.argmax(probs))
        confidence = float(probs[idx])
        item_class = CLASS_LABELS[idx] if idx < len(CLASS_LABELS) else "unknown"
        return item_class, confidence


# Singleton model instance
_default_engine: Optional[ModelInferenceEngine] = None


def get_default_engine() -> ModelInferenceEngine:
    """Return the lazily-initialized singleton inference engine."""
    global _default_engine
    if _default_engine is None:
        _default_engine = ModelInferenceEngine()
    return _default_engine


# -----------------------------------------------------------------------------
# Deterministic Fusion Rules Engine
# -----------------------------------------------------------------------------
def apply_fusion_rules(
    item_class: str,
    confidence: float,
    ir_present: bool = True,
    consecutive_low_conf: Optional[int] = None,
) -> Tuple[str, str, str]:
    """Evaluate deterministic fusion rules with safety vetoes.

    Fusion Rules:
      1. Sensor-vision disagreement: IR detects object but confidence < 0.30
         across 5 consecutive frames -> HOLD (reason: "sensor_vision_disagreement")
      2. Low confidence: confidence < 0.60 -> HOLD (reason: "low_confidence")
      3. Sharps / metal veto: needle, needle_cap, scalpel, blade -> HOLD (reason: "metal_veto")
      4. Glass veto: glass, glass_bottle -> HOLD (reason: "glass_detected")
      5. Hazardous veto: mercury_thermometer, radioactive_objects -> HOLD (reason: "metal_veto" / "hazard_veto")
      6. Unknown / unmapped class -> HOLD (reason: "unknown_object")
      7. Otherwise -> route according to mapping table (reason: "rule: class {class} -> {route}")

    Args:
        item_class: Candidate class name.
        confidence: Prediction confidence [0.0, 1.0].
        ir_present: Physical IR sensor status.
        consecutive_low_conf: Optional manual counter override (for testing).

    Returns:
        Tuple[str, str, str]: (route, flap, reason)
    """
    # 1. Update and check sensor-vision disagreement
    if consecutive_low_conf is not None:
        current_streak = consecutive_low_conf
    else:
        current_streak = update_disagreement_counter(ir_present, confidence)

    if ir_present and current_streak >= LOW_CONFIDENCE_FRAMES:
        return "hold", "centre", "sensor_vision_disagreement"

    # 2. Low confidence veto
    if confidence < CONFIDENCE_THRESHOLD:
        return "hold", "centre", "low_confidence"

    # 3. Unknown / unmapped class check
    if item_class not in ROUTE_MAP:
        return "hold", "centre", "unknown_object"

    # 4. Sharps / metal veto
    if item_class in METAL_VETO_CLASSES:
        return "hold", "centre", "metal_veto"

    # 5. Glass veto
    if item_class in GLASS_VETO_CLASSES:
        return "hold", "centre", "glass_detected"

    # 6. Hazardous material veto
    if item_class in HAZARD_VETO_CLASSES or ROUTE_MAP[item_class] == "hold":
        return "hold", "centre", "metal_veto"

    # 7. Standard mapping table routing
    route = ROUTE_MAP[item_class]
    flap = ROUTE_TO_FLAP.get(route, "centre")
    reason = f"rule: class {item_class} -> {route}"
    return route, flap, reason


# -----------------------------------------------------------------------------
# Main Classification API
# -----------------------------------------------------------------------------
def classify_item(
    image: Union[str, Path, Any, None] = None,
    ir_present: bool = True,
    item_id: Optional[str] = None,
    post: bool = True,
    engine: Optional[ModelInferenceEngine] = None,
    override_class: Optional[str] = None,
    override_confidence: Optional[float] = None,
    consecutive_low_conf: Optional[int] = None,
) -> Dict[str, Any]:
    """Classify a biomedical waste item and route it via deterministic fusion rules.

    Must work without a model file (returns HOLD with reason "no_model_loaded").

    Args:
        image: File path or numpy image array (or None if testing fallback).
        ir_present: Whether IR physical trigger confirms item presence.
        item_id: Optional explicit item ID. If None, auto-increments ITEM-NNNN.
        post: If True, dispatches the classified event HTTP POST to server.
        engine: Optional custom ModelInferenceEngine instance.
        override_class: Optional mock class for deterministic test injection.
        override_confidence: Optional mock confidence for test injection.
        consecutive_low_conf: Optional manual low-confidence frame count.

    Returns:
        Dict matching the event schema:
        {
            "ts": ISO timestamp,
            "event": "classified",
            "item_id": "ITEM-NNNN",
            "item_class": "...",
            "confidence": 0.00,
            "model": "yolo11n-cls",
            "device": "pi",
            "route": "...",
            "flap": "left|right|centre",
            "reason": "...",
            "ledger_hash": ""
        }
    """
    assigned_item_id = item_id if item_id is not None else get_next_item_id()
    now_iso = datetime.now(timezone.utc).isoformat()

    active_engine = engine if engine is not None else get_default_engine()

    # Case A: Explicit mock override provided
    if override_class is not None:
        item_class = override_class
        confidence = float(override_confidence if override_confidence is not None else 1.0)
        route, flap, reason = apply_fusion_rules(
            item_class=item_class,
            confidence=confidence,
            ir_present=ir_present,
            consecutive_low_conf=consecutive_low_conf,
        )

    # Case B: Model is loaded and input image is provided
    elif active_engine.is_loaded and image is not None:
        item_class, confidence = active_engine.predict(image)
        route, flap, reason = apply_fusion_rules(
            item_class=item_class,
            confidence=confidence,
            ir_present=ir_present,
            consecutive_low_conf=consecutive_low_conf,
        )

    # Case C: No model file loaded (Graceful fallback requirement)
    else:
        # Per specification: Must work without a model file (returns HOLD with reason "no_model_loaded")
        item_class = "unknown"
        confidence = 0.0
        route = DEFAULT_ROUTE
        flap = DEFAULT_FLAP
        reason = "no_model_loaded"

    event_payload: Dict[str, Any] = {
        "ts": now_iso,
        "event": "classified",
        "item_id": assigned_item_id,
        "item_class": item_class,
        "confidence": round(confidence, 2),
        "model": MODEL_NAME,
        "device": DEVICE_ID,
        "route": route,
        "flap": flap,
        "reason": reason,
        "ledger_hash": "",
    }

    if post:
        post_event(event_payload)

    return event_payload
