"""Serial link to the Arduino Uno that owns the flap and the IR sensor.

The Uno has two physical drops and a shut gate:
  LEFT  yellow bin
  RIGHT red bin
  HOLD  gate shut, item stays in the chute (not a third bin)

This module does not open a camera and does not invent a treatment cycle.
"""

from __future__ import annotations

import logging
import os
import termios
from typing import List, Optional

from bioseg.edge.config import SERIAL_BAUDRATE, SERIAL_PORT

logger = logging.getLogger("bioseg.edge.uno")

FLAP_COMMAND = {
    "left": "LEFT",
    "right": "RIGHT",
    "centre": "HOLD",
    "center": "HOLD",
}

# Only these two commands drop an item into a bin.
BIN_COMMANDS = ("LEFT", "RIGHT")


def command_for_flap(position: str) -> str:
    """Map a flap name to the Uno command. Unknown positions stay shut."""
    key = (position or "").lower().strip()
    if key == "center":
        key = "centre"
    return FLAP_COMMAND.get(key, "HOLD")


class UnoLink:
    """Line-oriented serial port. Missing hardware logs the command and continues."""

    def __init__(self, port: str = SERIAL_PORT, baud: int = SERIAL_BAUDRATE):
        self.port = port
        self.baud = baud
        self._fd: Optional[int] = None
        self._buf = b""
        self.connected = False
        self._open()

    def _open(self) -> None:
        if not hasattr(termios, "B115200") and self.baud == 115200:
            logger.warning("This platform has no 115200 baud constant.")
            return
        speed = getattr(termios, f"B{self.baud}", None)
        if speed is None:
            logger.warning("Unsupported baud %s", self.baud)
            return
        try:
            fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            logger.info("Arduino Uno is not on %s (%s). Flap commands will be logged only.", self.port, exc)
            return
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
        attrs[3] = 0
        attrs[4] = speed
        attrs[5] = speed
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        self._fd = fd
        self.connected = True
        logger.info("Arduino Uno connected on %s", self.port)

    def send(self, line: str) -> None:
        command = line.strip()
        if not self.connected or self._fd is None:
            logger.info("[UNO offline] %s", command)
            return
        os.write(self._fd, (command + "\n").encode("ascii"))

    def read_lines(self) -> List[str]:
        if not self.connected or self._fd is None:
            return []
        try:
            chunk = os.read(self._fd, 256)
        except BlockingIOError:
            return []
        except OSError:
            return []
        if not chunk:
            return []
        self._buf += chunk
        lines: List[str] = []
        while b"\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n", 1)
            text = raw.decode("ascii", errors="replace").strip()
            if text:
                lines.append(text)
        return lines

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        self.connected = False
