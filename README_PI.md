# BioSeg on DietPi

## SSH

| | |
|---|---|
| User | `root` or `dietpi` |
| Password | your DietPi password (set at first boot; not stored in this repo) |
| WiFi | `motorola edge 70_4711` |
| Code | `/home/dietpi/biobyte` |

```bash
ssh root@<pi-ip>
# or: ssh dietpi@<pi-ip>
```

Find IP on the phone hotspot client list, or from another machine:
`nmap -sn 192.168.43.0/24` (hotspot subnet may differ).

## First boot (once)

```bash
bash /home/dietpi/biobyte/setup_pi.sh
```

Flash Uno from a laptop: `bioseg/edge/arduino/bioseg_uno/bioseg_uno.ino` → plug USB into Pi (`/dev/ttyACM0`).

## Every demo

```bash
source /home/dietpi/biobyte/.venv/bin/activate
cd /home/dietpi/biobyte

# terminal 1 — dashboard + upload API
PYTHONPATH=. uvicorn bioseg.server.main:app --host 0.0.0.0 --port 8000

# terminal 2 — live camera → model → Uno
CAMERA_URL=http://127.0.0.1:9081/ PYTHONPATH=/home/dietpi/biobyte python -m bioseg.edge.capture
```

Laptop: `http://<pi-ip>:8000`

| Mode | What |
|---|---|
| **Live hardware** | motionEye camera + IR/chute → Uno LEFT/RIGHT/HOLD |
| **Upload / sample** | Card under the camera — pick class or upload JPEG → same ONNX → which bin |
| **Digital twin** | Four-bin diagram (no Uno move for mechanism buttons) |

Hardware props: **cotton → yellow**, **glove → red**, **mask → HOLD**.

## If motionEye is down

```bash
sudo systemctl enable --now motioneye
curl -I http://127.0.0.1:9081/
```
