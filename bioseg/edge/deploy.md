# Run BioSeg on the Raspberry Pi

The Pi owns the camera and talks to the Arduino Uno. The laptop only opens a browser. This program does not open the laptop webcam, and it does not simulate treatment. Another station reports whether a bag was processed.

## What you plug in

| Uno pin | Wire |
|---|---|
| D9 | Servo signal. Servo power comes from a separate 5 V supply, not the Uno 5 V pin. Tie the supply ground to Uno GND. |
| D2 | IR obstacle OUT |
| D8 | Buzzer, optional |
| USB | Pi |

Flash `bioseg/edge/arduino/bioseg_uno/bioseg_uno.ino` once. Set `ANGLE_YELLOW`, `ANGLE_RED`, and `ANGLE_SHUT` so shut blocks both bins.

The CSI camera is already served by motionEye on this DietPi card. Do not open it from Python at the same time. `config.txt` has `start_x=1`, and `/etc/motioneye/camera-1.conf` uses the legacy camera `vc.ril.camera` at 640×480. The live picture is `http://<pi-ip>:9081/`. The motionEye page is port 8765, so the BioSeg server stays on 8000.

motionEye is installed but not enabled for boot. Start it before the edge process:

```bash
sudo systemctl enable --now motioneye
```

## On the Pi

Prefer the one-shot script at the repo root:

```bash
bash /home/dietpi/biobyte/setup_pi.sh
```

Or manually:

```bash
cd /home/dietpi/biobyte
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi "uvicorn[standard]" python-multipart opencv-python-headless onnxruntime pillow requests pyserial

# terminal 1
PYTHONPATH=. uvicorn bioseg.server.main:app --host 0.0.0.0 --port 8000

# terminal 2 — reads http://127.0.0.1:9081/ and does not open the camera device
CAMERA_URL=http://127.0.0.1:9081/ PYTHONPATH=/home/dietpi/biobyte python -m bioseg.edge.capture
```

From the laptop browser open `http://<pi-ip>:8000`.

| Mode | What |
|---|---|
| **Live hardware** | motionEye stream + chute → Uno |
| **Upload / sample** | Always under “What the camera sees” — pick a stock photo or upload → ONNX → which bin |
| **Digital twin** | Four-bin design; mechanism buttons do not move the Uno |

SSH password and short checklist: `README_PI.md` at the biobyte root. Practice notes: `bioseg/docs/PRACTICE.md`.

The edge posts pictures to the server on the same Pi (`127.0.0.1:8000`). If the server is on another machine, set `BIOSEG_SERVER_IP` to that machine before starting capture.

## What the other station sends

This screen starts at **Not processed**. When the treatment station has a result, it posts:

```bash
curl -X POST http://<pi-ip>:8000/events \
  -H 'Content-Type: application/json' \
  -d '{"event":"state_change","from":"BI_PENDING","to":"RELEASED","processed":true,"source":"treatment-station"}'
```

`to: RELEASED` or `"processed": true` flips the twin to Processed. Until that message arrives, the line stays Not processed.
