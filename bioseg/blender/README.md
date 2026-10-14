# BioSeg Blender rig — full-scope digital twin

Exact two-stage BMW sorter for the **digital twin**. Hardware demo only builds stage 1.

## Mechanism

| Stage | Object | Motion | Meaning |
|---|---|---|---|
| 1 (Uno) | `FlapPivot` | −42° Z | LEFT → yellow (soiled) |
| 1 (Uno) | `FlapPivot` | +42° Z | RIGHT → red (contaminated plastic) |
| 1 (Uno) | `FlapPivot` | 0° | Pass into stage 2 / HOLD on hardware |
| 2 (twin only) | `Stage2Pivot` | −42° Z | White (sharps) |
| 2 (twin only) | `Stage2Pivot` | +42° Z | Blue (glassware) |

Caption on the model: *Twin: full sorter · Hardware demo: stage 1 only.*

## Build

```bash
cd /home/mudassir/Projects/biobyte
blender --background --python bioseg/blender/build_bioseg_rig.py
```

Writes:

- `bioseg/blender/bioseg_rig.blend`
- `bioseg/blender/exports/bioseg_rig.glb`
- `bioseg/blender/exports/preview.png`

## Consumers

- Godot: import GLB, animate `FlapPivot` + `Stage2Pivot`
- Website: Three.js loads the same GLB from `/static/bioseg_rig.glb`
