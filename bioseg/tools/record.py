"""
BioSeg Twin — Session Recorder
Records live WebSocket events from BioSeg Twin server to JSONL session files.
Optionally captures live camera frames from the /video_feed endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bioseg.record")


class FrameRecorder(threading.Thread):
    """Background thread that captures JPEG frames from HTTP /video_feed."""

    def __init__(self, video_url: str, output_dir: Path):
        super().__init__(daemon=True)
        self.video_url = video_url
        self.output_dir = output_dir
        self.stop_requested = threading.Event()
        self.saved_frames = 0
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> None:
        logger.info(f"Starting frame capture from {self.video_url} -> {self.output_dir}")
        buffer = bytearray()
        
        while not self.stop_requested.is_set():
            try:
                req = urllib.request.Request(self.video_url, headers={"User-Agent": "BioSeg-Recorder"})
                with urllib.request.urlopen(req, timeout=5.0) as stream:
                    while not self.stop_requested.is_set():
                        chunk = stream.read(8192)
                        if not chunk:
                            break
                        buffer.extend(chunk)

                        # Look for JPEG start and end markers
                        start = buffer.find(b"\xff\xd8")
                        end = buffer.find(b"\xff\xd9")
                        if start != -1 and end != -1 and end > start:
                            jpg_bytes = buffer[start : end + 2]
                            buffer = buffer[end + 2 :]

                            self.saved_frames += 1
                            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
                            frame_filename = self.output_dir / f"frame_{self.saved_frames:06d}_{timestamp}.jpg"
                            with open(frame_filename, "wb") as f:
                                f.write(jpg_bytes)
            except urllib.error.URLError as e:
                if not self.stop_requested.is_set():
                    logger.warning(f"Frame feed unavailable ({e.reason}). Retrying in 2s...")
                    time.sleep(2.0)
            except Exception as e:
                if not self.stop_requested.is_set():
                    logger.warning(f"Frame capture read error: {e}. Retrying in 2s...")
                    time.sleep(2.0)

    def stop(self) -> int:
        self.stop_requested.set()
        self.join(timeout=2.0)
        return self.saved_frames


async def record_session(
    server_ip: str = "localhost",
    port: int = 8000,
    save_frames: bool = False,
    output_dir: Optional[Path] = None,
    session_id: Optional[str] = None,
) -> int:
    """Connects to ws://{server_ip}:{port}/ws and records events to JSONL."""
    if session_id is None:
        # ISO timestamp format suitable for filenames: e.g. 2026-10-14T09-00-00
        session_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")

    if output_dir is None:
        # Check standard project locations
        candidate_paths = [
            Path("/home/mudassir/Projects/biobyte/bioseg/data/sessions"),
            Path("bioseg/data/sessions"),
            Path("data/sessions"),
        ]
        output_dir = next((p for p in candidate_paths if p.parent.exists()), Path("data/sessions"))

    output_dir.mkdir(parents=True, exist_ok=True)
    session_filepath = output_dir / f"{session_id}.jsonl"

    ws_url = f"ws://{server_ip}:{port}/ws"
    video_url = f"http://{server_ip}:{port}/video_feed"

    frame_recorder: Optional[FrameRecorder] = None
    if save_frames:
        frames_dir = output_dir / f"{session_id}_frames"
        frame_recorder = FrameRecorder(video_url, frames_dir)
        frame_recorder.start()

    event_count = 0
    shutdown_requested = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_requested.set)
        except (NotImplementedError, RuntimeError):
            # Windows or non-main thread fallback
            pass

    logger.info(f"BioSeg Session Recorder started")
    logger.info(f"Target WebSocket: {ws_url}")
    logger.info(f"Session Output:   {session_filepath}")
    if save_frames:
        logger.info(f"Frames Output:    {output_dir / f'{session_id}_frames'}")

    try:
        with open(session_filepath, "w", encoding="utf-8") as outfile:
            while not shutdown_requested.is_set():
                try:
                    logger.info(f"Connecting to {ws_url}...")
                    async with websockets.connect(ws_url) as ws:
                        logger.info("Connected to WebSocket stream. Recording events (Ctrl+C to stop)...")
                        while not shutdown_requested.is_set():
                            recv_task = asyncio.create_task(ws.recv())
                            shutdown_task = asyncio.create_task(shutdown_requested.wait())
                            done, pending = await asyncio.wait(
                                [recv_task, shutdown_task],
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            for task in pending:
                                task.cancel()

                            if shutdown_requested.is_set():
                                break

                            if recv_task in done:
                                msg = recv_task.result()
                                # Verify valid JSON
                                try:
                                    payload = json.loads(msg)
                                    outfile.write(json.dumps(payload) + "\n")
                                    outfile.flush()
                                    event_count += 1
                                    event_type = payload.get("event", "unknown")
                                    logger.debug(f"Recorded event #{event_count}: {event_type}")
                                except json.JSONDecodeError:
                                    logger.warning(f"Skipping invalid non-JSON payload: {msg!r}")

                except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                    if shutdown_requested.is_set():
                        break
                    logger.warning(f"WebSocket disconnected ({e}). Reconnecting in 3s...")
                    try:
                        await asyncio.wait_for(shutdown_requested.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        pass
    except KeyboardInterrupt:
        shutdown_requested.set()
    finally:
        if frame_recorder:
            frames_saved = frame_recorder.stop()
            logger.info(f"Frame recorder stopped. Captured {frames_saved} frames.")

        print(f"\n[RECORD] Recorded {event_count} events to {session_filepath}")

    return event_count


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bioseg.tools.record",
        description="BioSeg Twin Session Recorder — Captures live WebSocket telemetry and frames.",
    )
    parser.add_argument(
        "--server",
        default="localhost",
        help="BioSeg server IP or hostname (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="BioSeg server port (default: 8000)",
    )
    parser.add_argument(
        "--save-frames",
        action="store_true",
        help="Save video frames alongside the JSONL session from /video_feed",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Custom output directory for session data",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Override automatic ISO timestamp session ID",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    try:
        asyncio.run(
            record_session(
                server_ip=args.server,
                port=args.port,
                save_frames=args.save_frames,
                output_dir=args.output_dir,
                session_id=args.session_id,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
