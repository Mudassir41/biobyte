"""Flap actuator control using MG90S servo on Raspberry Pi.

Actuates the mechanical sorting flap to route biomedical waste items into
one of three bins:
  - left: Yellow bin (non-infectious, non-sharp: gauze, cotton, bandage, paper)
  - right: Red bin (infectious, non-sharp: gloves, syringes, tubing, bottles)
  - centre: Hold tray (sharps, glass, unknown, low-confidence)

Position angles:
  - left: -60.0°
  - centre: 0.0° (safe default on startup, idle, and error recovery)
  - right: +60.0°

Emits and POSTs 'routed' events to the BioSeg Twin server.
If gpiozero or PWM hardware is unavailable (e.g. testing on laptop),
the controller logs all actions in simulation mode without crashing.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from bioseg.edge.config import (
    DEFAULT_FLAP,
    DEVICE_ID,
    FLAP_ANGLES,
    SERIAL_PORT,
    SERVO_BACKEND,
    SERVO_PIN,
    SERVO_SETTLE_SECONDS,
    SERVER_IP,
    SERVER_PORT,
)
from bioseg.edge.uno import UnoLink, command_for_flap

logger = logging.getLogger("bioseg.edge.flap")

# Optional gpiozero import wrapped for testing on non-Pi environments
try:
    from gpiozero import AngularServo
    from gpiozero.exc import BadPinFactory, GPIOPinMissing
    GPIOZERO_AVAILABLE = True
except (ImportError, Exception):
    AngularServo = None
    BadPinFactory = Exception
    GPIOPinMissing = Exception
    GPIOZERO_AVAILABLE = False


class FlapController:
    """Manages the MG90S servo motor controlling the physical sorting flap."""

    def __init__(
        self,
        pin: int = SERVO_PIN,
        server_ip: str = SERVER_IP,
        server_port: int = SERVER_PORT,
    ):
        """Initialize the flap servo controller.

        Args:
            pin: BCM GPIO pin connected to the servo PWM control wire.
            server_ip: Target server IPv4 address for event dispatch.
            server_port: Target server TCP port.
        """
        self.pin = pin
        self.server_ip = server_ip
        self.server_port = server_port
        self.servo: Optional[Any] = None
        self.uno: Optional[UnoLink] = None
        self.hardware_active: bool = False
        self.current_position: str = DEFAULT_FLAP

        self._init_servo()

    def _init_servo(self) -> None:
        """Open the Uno serial port. GPIO is only used when BIOSEG_SERVO_BACKEND=gpio."""
        if SERVO_BACKEND != "gpio":
            self.uno = UnoLink(port=SERIAL_PORT)
            self.hardware_active = self.uno.connected
            return

        if not GPIOZERO_AVAILABLE or AngularServo is None:
            logger.info("gpiozero not installed. FlapController operating in simulation mode.")
            self.hardware_active = False
            return

        try:
            # MG90S typical specs:
            # PWM frequency: 50Hz, pulse width: 0.5ms (-90°) to 2.5ms (+90°)
            self.servo = AngularServo(
                self.pin,
                min_angle=-90,
                max_angle=90,
                min_pulse_width=0.0005,
                max_pulse_width=0.0025,
            )
            # Default safe position on startup: centre (HOLD, 0°)
            self.servo.angle = FLAP_ANGLES[DEFAULT_FLAP]
            self.hardware_active = True
            logger.info("Initialized MG90S servo on GPIO%d at centre (0.0°)", self.pin)
        except Exception as exc:
            logger.warning(
                "Hardware servo initialization failed on GPIO%d (%s). Falling back to simulation mode.",
                self.pin,
                exc,
            )
            self.servo = None
            self.hardware_active = False

    def move(self, position: str, item_id: str = "") -> Dict[str, Any]:
        """Move the servo flap to the specified position and emit a routed event.

        Args:
            position: Target position ('left', 'right', or 'centre').
            item_id: Unique identifier of the item being routed (e.g. 'ITEM-0001').

        Returns:
            Dict containing the dispatched event payload.
        """
        norm_position = position.lower().strip()
        if norm_position == "center":
            norm_position = "centre"

        # Safe fallback: if invalid position provided, route to centre (HOLD)
        if norm_position not in ("left", "right", "centre"):
            logger.warning("Unrecognized flap position '%s'; defaulting safely to 'centre'", position)
            norm_position = DEFAULT_FLAP

        target_angle = FLAP_ANGLES[norm_position]

        uno_command = command_for_flap(norm_position)
        if self.uno is not None:
            self.uno.send(uno_command)
            logger.info("Uno command %s for flap %s item '%s'", uno_command, norm_position, item_id)
        elif self.hardware_active and self.servo is not None:
            try:
                self.servo.angle = target_angle
                logger.info("Actuated flap on GPIO%d to %s (%.1f°)", self.pin, norm_position, target_angle)
            except Exception as exc:
                logger.error("Servo actuation error (%s). Resetting safely to centre.", exc)
                try:
                    self.servo.angle = FLAP_ANGLES[DEFAULT_FLAP]
                except Exception:
                    pass
                norm_position = DEFAULT_FLAP
        else:
            logger.info("[NO HARDWARE] Flap %s (%s) for item '%s'", norm_position, uno_command, item_id)

        # Wait 300ms for physical settling
        time.sleep(SERVO_SETTLE_SECONDS)
        self.current_position = norm_position

        # Construct routed event
        event_payload: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "routed",
            "flap": norm_position,
            "item_id": item_id,
            "device": DEVICE_ID,
        }

        # POST routed event to server
        self._post_event(event_payload)
        return event_payload

    def reset(self) -> Dict[str, Any]:
        """Return the flap to the safe centre position."""
        return self.move(DEFAULT_FLAP)

    def _post_event(self, event_data: Dict[str, Any], timeout: float = 2.0) -> bool:
        """POST event dictionary to the backend server."""
        endpoint = f"http://{self.server_ip}:{self.server_port}/events"
        try:
            response = requests.post(endpoint, json=event_data, timeout=timeout)
            response.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("Could not dispatch routed event to %s: %s", endpoint, exc)
            return False

    def close(self) -> None:
        """Release GPIO resources and detach the servo."""
        if self.uno is not None:
            self.uno.send("HOLD")
            self.uno.close()
            self.uno = None
        if self.servo is not None:
            try:
                self.servo.angle = FLAP_ANGLES[DEFAULT_FLAP]
                time.sleep(0.1)
                self.servo.close()
            except Exception as exc:
                logger.debug("Error while closing servo: %s", exc)
            finally:
                self.servo = None
                self.hardware_active = False


# -----------------------------------------------------------------------------
# Module-level convenience functions
# -----------------------------------------------------------------------------
_default_controller: Optional[FlapController] = None


def get_flap_controller(
    pin: int = SERVO_PIN,
    server_ip: str = SERVER_IP,
    server_port: int = SERVER_PORT,
) -> FlapController:
    """Return the global FlapController instance."""
    global _default_controller
    if _default_controller is None:
        _default_controller = FlapController(pin=pin, server_ip=server_ip, server_port=server_port)
    return _default_controller


def move(position: str, item_id: str = "") -> Dict[str, Any]:
    """Move the flap servo to position ('left', 'right', 'centre').

    Waits 300ms for settle, emits a 'routed' event, and POSTs to server.
    """
    return get_flap_controller().move(position=position, item_id=item_id)


def reset() -> Dict[str, Any]:
    """Reset the flap servo to the default centre position."""
    return get_flap_controller().reset()


def close() -> None:
    """Clean up and release the default flap controller."""
    global _default_controller
    if _default_controller is not None:
        _default_controller.close()
        _default_controller = None


# -----------------------------------------------------------------------------
# Standalone CLI Test Runner
# -----------------------------------------------------------------------------
def main() -> None:
    """CLI runner to test flap actuation on Raspberry Pi or laptop."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="BioSeg MG90S Flap Actuator Controller")
    parser.add_argument("--position", choices=["left", "centre", "right"], help="Move flap to specified position")
    parser.add_argument("--item-id", default="TEST-0001", help="Item ID to attach to routed event")
    parser.add_argument("--pin", type=int, default=SERVO_PIN, help="BCM GPIO pin for servo")
    parser.add_argument("--test", action="store_true", help="Run full sweep test (left -> centre -> right -> centre)")
    args = parser.parse_args()

    controller = FlapController(pin=args.pin)

    try:
        if args.test:
            print(f"Executing flap sweep test on GPIO{args.pin}...")
            for pos in ["centre", "left", "centre", "right", "centre"]:
                print(f"Moving to: {pos}")
                controller.move(pos, item_id=f"TEST-{pos.upper()}")
                time.sleep(0.5)
            print("Sweep test completed successfully.")
        elif args.position:
            print(f"Moving to: {args.position}")
            res = controller.move(args.position, item_id=args.item_id)
            print("Dispatched event:", res)
        else:
            parser.print_help()
    finally:
        controller.close()


if __name__ == "__main__":
    main()
