#!/bin/bash
# First-boot setup on DietPi (run once after SSH).
set -euo pipefail
cd /home/dietpi/biobyte

sudo systemctl enable --now motioneye || true

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip wheel
pip install \
  fastapi "uvicorn[standard]" python-multipart \
  opencv-python-headless onnxruntime pillow requests pyserial

echo
echo "OK — model: $(ls bioseg/edge/models/yolo11n-bioseg.onnx 2>/dev/null || echo MISSING)"
echo
echo "=== Start BioSeg (two terminals or tmux) ==="
echo "  source /home/dietpi/biobyte/.venv/bin/activate"
echo "  cd /home/dietpi/biobyte && PYTHONPATH=. uvicorn bioseg.server.main:app --host 0.0.0.0 --port 8000"
echo
echo "  CAMERA_URL=http://127.0.0.1:9081/ PYTHONPATH=/home/dietpi/biobyte python -m bioseg.edge.capture"
echo
echo "Laptop browser: http://<pi-ip>:8000"
echo "  Live = Pi camera (:9081) + Uno"
echo "  Upload / sample = same ONNX without waiting for the chute"
echo "motionEye UI: http://<pi-ip>:8765"
