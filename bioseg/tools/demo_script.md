# Demo script

About two minutes. Say only what the machine does.

## Props

| Mode | What you drop |
|---|---|
| **Live hardware** (Pi + Uno) | Cotton, surgical mask, nitrile glove |
| **Digital twin** (dashboard / Godot) | All six trained classes: gauze, glove, syringe, urine bag, test tube, glasses |

Do not open the old supermarket twin. Godot main scene is `scenes/rig.tscn` only.

## Before you start

- DietPi: `sudo systemctl enable --now motioneye`
- Server on port 8000, then `python -m bioseg.edge.capture` on the Pi
- Laptop browser: `http://<pi-ip>:8000` on **Live hardware**
- Soft twin only: `http://127.0.0.1:8000/?twin=1`

Do not play `demo_synthetic.jsonl` and call it a recording from the rig. It is not.

## Live: cotton

Drop cotton. Expect gauze (or soiled), yellow, flap left. Uno command LEFT.

Say: soiled cotton goes to yellow, and the row is written down.

## Live: glove

Drop a nitrile glove. Expect glove, red, flap right. Command RIGHT.

Click **Verify chain**. The page says Chain valid when the hashes still link.

Say: a glove is red plastic. The chain is only a record of what this chute did.

## Live: mask

Drop the surgical mask. The model was **not** trained on masks. Expect HOLD — gate stays shut — or a low-confidence wrong class that still holds.

Say: if it is not sure, it does not drop. A person takes the mask. White and blue are also by hand after a hold; this Uno only drives yellow and red.

## Twin: full variety

Switch to **Digital twin**. Press the Model buttons (gauze, glove, syringe, urine bag, test tube, glasses). Those run real ONNX on stock photos. Say the twin shows every class we trained; the table only drops cotton, mask, and glove.

## Twin: upload or pick a photo

In **Digital twin**, use **Drop a photo through the model** — **Choose sample photo** + **Run sample**, or **Upload image** + **Classify upload**. This is a file or stock val image, not the laptop webcam.

Say: *We can drop a photo — not the laptop camera. Upload or pick gauze, glove, syringe… The model names the class and which bin. On the real table we only servo yellow and red; cotton, mask, and glove are what wards hand us.*

| Result on screen | What to say |
|---|---|
| **Yellow bin · servo LEFT** | Soiled waste — gauze or cotton path. On the Uno that is command LEFT into the yellow bag. |
| **Red bin · servo RIGHT** | Contaminated plastic — glove, syringe, urine bag, test tube. Command RIGHT into red. |
| **HOLD · gate shut** | Not sure, unknown class, or a hold class like glasses. The flap stays shut. White sharps and blue glass are **by hand** after hold — this Uno only drives yellow and red. |

Point at the result card (class, confidence, bin). The chute SVG moves from the same live event as hardware would log.

## Processed

Point at **Not processed**.

Say: another station tells us if the bag was processed. We do not draw a cycle ourselves.

## If they ask

Why not four bins on the table? The Uno has one servo and two bins. The twin shows the other two colours as a handoff.

Did the model see needles? No. Do not put a needle in the chute. A syringe photo in the training set is a barrel with the needle off, and that class goes red.

Is 99% the ward accuracy? No. 680 of 687 is the model's own validation photos, split from the same download.

Why cotton not gauze? Same yellow path. Cotton is what wards actually hand you; the photos are gauze.
