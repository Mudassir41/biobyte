class_name WasteItems
extends RefCounted
## Procedural waste units for the BioSeg twin:
## a simple mesh with a real photo on the face the chute camera sees.
## The ONNX model was trained on photos, so the twin shows those photos on the board.

const SPECS := {
	"gauze": {"label": "Gauze", "tint": Color(0.95, 0.93, 0.88), "tex": "res://assets/waste/gauze.jpg"},
	"glove": {"label": "Glove", "tint": Color(0.25, 0.35, 0.45), "tex": "res://assets/waste/glove.jpg"},
	"syringe": {"label": "Syringe", "tint": Color(0.85, 0.88, 0.92), "tex": "res://assets/waste/syringe.jpg"},
	"urine_bag": {"label": "Urine bag", "tint": Color(0.90, 0.88, 0.70), "tex": "res://assets/waste/urine_bag.jpg"},
	"test_tube": {"label": "Test tube", "tint": Color(0.70, 0.82, 0.88), "tex": "res://assets/waste/test_tube.jpg"},
	"glasses": {"label": "Glasses", "tint": Color(0.55, 0.58, 0.62), "tex": "res://assets/waste/glasses.jpg"},
	"hold": {"label": "Hold sample", "tint": Color(0.55, 0.58, 0.62), "tex": "res://assets/waste/hold.jpg"},
}


static func make(kind: String) -> Node3D:
	var key := kind if SPECS.has(kind) else "hold"
	var spec: Dictionary = SPECS[key]
	var root := Node3D.new()
	root.name = "Waste_" + key
	root.set_meta("kind", key)

	# Soft pack body.
	var body := MeshInstance3D.new()
	var box := BoxMesh.new()
	box.size = Vector3(0.14, 0.10, 0.04)
	body.mesh = box
	var body_mat := StandardMaterial3D.new()
	body_mat.albedo_color = spec["tint"]
	body_mat.roughness = 0.7
	body.material_override = body_mat
	body.position = Vector3(0, 0, 0)
	root.add_child(body)

	# Photo facing the chute camera (local +Z toward camera looking from +Z).
	var face := MeshInstance3D.new()
	var quad := QuadMesh.new()
	quad.size = Vector2(0.13, 0.09)
	face.mesh = quad
	var face_mat := StandardMaterial3D.new()
	var tex = load(spec["tex"])
	if tex != null:
		face_mat.albedo_texture = tex
	else:
		face_mat.albedo_color = spec["tint"]
	face_mat.roughness = 0.55
	face.material_override = face_mat
	face.position = Vector3(0, 0, 0.022)
	root.add_child(face)

	var tag := Label3D.new()
	tag.text = spec["label"]
	tag.font_size = 28
	tag.position = Vector3(0, 0.08, 0)
	tag.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	tag.modulate = Color(0.95, 0.94, 0.9)
	root.add_child(tag)
	return root
