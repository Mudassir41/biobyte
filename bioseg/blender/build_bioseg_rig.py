"""Build the BioSeg full-scope digital twin sorter in Blender (bpy).

Twin = full BMW four-bin tabletop machine (two stages):
  Stage 1 FlapPivot  (maps to Uno servo D9): -42° yellow / +42° red / 0° pass to stage 2
  Stage 2 Stage2Pivot (twin-only):           -42° white (sharps) / +42° blue (glassware)

Run:
  blender --background --python bioseg/blender/build_bioseg_rig.py
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parent
EXPORT = ROOT / "exports"
BLEND = ROOT / "bioseg_rig.blend"
COLLECTION = "BioSegRig"
FLAP_ANGLE = math.radians(42.0)

COL = {
    "steel": (0.45, 0.48, 0.52, 1.0),
    "steel_dark": (0.16, 0.17, 0.19, 1.0),
    "table": (0.09, 0.09, 0.10, 1.0),
    "glass": (0.62, 0.68, 0.72, 0.30),
    "yellow": (0.88, 0.66, 0.12, 1.0),
    "red": (0.74, 0.16, 0.15, 1.0),
    "white": (0.90, 0.90, 0.88, 1.0),
    "blue": (0.16, 0.34, 0.66, 1.0),
    "brass": (0.55, 0.42, 0.18, 1.0),
    "uno": (0.04, 0.45, 0.62, 1.0),
    "act2": (0.25, 0.28, 0.34, 1.0),
    "ir": (0.92, 0.20, 0.16, 1.0),
    "ink_light": (0.93, 0.93, 0.90, 1.0),
}

M: dict[str, bpy.types.Material] = {}


# ---------------------------------------------------------------- helpers


def clear_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    if COLLECTION in bpy.data.collections:
        bpy.data.collections.remove(bpy.data.collections[COLLECTION])
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.curves, bpy.data.cameras, bpy.data.lights):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


def safe_name(col: bpy.types.Collection, name: str) -> str:
    """Unique object name so pivots keep exact GLB node names."""
    existing = {o.name for o in col.all_objects}
    if name not in existing:
        return name
    n = 1
    while f"{name}.{n:03d}" in existing:
        n += 1
    return f"{name}.{n:03d}"


def mat(name: str, rgba: tuple[float, float, float, float], *, metallic: float = 0.0,
        roughness: float = 0.45, emission: tuple[float, float, float] | None = None,
        alpha: bool = False) -> bpy.types.Material:
    if name in M:
        return M[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    if alpha:
        m.blend_method = "BLEND"
        if hasattr(m, "shadow_method"):
            m.shadow_method = "HASHED"
        bsdf.inputs["Alpha"].default_value = rgba[3]
    if emission is not None:
        sock = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
        if sock is not None:
            sock.default_value = (*emission, 1.0)
        strength = bsdf.inputs.get("Emission Strength")
        if strength is not None:
            strength.default_value = 4.0
    M[name] = m
    return m


def link(obj: bpy.types.Object, col: bpy.types.Collection) -> bpy.types.Object:
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    col.objects.link(obj)
    return obj


def apply_bevel(obj: bpy.types.Object, width: float, segments: int = 3) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.object.shade_smooth_by_angle()
    except Exception:
        for p in obj.data.polygons:
            p.use_smooth = True
    mod = obj.modifiers.new("Bevel", "BEVEL")
    mod.width = width
    mod.segments = segments
    mod.limit_method = "ANGLE"
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception:
        pass
    bpy.ops.object.select_all(action="DESELECT")


def box(col: bpy.types.Collection, name: str, size: tuple[float, float, float],
        loc: tuple[float, float, float], material: bpy.types.Material,
        parent: bpy.types.Object | None = None, bevel: float = 0.0,
        rot_z: float = 0.0) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name + "Mesh")
    sx, sy, sz = (d * 0.5 for d in size)
    verts = [
        (-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
        (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz),
    ]
    faces = [
        (0, 1, 2, 3), (4, 7, 6, 5),
        (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7),
    ]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.materials.append(material)
    obj = bpy.data.objects.new(safe_name(col, name), mesh)
    if rot_z:
        obj.rotation_euler.z = rot_z
    if parent is not None:
        obj.parent = parent
        obj.location = loc
    else:
        obj.location = loc
    link(obj, col)
    if bevel > 0.0:
        apply_bevel(obj, min(bevel, min(size) * 0.45))
    return obj


def plate(col: bpy.types.Collection, name: str, outline: list[tuple[float, float]],
          thickness: float, loc: tuple[float, float, float], material: bpy.types.Material,
          parent: bpy.types.Object | None = None, bevel: float = 0.0) -> bpy.types.Object:
    """Extruded polygon in XZ (thin in Y) — real diverter-plate silhouette."""
    n = len(outline)
    hy = thickness * 0.5
    verts = [(x, -hy, z) for (x, z) in outline] + [(x, hy, z) for (x, z) in outline]
    faces = []
    faces.append(tuple(range(n - 1, -1, -1)))
    faces.append(tuple(range(n, 2 * n)))
    for i in range(n):
        j = (i + 1) % n
        faces.append((i, j, n + j, n + i))
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.materials.append(material)
    obj = bpy.data.objects.new(safe_name(col, name), mesh)
    if parent is not None:
        obj.parent = parent
        obj.location = loc
    else:
        obj.location = loc
    link(obj, col)
    if bevel > 0.0:
        apply_bevel(obj, min(bevel, thickness * 0.45))
    return obj


def empty(col: bpy.types.Collection, name: str, loc: tuple[float, float, float],
          *, display: str = "PLAIN_AXES", size: float = 0.0) -> bpy.types.Object:
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = display
    obj.empty_display_size = size
    obj.hide_render = True
    obj.location = loc
    return link(obj, col)


def text_obj(col: bpy.types.Collection, name: str, body: str,
             loc: tuple[float, float, float], rot: tuple[float, float, float],
             size: float, material: bpy.types.Material, extrude: float = 0.0015,
             align: str = "CENTER") -> bpy.types.Object:
    curve = bpy.data.curves.new(name + "Curve", type="FONT")
    curve.body = body
    curve.size = size
    curve.align_x = align
    curve.extrude = extrude
    curve.bevel_depth = 0.0
    curve.materials.append(material)
    obj = bpy.data.objects.new(safe_name(col, name), curve)
    obj.location = loc
    obj.rotation_euler = rot
    return link(obj, col)


FRONT = (math.radians(90), 0.0, 0.0)  # text facing -Y (camera side)


def chute(col: bpy.types.Collection, name: str, size: tuple[float, float, float],
          loc: tuple[float, float, float], rot: tuple[float, float, float],
          material: bpy.types.Material) -> bpy.types.Object:
    """Angled open-top slide (floor + two side walls)."""
    root = empty(col, name + "Root", loc, size=0.0)
    root.rotation_euler = rot
    sx, sy, sz = size
    box(col, name + "Floor", (sx, sy, 0.008), (0.0, 0.0, 0.004), material, parent=root, bevel=0.003)
    box(col, name + "WallL", (0.010, sy, sz), (-sx * 0.5 + 0.005, 0.0, sz * 0.5), material, parent=root, bevel=0.002)
    box(col, name + "WallR", (0.010, sy, sz), (sx * 0.5 - 0.005, 0.0, sz * 0.5), material, parent=root, bevel=0.002)
    return root


def stage_banner(col: bpy.types.Collection, name: str, title: str, subtitle: str,
                 loc: tuple[float, float, float], accent: bpy.types.Material,
                 ink: bpy.types.Material) -> None:
    box(col, name + "Plate", (0.52, 0.008, 0.11), loc, accent, bevel=0.003)
    lx, ly, lz = loc
    text_obj(col, name + "Title", title, (lx, ly - 0.006, lz + 0.038), FRONT, 0.048, ink, extrude=0.0012)
    text_obj(col, name + "Sub", subtitle, (lx, ly - 0.006, lz + 0.012), FRONT, 0.022, ink, extrude=0.0008)


# ---------------------------------------------------------------- build


def build() -> None:
    clear_scene()
    col = bpy.data.collections.new(COLLECTION)
    bpy.context.scene.collection.children.link(col)

    m_table = mat("Mat_Table", COL["table"], roughness=0.9)
    m_steel = mat("Mat_Steel", COL["steel"], metallic=0.85, roughness=0.35)
    m_steel_dark = mat("Mat_SteelDark", COL["steel_dark"], metallic=0.7, roughness=0.5)
    m_glass = mat("Mat_Glass", COL["glass"], roughness=0.15, alpha=True)
    m_brass = mat("Mat_Brass", COL["brass"], metallic=0.9, roughness=0.3)
    m_blade = mat("Mat_Blade", (0.80, 0.83, 0.87, 1.0), metallic=0.9, roughness=0.28)
    m_blade2 = mat("Mat_Blade2", (0.70, 0.78, 0.88, 1.0), metallic=0.9, roughness=0.28)
    m_uno = mat("Mat_Uno", COL["uno"], roughness=0.5)
    m_act2 = mat("Mat_Act2", COL["act2"], metallic=0.4, roughness=0.5)
    m_ir = mat("Mat_IR", COL["ir"], emission=(1.0, 0.12, 0.08))
    m_ink_light = mat("Mat_InkLight", COL["ink_light"], roughness=0.6)
    m_servo_horn = mat("Mat_Horn", (0.85, 0.85, 0.82, 1.0), roughness=0.4)
    m_stage1_tint = mat("Mat_Stage1Tint", (0.12, 0.22, 0.14, 0.55), roughness=0.85, alpha=True)
    m_stage2_tint = mat("Mat_Stage2Tint", (0.14, 0.16, 0.28, 0.55), roughness=0.85, alpha=True)
    m_chute = mat("Mat_Chute", (0.28, 0.30, 0.34, 1.0), metallic=0.35, roughness=0.45)
    m_banner1 = mat("Mat_Banner1", (0.20, 0.38, 0.22, 1.0), roughness=0.55)
    m_banner2 = mat("Mat_Banner2", (0.22, 0.26, 0.48, 1.0), roughness=0.55)
    m_control = mat("Mat_Control", (0.12, 0.13, 0.15, 1.0), roughness=0.7)

    # --- tabletop + frame -------------------------------------------------
    box(col, "WorkTop", (2.35, 1.30, 0.05), (0.0, 0.0, -0.025), m_table, bevel=0.012)
    box(col, "Stage1Zone", (1.05, 0.95, 0.002), (-0.58, 0.08, 0.002), m_stage1_tint)
    box(col, "Stage2Zone", (1.05, 0.95, 0.002), (0.58, 0.08, 0.002), m_stage2_tint)
    box(col, "StageDivider", (0.012, 0.95, 0.22), (0.0, 0.08, 0.11), m_steel_dark, bevel=0.003)
    box(col, "FootFL", (0.10, 0.10, 0.12), (-1.05, -0.55, -0.11), m_steel_dark, bevel=0.008)
    box(col, "FootFR", (0.10, 0.10, 0.12), (1.05, -0.55, -0.11), m_steel_dark, bevel=0.008)
    box(col, "FootBL", (0.10, 0.10, 0.12), (-1.05, 0.55, -0.11), m_steel_dark, bevel=0.008)
    box(col, "FootBR", (0.10, 0.10, 0.12), (1.05, 0.55, -0.11), m_steel_dark, bevel=0.008)

    # --- stage 1: input chute ---------------------------------------------
    CX = -0.62  # stage-1 centreline x
    box(col, "ChuteFloor", (0.34, 0.34, 0.012), (CX, -0.02, 1.245), m_steel_dark, bevel=0.004)
    box(col, "ChuteWallLeft", (0.012, 0.34, 0.20), (CX - 0.176, -0.02, 1.35), m_glass, bevel=0.003)
    box(col, "ChuteWallRight", (0.012, 0.34, 0.20), (CX + 0.176, -0.02, 1.35), m_glass, bevel=0.003)
    box(col, "ChuteWallBack", (0.364, 0.012, 0.20), (CX, 0.156, 1.35), m_glass, bevel=0.003)
    box(col, "ChuteLip", (0.364, 0.012, 0.05), (CX, -0.196, 1.275), m_glass, bevel=0.003)
    box(col, "InfeedRamp", (0.30, 0.22, 0.010), (CX, 0.02, 1.46), m_steel,
        bevel=0.004)  # visual hopper lip above chute
    box(col, "InfeedRampWallL", (0.012, 0.22, 0.09), (CX - 0.156, 0.02, 1.50), m_steel, bevel=0.003)
    box(col, "InfeedRampWallR", (0.012, 0.22, 0.09), (CX + 0.156, 0.02, 1.50), m_steel, bevel=0.003)

    # IR break-beam pair across the chute (Uno D2)
    box(col, "IRLeft", (0.035, 0.035, 0.035), (CX - 0.19, -0.02, 1.33), m_ir, bevel=0.006)
    box(col, "IRRight", (0.035, 0.035, 0.035), (CX + 0.19, -0.02, 1.33), m_ir, bevel=0.006)
    box(col, "IRBeam", (0.345, 0.004, 0.004), (CX, -0.02, 1.33), m_ir)

    # --- stage 1 flap: FlapPivot + FlapBlade -------------------------------
    pivot = empty(col, "FlapPivot", (CX, -0.02, 1.12))
    pivot["maps_to"] = "Arduino Uno servo D9"
    pivot["angles"] = "-0.73 rad = BinYellow, +0.73 rad = BinRed, 0 = pass to stage 2"
    plate(col, "FlapBlade",
          [(-0.14, -0.46), (0.14, -0.46), (0.14, -0.10), (0.06, 0.0), (-0.06, 0.0)],
          0.014, (0.0, 0.0, -0.02), m_blade, parent=pivot, bevel=0.004)
    box(col, "FlapHub", (0.09, 0.12, 0.07), (0.0, 0.0, 0.02), m_brass, parent=pivot, bevel=0.006)
    box(col, "FlapShaft", (0.018, 0.018, 0.09), (0.0, 0.0, 0.05), m_steel, parent=pivot, bevel=0.003)
    box(col, "FlapHorn", (0.08, 0.022, 0.012), (0.05, 0.0, 0.02), m_servo_horn, parent=pivot, bevel=0.002)
    pivot.rotation_euler.z = -FLAP_ANGLE  # shown in BinYellow pose

    # Uno + servo that drive stage 1 (enclosed so it reads as electronics bay, not floor clutter)
    box(col, "ServoBody", (0.055, 0.12, 0.11), (CX + 0.21, -0.02, 1.10), m_brass, bevel=0.006)
    box(col, "ServoBracket", (0.012, 0.16, 0.14), (CX + 0.255, -0.02, 1.10), m_steel_dark, bevel=0.003)
    ctrl = empty(col, "ControlBay", (-1.02, -0.48, 0.0), size=0.0)
    box(col, "ControlBase", (0.22, 0.18, 0.008), (0.0, 0.0, 0.004), m_control, parent=ctrl, bevel=0.003)
    box(col, "ControlWallL", (0.012, 0.18, 0.07), (-0.104, 0.0, 0.039), m_steel_dark, parent=ctrl, bevel=0.002)
    box(col, "ControlWallR", (0.012, 0.18, 0.07), (0.104, 0.0, 0.039), m_steel_dark, parent=ctrl, bevel=0.002)
    box(col, "ControlWallBack", (0.22, 0.012, 0.07), (0.0, 0.084, 0.039), m_steel_dark, parent=ctrl, bevel=0.002)
    box(col, "Uno", (0.10, 0.145, 0.018), (0.0, -0.02, 0.018), m_uno, parent=ctrl, bevel=0.003)
    box(col, "UnoUSB", (0.018, 0.026, 0.014), (-0.052, -0.055, 0.031), m_steel, parent=ctrl, bevel=0.002)
    text_obj(col, "UnoLabel", "UNO · D9 flap · D2 IR", (-1.02, -0.535, 0.095), FRONT, 0.028, m_ink_light, extrude=0.001)

    # --- pass tunnel: stage-1 centre -> stage-2 hopper ---------------------
    box(col, "PassFloor", (0.48, 0.30, 0.010), (0.08, 0.0, 0.895), m_steel_dark, bevel=0.004)
    box(col, "PassWallLeft", (0.012, 0.30, 0.16), (-0.166, 0.0, 0.975), m_glass, bevel=0.003)
    box(col, "PassWallRight", (0.012, 0.30, 0.16), (0.326, 0.0, 0.975), m_glass, bevel=0.003)
    box(col, "PassRoof", (0.50, 0.30, 0.010), (0.08, 0.0, 1.05), m_glass, bevel=0.003)

    # --- stage 2: hopper + diverter ----------------------------------------
    SX = 0.50  # stage-2 centreline x
    box(col, "Stage2Floor", (0.30, 0.30, 0.012), (SX, 0.0, 0.695), m_steel_dark, bevel=0.004)
    box(col, "Stage2WallLeft", (0.012, 0.30, 0.18), (SX - 0.156, 0.0, 0.79), m_glass, bevel=0.003)
    box(col, "Stage2WallRight", (0.012, 0.30, 0.18), (SX + 0.156, 0.0, 0.79), m_glass, bevel=0.003)
    box(col, "Stage2WallBack", (0.324, 0.012, 0.18), (SX, 0.156, 0.79), m_glass, bevel=0.003)
    box(col, "Stage2Lip", (0.324, 0.012, 0.05), (SX, -0.156, 0.725), m_glass, bevel=0.003)

    stage2 = empty(col, "Stage2Pivot", (SX, 0.0, 0.62))
    stage2["maps_to"] = "twin only (no Uno channel)"
    stage2["angles"] = "-0.73 rad = BinWhite (sharps), +0.73 rad = BinBlue (glassware)"
    plate(col, "Stage2Blade",
          [(-0.125, -0.34), (0.125, -0.34), (0.125, -0.10), (0.05, 0.0), (-0.05, 0.0)],
          0.014, (0.0, 0.0, -0.02), m_blade2, parent=stage2, bevel=0.004)
    box(col, "Stage2Hub", (0.08, 0.11, 0.065), (0.0, 0.0, 0.02), m_brass, parent=stage2, bevel=0.006)
    box(col, "Stage2Shaft", (0.018, 0.018, 0.08), (0.0, 0.0, 0.05), m_steel, parent=stage2, bevel=0.003)
    stage2.rotation_euler.z = FLAP_ANGLE  # shown in BinBlue pose

    box(col, "Stage2Actuator", (0.06, 0.10, 0.09), (SX + 0.19, 0.0, 0.61), m_act2, bevel=0.006)
    box(col, "Stage2Bracket", (0.012, 0.14, 0.12), (SX + 0.23, 0.0, 0.61), m_steel_dark, bevel=0.003)
    box(col, "Stage2Horn", (0.05, 0.03, 0.03), (SX + 0.14, 0.0, 0.62), m_servo_horn, bevel=0.004)

    # --- visible drop chutes (each runs from pivot zone toward its bin front) -
    chute(col, "ChuteYellow", (0.14, 0.36, 0.18), (-0.78, 0.12, 0.78), (math.radians(-38), 0.0, math.radians(-8)), m_chute)
    chute(col, "ChuteRed", (0.14, 0.36, 0.18), (-0.36, 0.12, 0.78), (math.radians(-38), 0.0, math.radians(8)), m_chute)
    chute(col, "ChuteWhite", (0.14, 0.34, 0.16), (0.28, 0.12, 0.48), (math.radians(-42), 0.0, math.radians(-10)), m_chute)
    chute(col, "ChuteBlue", (0.14, 0.34, 0.16), (0.74, 0.12, 0.48), (math.radians(-42), 0.0, math.radians(10)), m_chute)

    stage_banner(col, "BannerStage1", "STAGE 1", "infeed · IR · FlapPivot (Uno D9)",
                 (-0.62, -0.58, 0.06), m_banner1, m_ink_light)
    stage_banner(col, "BannerStage2", "STAGE 2", "Stage2Pivot · sharps / glassware",
                 (0.50, -0.58, 0.06), m_banner2, m_ink_light)

    # --- frame uprights (machine frame, not bin spines) ---------------------
    for name, x, z_top in (
        ("PostA", CX - 0.30, 1.30), ("PostB", CX + 0.30, 1.30),
        ("PostC", SX - 0.22, 0.90), ("PostD", SX + 0.22, 0.90),
    ):
        box(col, name, (0.035, 0.035, z_top), (x, -0.12, z_top * 0.5), m_steel, bevel=0.006)
    box(col, "CrossRail1", (0.035 + 0.60, 0.035, 0.035), (CX, -0.12, 1.30), m_steel, bevel=0.006)
    box(col, "CrossRail2", (0.44, 0.035, 0.035), (SX, -0.12, 0.90), m_steel, bevel=0.006)
    box(col, "BackRail", (2.0, 0.035, 0.035), (0.0, 0.52, 0.55), m_steel, bevel=0.006)

    # --- bins ---------------------------------------------------------------
    BIN_W, BIN_D, BIN_H, T = 0.42, 0.40, 0.30, 0.016
    bins = [
        ("BinYellow", (-0.80, 0.28, 0.0), "yellow", "YELLOW", "soiled · stage1 left"),
        ("BinRed", (-0.34, 0.28, 0.0), "red", "RED", "plastic · stage1 right"),
        ("BinWhite", (0.32, 0.28, 0.0), "white", "WHITE", "sharps · stage2 left"),
        ("BinBlue", (0.80, 0.28, 0.0), "blue", "BLUE", "glassware · stage2 right"),
    ]
    for name, (bx, by, bz), key, title, sub in bins:
        root = empty(col, name, (bx, by, bz))
        m_bin = mat(f"Mat_{name}", COL[key], roughness=0.55)
        m_bin_dark = mat(f"Mat_{name}_dark",
                         (COL[key][0] * 0.55, COL[key][1] * 0.55, COL[key][2] * 0.55, 1.0),
                         roughness=0.65)
        ink = m_steel_dark if key == "white" else m_ink_light
        box(col, f"{name}_Floor", (BIN_W, BIN_D, T), (0, 0, T * 0.5), m_bin_dark, parent=root, bevel=0.004)
        box(col, f"{name}_Back", (BIN_W, T, BIN_H), (0, BIN_D * 0.5, BIN_H * 0.5), m_bin, parent=root, bevel=0.004)
        box(col, f"{name}_Left", (T, BIN_D, BIN_H), (-BIN_W * 0.5, 0, BIN_H * 0.5), m_bin, parent=root, bevel=0.004)
        box(col, f"{name}_Right", (T, BIN_D, BIN_H), (BIN_W * 0.5, 0, BIN_H * 0.5), m_bin, parent=root, bevel=0.004)
        # low front wall so the bin reads as "open" and the label stays readable
        box(col, f"{name}_Front", (BIN_W, T, 0.14), (0, -BIN_D * 0.5, 0.07), m_bin, parent=root, bevel=0.004)
        text_obj(col, f"{name}_Title", title, (0, -BIN_D * 0.5 - T - 0.004, 0.12), FRONT,
                 0.068, ink, extrude=0.0015).parent = root
        text_obj(col, f"{name}_Sub", sub, (0, -BIN_D * 0.5 - T - 0.004, 0.045), FRONT,
                 0.030, ink, extrude=0.001).parent = root

    # --- caption (facing camera) --------------------------------------------
    text_obj(col, "Caption",
             "Twin: full sorter · Hardware demo: stage 1 only",
             (0.0, -0.615, 0.02), FRONT, 0.042, m_ink_light, extrude=0.0012)

    # --- camera marker for the overhead inspection cam ----------------------
    cam_mark = empty(col, "CamChute", (CX, -0.30, 1.55), display="SINGLE_ARROW", size=0.0)
    cam_mark.rotation_euler = (math.radians(60), 0.0, 0.0)

    # --- preview camera + lights --------------------------------------------
    cam_data = bpy.data.cameras.new("PreviewCam")
    cam_data.lens = 42
    cam_data.clip_start = 0.05
    cam_data.clip_end = 100.0
    preview = bpy.data.objects.new("PreviewCam", cam_data)
    preview.location = (0.0, -2.85, 1.95)
    link(preview, col)
    direction = Vector((0.0, 0.05, 0.72)) - Vector(preview.location)
    preview.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = preview

    key_data = bpy.data.lights.new(name="KeyLight", type="AREA")
    key_data.energy = 900.0
    key_data.shape = "DISK"
    key_data.size = 2.2
    key = bpy.data.objects.new("KeyLight", key_data)
    key.location = (-1.4, -1.6, 3.2)
    key.rotation_euler = (math.radians(25), 0.0, math.radians(-25))
    link(key, col)

    fill_data = bpy.data.lights.new(name="FillLight", type="AREA")
    fill_data.energy = 500.0
    fill_data.size = 2.5
    fill = bpy.data.objects.new("FillLight", fill_data)
    fill.location = (2.2, -0.6, 2.2)
    fill.rotation_euler = (math.radians(35), 0.0, math.radians(120))
    link(fill, col)

    rim_data = bpy.data.lights.new(name="RimLight", type="AREA")
    rim_data.energy = 700.0
    rim_data.size = 1.5
    rim = bpy.data.objects.new("RimLight", rim_data)
    rim.location = (0.2, 1.8, 2.6)
    rim.rotation_euler = (math.radians(55), 0.0, math.radians(180))
    link(rim, col)

    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.035, 0.038, 0.045, 1.0)
        bg.inputs[1].default_value = 0.5

    bpy.context.view_layer.update()


# ---------------------------------------------------------------- export


def export_all() -> None:
    EXPORT.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))

    col = bpy.data.collections[COLLECTION]
    bpy.ops.object.select_all(action="DESELECT")
    for obj in col.all_objects:
        obj.select_set(True)
    if col.all_objects:
        bpy.context.view_layer.objects.active = col.all_objects[0]

    glb = EXPORT / "bioseg_rig.glb"
    bpy.ops.export_scene.gltf(
        filepath=str(glb),
        export_format="GLB",
        use_selection=True,
        export_apply=False,
        export_yup=True,
    )

    scene = bpy.context.scene
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.filepath = str(EXPORT / "preview.png")
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        try:
            scene.render.engine = "BLENDER_EEVEE"
        except Exception:
            scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.look = "AgX - Medium High Contrast"
    bpy.ops.render.render(write_still=True)

    # verify exact pivot node names survived the GLB round-trip
    names = set()
    with open(glb, "rb") as fh:
        blob = fh.read()
    for token in re.findall(rb'"name"\s*:\s*"([^"]+)"', blob):
        names.add(token.decode("utf-8", "replace"))
    ok = {"FlapPivot", "Stage2Pivot", "FlapBlade", "Stage2Blade"}.issubset(names)
    print(f"GLB node check: FlapPivot={'FlapPivot' in names} "
          f"Stage2Pivot={'Stage2Pivot' in names} "
          f"FlapBlade={'FlapBlade' in names} Stage2Blade={'Stage2Blade' in names}")
    if not ok:
        raise RuntimeError("required pivot node names missing from GLB")


def main() -> None:
    build()
    export_all()
    print("BioSeg full-scope rig written:")
    print(" ", BLEND)
    print(" ", EXPORT / "bioseg_rig.glb")
    print(" ", EXPORT / "preview.png")
    print("FlapPivot: -42 deg yellow / +42 deg red / 0 deg pass -> stage2 (Uno D9)")
    print("Stage2Pivot: -42 deg white / +42 deg blue (twin only)")


if __name__ == "__main__":
    main()
