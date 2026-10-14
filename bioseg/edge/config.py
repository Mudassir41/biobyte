"""Edge configuration parameters for BioSeg Twin.

Provides hardware pin assignments, server connectivity settings, model
parameters, and operational thresholds for deployment on Raspberry Pi 4.
All parameters can be overridden via environment variables if needed.
"""

from __future__ import annotations

import os
from typing import Tuple

# -----------------------------------------------------------------------------
# Server Connectivity
# -----------------------------------------------------------------------------
SERVER_IP: str = os.getenv("BIOSEG_SERVER_IP", "127.0.0.1")
SERVER_PORT: int = int(os.getenv("BIOSEG_SERVER_PORT", "8000"))
EVENTS_ENDPOINT: str = f"http://{SERVER_IP}:{SERVER_PORT}/events"
DEVICE_ID: str = os.getenv("BIOSEG_DEVICE_ID", "pi")

# -----------------------------------------------------------------------------
# GPIO Hardware Configuration (BCM pin numbering)
# -----------------------------------------------------------------------------
# Infrared obstacle / proximity sensor for object arrival detection
IR_PIN: int = int(os.getenv("BIOSEG_IR_PIN", "17"))

# MG90S PWM servo motor signal pin controlling the segregation flap
SERVO_PIN: int = int(os.getenv("BIOSEG_SERVO_PIN", "18"))

# motionEye on this DietPi card serves the CSI camera as MJPEG.
# Python reads that stream. It does not open the camera while motionEye holds it.
# Set BIOSEG_CAMERA_URL empty to skip the stream and open a device instead.
CAMERA_URL: str = os.getenv("BIOSEG_CAMERA_URL", "http://127.0.0.1:9081/")
CAMERA_INDEX: int = int(os.getenv("BIOSEG_CAMERA_INDEX", "0"))
CAPTURE_RESOLUTION: Tuple[int, int] = (640, 480)

# -----------------------------------------------------------------------------
# Servo Actuation Parameters (MG90S 3-position flap)
# -----------------------------------------------------------------------------
# left = yellow bin, right = red bin, centre = gate shut (not a third bin)
SERVO_LEFT_ANGLE: float = -60.0
SERVO_CENTRE_ANGLE: float = 0.0
SERVO_RIGHT_ANGLE: float = 60.0

FLAP_ANGLES = {
    "left": SERVO_LEFT_ANGLE,
    "centre": SERVO_CENTRE_ANGLE,
    "center": SERVO_CENTRE_ANGLE,  # convenience alias
    "right": SERVO_RIGHT_ANGLE,
}

# Settling delay in seconds for flap physical motion
SERVO_SETTLE_SECONDS: float = 0.30

# Safe startup and failure default route & flap
DEFAULT_ROUTE: str = "hold"
DEFAULT_FLAP: str = "centre"

# The flap is driven by the Arduino Uno over USB serial. 'gpio' remains only as a fallback.
SERVO_BACKEND: str = os.getenv("BIOSEG_SERVO_BACKEND", "serial")
PCA9685_I2C_ADDRESS: int = int(os.getenv("BIOSEG_PCA9685_ADDR", "0x40"), 16)
PCA9685_CHANNEL: int = int(os.getenv("BIOSEG_PCA9685_CHANNEL", "0"))
SERIAL_PORT: str = os.getenv("BIOSEG_SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUDRATE: int = int(os.getenv("BIOSEG_SERIAL_BAUD", "115200"))

# -----------------------------------------------------------------------------
# Vision & Classification Engine Settings
# -----------------------------------------------------------------------------
MODEL_PATH: str = os.getenv("BIOSEG_MODEL_PATH", "bioseg/edge/models/yolo11n-bioseg.onnx")
MODEL_NAME: str = "yolo11n-bioseg"

# Minimum classification confidence required to accept a routed class
CONFIDENCE_THRESHOLD: float = 0.60

# Disagreement threshold: object detected by IR, but vision confidence < 0.30
DISAGREEMENT_CONFIDENCE_THRESHOLD: float = 0.30

# Consecutive frames required to trigger sensor-vision disagreement veto
LOW_CONFIDENCE_FRAMES: int = 5

# Input resolution expected by classification backbone (width, height)
INFERENCE_IMAGE_SIZE: Tuple[int, int] = (224, 224)
