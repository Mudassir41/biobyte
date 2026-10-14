# How to run the demo

Always start from the repo root:

```bash
cd /home/mudassir/Projects/biobyte
source .venv/bin/activate
```

Do not use port 8765 for BioSeg. On DietPi that is motionEye.

---

## 1. Website (dashboard + twin mode)

```bash
cd /home/mudassir/Projects/biobyte
source .venv/bin/activate
PYTHONPATH=. python -m uvicorn bioseg.server.main:app --host 127.0.0.1 --port 8000
```

Open: [http://127.0.0.1:8000](http://127.0.0.1:8000)

| Control | What it does |
|---|---|
| **Live hardware** | Two-bin chute view. Camera comes from DietPi motionEye (`:9081`), not the laptop. |
| **Digital twin** | Four-bin design + mechanism buttons. |
| **Drop a photo through the model** | Sample dropdown + **Run sample**, or **Upload image** + **Classify upload** (`POST /demo/upload`). No laptop camera. |
| **Model: gauze / glove / syringe / urine bag / test tube / glasses** | Same ONNX path as the card above — quick buttons. Live hardware props are cotton / mask / glove. |
| Mechanism buttons | Move the drawing only. Do not call the model or the Arduino. |

Direct twin URL: [http://127.0.0.1:8000/?twin=1](http://127.0.0.1:8000/?twin=1)

---

## 2. Godot 3D twin (closed loop)

The twin places a sample, a virtual chute camera captures it, the **real** ONNX model classifies that JPEG, then the twin moves the flap and bins.

Needs the website server on port **8000**.

```bash
godot --path /home/mudassir/Projects/biobyte/bioseg/godot
```

Or open that folder in Godot 4.7 and press Play. Main scene is `scenes/rig.tscn` only (supermarket archive is `bioseg/_archive_visionops_shop`).

| Control | What it does |
|---|---|
| **Gauze / glove / syringe / urine bag / test tube / glasses** | All trained classes — chute JPEG → `POST /demo/frame` → bins move |
| Keys **1–6** | Same six closed-loop drops |
| Key **T** | Auto script through all six classes |
| Key **R** | Reset |
| Mechanism buttons | Local animation only (no model) |
| Chute camera panel | What the model is shown (photo on the sample face) |

Laptop webcam is not used. Arduino is not moved from these buttons.

---

## 3. Live hardware on DietPi (real camera + Uno)

On the Pi:

```bash
sudo systemctl enable --now motioneye
cd /path/to/biobyte
source .venv/bin/activate
pip install fastapi uvicorn opencv-python-headless onnxruntime pillow

# terminal 1
PYTHONPATH=. uvicorn bioseg.server.main:app --host 0.0.0.0 --port 8000

# terminal 2 — reads motionEye, does not open the camera device itself
PYTHONPATH=. python -m bioseg.edge.capture
```

Laptop browser: `http://<pi-ip>:8000`

Wiring and flash notes: `bioseg/edge/deploy.md`  
Talking points: `bioseg/tools/demo_script.md`  
What this solves: `bioseg/docs/PRACTICE.md`

---

## 4. Check the model alone (no Godot, no camera)

```bash
cd /home/mudassir/Projects/biobyte
source .venv/bin/activate
PYTHONPATH=. python - << 'PY'
from bioseg.edge.infer import ModelInferenceEngine
e = ModelInferenceEngine("bioseg/edge/models/yolo11n-bioseg.onnx")
print(e.backend, e.predict("bioseg/data/datasets/cls_data/val/gauze"))
PY
```

Or with the server up:

```bash
curl -s -X POST http://127.0.0.1:8000/demo/sample \
  -H 'Content-Type: application/json' \
  -d '{"sample":"gauze"}'

curl -s -X POST http://127.0.0.1:8000/demo/upload \
  -F "file=@bioseg/data/datasets/cls_data/val/gauze/gauze_1671116250120.jpeg"
```

Requires `python-multipart` in the venv (`pip install python-multipart`) for upload.

---

## 5. Blender mechanism (correct Uno kinematics)

```bash
export PATH="$HOME/.local/bin:$PATH"   # portable Blender 4.2 if installed there
blender --background --python bioseg/blender/build_bioseg_rig.py
```

Opens as `bioseg/blender/bioseg_rig.blend`. Godot import: `bioseg/blender/exports/bioseg_rig.glb`. Notes: `bioseg/blender/README.md`.

## Quick demo order (laptop only, no Pi)

1. Start uvicorn on **8000**
2. Open the dashboard → **Digital twin** → run the Model buttons (all six classes). Live hardware uses cotton / mask / glove.
3. Optionally open Godot on the same machine and press the same Model buttons
4. Leave **Live hardware** for when the Pi camera and Uno are connected
