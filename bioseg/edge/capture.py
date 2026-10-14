"""IR-triggered camera capture pipeline for BioSeg Twin.

Monitors an infrared proximity/obstacle sensor on Raspberry Pi GPIO.
When an item is detected:
  1. Emits and dispatches an 'ir_trigger' event to the server.
  2. Captures an image frame from the DietPi motionEye stream (port 9081).
  3. Saves the frame to a temporary file.
  4. Invokes classify_item() in infer.py to determine segregation bin.
  5. Actuates the MG90S servo flap via flap.py to route the physical item.
  6. Dispatches 'classified' and 'routed' events to the backend.

Includes manual trigger mode (spacebar / terminal enter) for desktop and
laptop testing when physical sensors are not attached.
"""

from __future__ import annotations

import argparse
import logging
import os
import select
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import requests

from bioseg.edge.config import (
    CAPTURE_RESOLUTION,
    CAMERA_INDEX,
    CAMERA_URL,
    DEVICE_ID,
    IR_PIN,
    SERVER_IP,
    SERVER_PORT,
)
from bioseg.edge.flap import FlapController, get_flap_controller
from bioseg.edge.infer import classify_item, post_event

logger = logging.getLogger("bioseg.edge.capture")

# Optional hardware imports wrapped for laptop and CI testing
try:
    from picamera2 import Picamera2
    PICAMERA2_AVAILABLE = True
except (ImportError, Exception):
    Picamera2 = None
    PICAMERA2_AVAILABLE = False

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

try:
    from gpiozero import DigitalInputDevice
    GPIOZERO_AVAILABLE = True
except (ImportError, Exception):
    DigitalInputDevice = None
    GPIOZERO_AVAILABLE = False


