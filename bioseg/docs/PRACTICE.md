# What this is for

Biomedical waste is supposed to leave a ward in four colours (BMW Rules 2016, Schedule I):

| Colour | Schedule I (BMW Rules 2016) | On this rig |
|---|---|---|
| Yellow | Soiled waste (blood/fluid dressings, gauze, cotton), plus other yellow categories we do not demo | Servo left → physical yellow bag |
| Red | Contaminated recyclable plastic: gloves, IV tubing, urine bags, syringes **without** needles | Servo right → physical red bag |
| White (translucent) | Sharps: needles, blades, syringes **with** needle — puncture-proof container | No bin. Gate shut; person moves it |
| Blue | Contaminated glassware (vials, ampoules), metallic implants — blue-marked box | No bin. Gate shut; person moves it |

The failure that matters is a person putting the wrong thing in a bag. A needle in the red bag is a needlestick later. A glove in the yellow bag is plastic that will not be recycled and waste that will be treated on the wrong path.

## What this build actually does

It watches one chute. A camera classifies the item. One servo on an Arduino Uno either drops it into a yellow bin, drops it into a red bin, or leaves the gate shut. The dashboard writes down where it went. If another station later reports that the bag was processed, the page says Processed. Until that message arrives, the page says Not processed.

That is a useful check at the moment of discard for the two soft categories the model has seen, plus a refusal when it is not sure. The refusal is the safety behaviour. The record is a local chain of hashes so a row cannot be quietly edited.

## What it does not do

- The Uno has two bins. White and blue are not on the machine. A held item stays in the chute until a person moves it.
- The model has six classes: gauze, glove, medical_glasses, syringe, test_tube, urine_bag. It has never been trained on needles, blades, or glass vials.
- `medical_glasses` is safety goggles in the photo set, not a vial. It is mapped to hold so a wrong guess stays in the chute. Do not call it glass recognition.
- `syringe` is mapped to red, because the training photos are needle-off barrels. A real syringe with the needle still on can be called syringe and sent red. Do not drop a real needle.
- On its own validation photos, the same download it was trained from, it got 680 of 687 right (14 Oct 2026, ONNX, CPU). That number is not a result from this chute or this camera.
- It does not sterilise anything and it does not invent a temperature or an autoclave result.
- The digital twin buttons do not command the Arduino.

## Is the problem statement useful?

The segregation problem is real, and a ward does need the four colours. An automatic claim that a camera and one servo "solve BMW compliance" is not useful. A clinic still needs the white and blue containers, and a person still has to put sharps and glass in them.

What is useful in practice is narrower:

- Catch gauze versus a glove, tube, or bag at the bin, instead of trusting memory.
- Refuse to drop when the picture is not one of those classes or the score is low.
- Keep a list of where each item went, and show a processed flag only when another device reports it.

Shown that way, the demo matches a job a small ward can actually use. Shown as a four-bin robot that sees needles and proves sterilisation, it does not.

## What to put in the chute

Live hardware props: cotton, surgical mask, nitrile glove. Cotton takes the yellow path (same as gauze in the photos). Glove takes red. Mask is not in the training set — expect HOLD. Digital twin shows all six trained classes. Do not use a real needle. Do not use a shoe cover, a cap, or goggles and call the result blue.

## Two views

How to start each part: `bioseg/docs/RUN.md`.

Live hardware, on the dashboard: the camera from motionEye on the DietPi (port 9081), the two bins, and the hold. Open `http://<pi-ip>:8000`.

Digital twin, on the same page: **Digital twin**. Model buttons cover gauze, glove, syringe, urine bag, test tube, and glasses (hold). Mechanism buttons only move the drawing. Neither opens the laptop camera.

The same machine in 3D is `bioseg/godot` (Godot 4.7). Open that folder and press Play — main scene is `scenes/rig.tscn`. Place a digital sample → chute camera JPEG → real ONNX → bins move. The old supermarket twin was moved to `bioseg/_archive_visionops_shop`.

## On the Pi

motionEye already owns the camera. Start it, then the server, then capture. Details are in `bioseg/edge/deploy.md`. Do not open the camera from Python while motionEye is running. Do not put this server on port 8765. That port is motionEye's page.