def _stream_responds(url: str) -> bool:
    """True when the motionEye MJPEG port accepts a connection."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.info("motionEye stream is not up at %s (%s).", url, exc)
        return False


class CameraDevice:
    """Reads the DietPi motionEye stream, or a local camera if that stream is down."""

    def __init__(
        self,
        camera_index: int = CAMERA_INDEX,
        camera_url: str = CAMERA_URL,
        resolution: Tuple[int, int] = CAPTURE_RESOLUTION,
    ):
        """Initialize the camera driver.

        Args:
            camera_index: Hardware index used only when the motionEye stream is down.
            camera_url: motionEye MJPEG URL. Empty skips the stream.
            resolution: Target capture (width, height).
        """
        self.camera_index = camera_index
        self.camera_url = camera_url.strip()
        self.resolution = resolution
        self.picam2 = None
        self.cv2_cap = None
        self.backend: Optional[str] = None

        self._init_camera()

    def _init_camera(self) -> None:
        """Use the motionEye broadcast first. Open the device only if that port is down."""
        if self.camera_url and CV2_AVAILABLE and cv2 is not None and _stream_responds(self.camera_url):
            try:
                cap = cv2.VideoCapture(self.camera_url)
                if cap.isOpened():
                    self.cv2_cap = cap
                    self.backend = "stream"
                    logger.info("Reading the DietPi camera from motionEye at %s", self.camera_url)
                    return
                cap.release()
                logger.warning("motionEye answered at %s but OpenCV could not read it.", self.camera_url)
            except Exception as exc:
                logger.warning("motionEye open failed: %s", exc)

        if PICAMERA2_AVAILABLE and Picamera2 is not None:
            try:
                picam = Picamera2()
                config = picam.create_still_configuration(
                    main={"size": self.resolution, "format": "RGB888"}
                )
                picam.configure(config)
                picam.start()
                self.picam2 = picam
                self.backend = "picamera2"
                logger.info("Initialized Picamera2 device at %dx%d", *self.resolution)
                return
            except Exception as exc:
                logger.warning("Picamera2 initialization failed: %s", exc)

        # 2. Fall back to cv2.VideoCapture
        if CV2_AVAILABLE and cv2 is not None:
            try:
                cap = cv2.VideoCapture(self.camera_index)
                if cap.isOpened():
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
                    self.cv2_cap = cap
                    self.backend = "cv2"
                    logger.info("Initialized cv2.VideoCapture(%d) at %dx%d", self.camera_index, *self.resolution)
                    return
                else:
                    logger.warning("cv2.VideoCapture(%d) failed to open device.", self.camera_index)
            except Exception as exc:
                logger.warning("cv2.VideoCapture initialization failed: %s", exc)

        logger.info("No physical camera detected. Camera device will operate in synthetic test mode.")
        self.backend = "synthetic"

    def capture(self) -> Optional[Any]:
        """Capture a single image frame as a numpy array.

        Returns:
            RGB or BGR numpy array, or synthetic test pattern if camera is unavailable.
        """
        # Picamera2 capture
        if self.backend == "picamera2" and self.picam2 is not None:
            try:
                rgb_frame = self.picam2.capture_array()
                return rgb_frame
            except Exception as exc:
                logger.error("Picamera2 frame acquisition failed: %s", exc)

        # OpenCV capture
        if self.backend in ("cv2", "stream") and self.cv2_cap is not None:
            try:
                ret, bgr_frame = self.cv2_cap.read()
                if ret and bgr_frame is not None:
                    return bgr_frame
                logger.warning("cv2.VideoCapture returned empty frame.")
            except Exception as exc:
                logger.error("cv2.VideoCapture read error: %s", exc)

        # Synthetic test pattern fallback
        return self._generate_synthetic_frame()

    def _generate_synthetic_frame(self) -> Optional[Any]:
        """Generate a synthetic test frame for headless laptop verification."""
        if not NUMPY_AVAILABLE:
            return None

        w, h = self.resolution
        # Clean solid canvas
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[:] = (30, 30, 35)  # dark slate gray

        if CV2_AVAILABLE:
            ts_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
            cv2.putText(
                img,
                f"BioSeg Synthetic Frame ({ts_str})",
                (30, 60),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 200),
                2,
            )
            cv2.putText(
                img,
                "Hardware Camera Not Attached",
                (30, 100),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (200, 200, 200),
                1,
            )
            # Draw sample item outline
            cv2.rectangle(img, (220, 160), (420, 360), (80, 120, 220), 2)
            cv2.putText(img, "TEST SPECIMEN", (240, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 180, 255), 1)

        return img

    def release(self) -> None:
        """Release underlying camera resources."""
        if self.picam2 is not None:
            try:
                self.picam2.stop()
                self.picam2.close()
            except Exception as exc:
                logger.debug("Error releasing picam2: %s", exc)
            self.picam2 = None

        if self.cv2_cap is not None:
            try:
                self.cv2_cap.release()
            except Exception as exc:
                logger.debug("Error releasing cv2_cap: %s", exc)
            self.cv2_cap = None


class EdgeCapturePipeline:
    """End-to-end edge pipeline tying IR sensing, camera acquisition, and flap control."""

    def __init__(
        self,
        server_ip: str = SERVER_IP,
        server_port: int = SERVER_PORT,
        camera_index: int = CAMERA_INDEX,
        ir_pin: int = IR_PIN,
    ):
        """Initialize pipeline with specified hardware assignments and network destinations."""
        self.server_ip = server_ip
        self.server_port = server_port
        self.camera_index = camera_index
        self.ir_pin = ir_pin

        self.camera = CameraDevice(camera_index=self.camera_index, resolution=CAPTURE_RESOLUTION)
        self.flap = get_flap_controller(server_ip=self.server_ip, server_port=self.server_port)
        self._cam_lock = threading.Lock()
        self._preview_on = False
        self.ir_sensor = None
        self.ir_available = bool(self.flap.uno and self.flap.uno.connected)

    def _init_ir_sensor(self) -> None:
        """Initialize the infrared digital proximity sensor via gpiozero."""
        if not GPIOZERO_AVAILABLE or DigitalInputDevice is None:
            logger.info("gpiozero unavailable; IR sensor operating in manual mode.")
            self.ir_available = False
            return

        try:
            # Active-low IR obstacle modules pull pin LOW when obstacle present
            self.ir_sensor = DigitalInputDevice(self.ir_pin, pull_up=True, bounce_time=0.08)
            self.ir_available = True
            logger.info("Configured IR sensor on BCM GPIO%d (active-low)", self.ir_pin)
        except Exception as exc:
            logger.warning(
                "Hardware IR sensor initialization failed on GPIO%d (%s). Operating in manual trigger mode.",
                self.ir_pin,
                exc,
            )
            self.ir_sensor = None
            self.ir_available = False

    def emit_trigger_event(self, manual: bool = False) -> Dict[str, Any]:
        """Tell the server an item arrived. The IR sensor is on the Uno, not a laptop."""
        event_payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "trigger",
            "source": "manual" if manual else "ir",
            "device": DEVICE_ID,
        }
        post_event(event_payload, server_ip=self.server_ip, server_port=self.server_port)
        return event_payload

    def grab(self) -> Optional[Any]:
        with self._cam_lock:
            return self.camera.capture()

    def frame_jpeg(self, frame: Any) -> Optional[bytes]:
        """Encode a real camera frame. Synthetic stand-in frames are not uploaded."""
        if frame is None or self.camera.backend not in ("picamera2", "cv2", "stream"):
            return None
        if not CV2_AVAILABLE or cv2 is None:
            return None
        image = frame
        if self.camera.backend == "picamera2":
            image = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if not ok:
            return None
        return buf.tobytes()

    def publish_frame(self, frame: Any) -> None:
        jpeg = self.frame_jpeg(frame)
        if jpeg is None:
            return
        try:
            requests.post(
                f"http://{self.server_ip}:{self.server_port}/camera_frame",
                data=jpeg,
                headers={"Content-Type": "image/jpeg"},
                timeout=0.4,
            )
        except Exception as exc:
            logger.debug("Pi camera frame was not accepted: %s", exc)

    def save_frame_to_tempfile(self, frame: Any) -> Optional[str]:
        """Save captured image frame to a temporary JPG file on disk."""
        if frame is None:
            return None

        fd, temp_path = tempfile.mkstemp(prefix="bioseg_cap_", suffix=".jpg")
        os.close(fd)

        if CV2_AVAILABLE and cv2 is not None:
            # OpenCV imwrite expects BGR
            cv2.imwrite(temp_path, frame)
            return temp_path

        # If OpenCV not available, create an empty placeholder file
        with open(temp_path, "wb") as f:
            f.write(b"")
        return temp_path

    def process_item_arrival(self, manual: bool = False) -> Dict[str, Any]:
        """Full lifecycle when an item is triggered: capture -> infer -> flap -> post."""
        # 1. Emit trigger event
        self.emit_trigger_event(manual=manual)
        frame = self.grab()
        self.publish_frame(frame)
        temp_path = self.save_frame_to_tempfile(frame)

        try:
            # 4. Pass frame to infer.py for classification and fusion rules
            classification = classify_item(
                image=temp_path,
                ir_present=True,
                post=True,
            )

            # 5. Move servo flap to routed position and emit 'routed' event
            flap_target = classification.get("flap", "centre")
            item_id = classification.get("item_id", "")
            self.flap.move(flap_target, item_id=item_id)

            return classification

        finally:
            # 6. Clean up temporary frame file
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def run_uno_loop(self) -> None:
        """Wait for TRIG lines from the Uno, then capture on the Pi camera."""
        logger.info("Waiting for the IR sensor on the Arduino Uno.")
        try:
            while True:
                lines = self.flap.uno.read_lines() if self.flap.uno is not None else []
                if "TRIG" in lines:
                    logger.info("Uno reported an item in the chute.")
                    self.process_item_arrival(manual=False)
                    time.sleep(0.4)
                time.sleep(0.02)
        except KeyboardInterrupt:
            logger.info("Uno capture loop stopped.")

    def _preview_loop(self) -> None:
        while self._preview_on:
            self.publish_frame(self.grab())
            time.sleep(0.2)

    def run(self, manual: bool = False, single: bool = False) -> None:
        """Start the Pi camera preview and the Uno trigger loop."""
        # motionEye already serves the picture on port 9081. Do not re-encode it.
        self._preview_on = self.camera.backend in ("picamera2", "cv2")
        preview = None
        if self.camera.backend == "stream":
            logger.info("Dashboard watches motionEye on port 9081. This process grabs a frame only when the IR trips.")
        elif self._preview_on:
            preview = threading.Thread(target=self._preview_loop, daemon=True)
            preview.start()
            logger.info("Publishing the Pi camera to the dashboard.")
        else:
            logger.info("No Pi camera is open. The dashboard will keep waiting. The laptop camera is not used.")
        try:
            if single:
                self.process_item_arrival(manual=True)
            elif manual or not self.ir_available:
                self.run_manual_loop()
            else:
                self.run_uno_loop()
        finally:
            self._preview_on = False
            if preview is not None:
                preview.join(timeout=1.0)

    def run_manual_loop(self) -> None:
        """Interactive manual trigger loop (spacebar / Enter) for desktop testing."""
        logger.info("Entering manual trigger mode.")
        logger.info("Press [Enter] or [Spacebar + Enter] to trigger capture. Type 'q' to quit.")

        try:
            while True:
                # Check for keyboard input non-blockingly or prompt
                line = input("Trigger [Enter/'q']: ").strip()
                if line.lower() == "q":
                    break
                logger.info("Manual trigger activated. Capturing specimen...")
                result = self.process_item_arrival(manual=True)
                logger.info(
                    "Segregated %s (class=%s, confidence=%.2f, route=%s, flap=%s)",
                    result.get("item_id"),
                    result.get("item_class"),
                    result.get("confidence", 0.0),
                    result.get("route"),
                    result.get("flap"),
                )
        except (KeyboardInterrupt, EOFError):
            logger.info("Manual trigger mode exited.")

    def close(self) -> None:
        """Release all camera, sensor, and actuator resources."""
        self.camera.release()
        self.flap.close()
        if self.ir_sensor is not None:
            try:
                self.ir_sensor.close()
            except Exception:
                pass


# -----------------------------------------------------------------------------
# Standalone CLI Entry Point
# -----------------------------------------------------------------------------
def main() -> None:
    """CLI entrypoint for running the edge capture and segregation pipeline."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="BioSeg Twin Edge Capture Pipeline")
    parser.add_argument("--server-ip", default=SERVER_IP, help="Target backend server IP")
    parser.add_argument("--server-port", type=int, default=SERVER_PORT, help="Target backend server port")
    parser.add_argument("--camera-index", type=int, default=CAMERA_INDEX, help="OpenCV camera device index")
    parser.add_argument("--ir-pin", type=int, default=IR_PIN, help="BCM GPIO pin for IR proximity sensor")
    parser.add_argument("--manual", action="store_true", help="Force interactive manual trigger mode (spacebar/Enter)")
    parser.add_argument("--single", action="store_true", help="Execute a single capture-infer-route cycle and exit")
    args = parser.parse_args()

    pipeline = EdgeCapturePipeline(
        server_ip=args.server_ip,
        server_port=args.server_port,
        camera_index=args.camera_index,
        ir_pin=args.ir_pin,
    )

    try:
        pipeline.run(manual=args.manual, single=args.single)
    finally:
        pipeline.close()


if __name__ == "__main__":
    main()
