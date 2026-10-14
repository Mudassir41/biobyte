extends Node3D
## BioSeg twin closed loop:
## place a digital waste sample → chute camera JPEG → real ONNX → flap / bins move.
## Yellow and red are the Uno. White and blue are a person after a hold.
## Twin shows all trained classes. Live hardware demo uses cotton / mask / glove.

const YELLOW := Color(0.90, 0.71, 0.13)
const RED := Color(0.83, 0.32, 0.30)
const WHITE := Color(0.93, 0.92, 0.88)
const BLUE := Color(0.35, 0.52, 0.78)
const STEEL := Color(0.22, 0.24, 0.26)
const BRASS := Color(0.72, 0.58, 0.28)
const CHUTE_X := -0.28
const SCRIPT := ["gauze", "glove", "syringe", "urine_bag", "test_tube", "glasses"]

var flap_pivot: Node3D
var piece: Node3D
var status_label: Label
var count_label: Label
var link: Node
var http: HTTPRequest
var chute_vp: SubViewport
var chute_cam: Camera3D
var preview: TextureRect
var _tween: Tween
var _counts := {"yellow": 0, "red": 0, "white": 0, "blue": 0, "held": 0}
var _busy := false
var _server := "http://127.0.0.1:8000"
var _auto := false
var _script_i := 0
var _script_t := 0.0
var _await_route := false

func _ready() -> void:
	_build_world()
	_build_machine()
	_build_chute_camera()
	_build_ui()
	http = HTTPRequest.new()
	http.timeout = 20.0
	add_child(http)
	http.request_completed.connect(_on_http_done)
	link = get_node_or_null("BioSegLink")
	if link:
		link.classified.connect(_on_classified)
		link.routed.connect(_on_routed)
		link.held.connect(_on_held)
	_set_status("BioSeg closed loop: place a sample → chute camera → real ONNX → bins move. Server on :8000.")

func _process(delta: float) -> void:
	if not _auto or _busy:
		return
	_script_t -= delta
	if _script_t > 0.0:
		return
	var kind: String = SCRIPT[_script_i % SCRIPT.size()]
	_script_i += 1
	_script_t = 4.5
	_place_and_classify(kind)

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		match event.keycode:
			KEY_1:
				_place_and_classify("gauze")
			KEY_2:
				_place_and_classify("glove")
			KEY_3:
				_place_and_classify("syringe")
			KEY_4:
				_place_and_classify("urine_bag")
			KEY_5:
				_place_and_classify("test_tube")
			KEY_6:
				_place_and_classify("glasses")
			KEY_T:
				_auto = not _auto
				_script_t = 0.2
				_set_status("Auto script %s (all six classes)" % ("ON" if _auto else "OFF"))
			KEY_R:
				_reset()

func _build_world() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.11, 0.12, 0.11)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.62, 0.60, 0.54)
	env.ambient_light_energy = 0.55
	var world := WorldEnvironment.new()
	world.environment = env
	add_child(world)

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-48, 32, 0)
	sun.shadow_enabled = true
	sun.light_energy = 1.15
	add_child(sun)

	var cam := Camera3D.new()
	cam.position = Vector3(1.55, 1.62, 2.55)
	cam.current = true
	add_child(cam)
	cam.look_at(Vector3(0.05, 0.75, 0.05))
	_box(self, Vector3(4.2, 0.04, 2.4), Vector3(0, -0.02, 0.2), Color(0.16, 0.17, 0.15))

func _build_machine() -> void:
	for x in [CHUTE_X - 0.30, CHUTE_X + 0.30]:
		_box(self, Vector3(0.04, 1.55, 0.04), Vector3(x, 0.78, -0.08), STEEL)
	_box(self, Vector3(0.68, 0.04, 0.04), Vector3(CHUTE_X, 1.52, -0.08), STEEL)
	_box(self, Vector3(0.42, 0.02, 0.28), Vector3(CHUTE_X, 1.38, -0.08), Color(0.45, 0.48, 0.50, 0.55), true)
	_box(self, Vector3(0.02, 0.42, 0.28), Vector3(CHUTE_X - 0.20, 1.16, -0.08), Color(0.55, 0.58, 0.60, 0.4), true)
	_box(self, Vector3(0.02, 0.42, 0.28), Vector3(CHUTE_X + 0.20, 1.16, -0.08), Color(0.55, 0.58, 0.60, 0.4), true)
	_box(self, Vector3(0.03, 0.03, 0.03), Vector3(CHUTE_X - 0.18, 1.22, -0.08), Color(0.85, 0.2, 0.18))
	_box(self, Vector3(0.03, 0.03, 0.03), Vector3(CHUTE_X + 0.18, 1.22, -0.08), Color(0.85, 0.2, 0.18))
	_box(self, Vector3(0.30, 0.008, 0.008), Vector3(CHUTE_X, 1.22, -0.08), Color(0.9, 0.25, 0.2, 0.8), true)

	flap_pivot = Node3D.new()
	flap_pivot.name = "FlapPivot"
	flap_pivot.position = Vector3(CHUTE_X, 0.98, -0.02)
	add_child(flap_pivot)
	_box(flap_pivot, Vector3(0.05, 0.05, 0.08), Vector3(0, 0, 0), BRASS)
	_box(flap_pivot, Vector3(0.36, 0.22, 0.015), Vector3(0, -0.12, 0), Color(0.82, 0.80, 0.74))

	# BMW Rules 2016 Schedule I colours. Uno only drops into yellow and red.
	_open_bin(Vector3(-0.72, 0, 0.32), YELLOW, "Yellow", "Soiled waste")
	_open_bin(Vector3(-0.16, 0, 0.32), RED, "Red", "Contaminated plastic")
	_open_bin(Vector3(0.42, 0, 0.32), WHITE, "White", "Sharps · by hand")
	_open_bin(Vector3(0.98, 0, 0.32), BLUE, "Blue", "Glassware · by hand")
	_box(self, Vector3(1.15, 0.02, 0.10), Vector3(0.55, 0.64, 0.02), Color(0.28, 0.30, 0.28))

	piece = Node3D.new()
	piece.name = "ActiveItem"
	piece.visible = false
	piece.position = Vector3(CHUTE_X, 1.55, -0.08)
	add_child(piece)

func _build_chute_camera() -> void:
	# Twin chute camera: what the model sees.
	chute_vp = SubViewport.new()
	chute_vp.name = "ChuteCamViewport"
	chute_vp.size = Vector2i(320, 240)
	chute_vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	chute_vp.transparent_bg = false
	add_child(chute_vp)

	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-35, 20, 0)
	light.light_energy = 1.3
	chute_vp.add_child(light)

	chute_cam = Camera3D.new()
	chute_cam.current = true
	chute_cam.fov = 42.0
	chute_cam.position = Vector3(CHUTE_X, 1.24, 0.22)
	chute_vp.add_child(chute_cam)
	chute_cam.look_at(Vector3(CHUTE_X, 1.24, -0.08))

func _build_ui() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	var root := MarginContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.add_theme_constant_override("margin_left", 16)
	root.add_theme_constant_override("margin_top", 16)
	root.add_theme_constant_override("margin_right", 16)
	layer.add_child(root)

	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 16)
	root.add_child(row)

	var col := VBoxContainer.new()
	col.custom_minimum_size = Vector2(360, 0)
	col.add_theme_constant_override("separation", 8)
	row.add_child(col)

	var title := Label.new()
	title.text = "BioSeg twin"
	title.add_theme_font_size_override("font_size", 28)
	col.add_child(title)

	status_label = Label.new()
	status_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	status_label.custom_minimum_size = Vector2(340, 84)
	col.add_child(status_label)

	count_label = Label.new()
	col.add_child(count_label)
	_paint_counts()

	var closed := Label.new()
	closed.text = "Closed loop — all trained classes"
	col.add_child(closed)
	_button(col, "Gauze → yellow", func() -> void: _place_and_classify("gauze"))
	_button(col, "Glove → red", func() -> void: _place_and_classify("glove"))
	_button(col, "Syringe → red", func() -> void: _place_and_classify("syringe"))
	_button(col, "Urine bag → red", func() -> void: _place_and_classify("urine_bag"))
	_button(col, "Test tube → red", func() -> void: _place_and_classify("test_tube"))
	_button(col, "Glasses → hold", func() -> void: _place_and_classify("glasses"))
	_button(col, "Auto script on/off (T)", func() -> void:
		_auto = not _auto
		_script_t = 0.2
		_set_status("Auto script %s" % ("ON" if _auto else "OFF"))
	)

	var mech := Label.new()
	mech.text = "Mechanism only (no model)"
	col.add_child(mech)
	_button(col, "Servo left, yellow", func() -> void: _play("yellow"))
	_button(col, "Servo right, red", func() -> void: _play("red"))
	_button(col, "Gate shut", func() -> void: _play("shut"))
	_button(col, "Hold, then white", func() -> void: _play("white"))
	_button(col, "Hold, then blue", func() -> void: _play("blue"))
	_button(col, "Reset (R)", func() -> void: _reset())

	var note := Label.new()
	note.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	note.custom_minimum_size = Vector2(340, 110)
	note.text = "Keys 1–6 = all classes · T auto · R reset. Chute JPEG → ONNX. No laptop webcam. Arduino not moved. Live hardware props are cotton / mask / glove."
	col.add_child(note)

	var right := VBoxContainer.new()
	right.add_theme_constant_override("separation", 6)
	row.add_child(right)
	var cam_title := Label.new()
	cam_title.text = "Chute camera (what the model sees)"
	right.add_child(cam_title)
	preview = TextureRect.new()
	preview.custom_minimum_size = Vector2(320, 240)
	preview.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	preview.texture = chute_vp.get_texture()
	right.add_child(preview)

func _button(parent: Node, text: String, action: Callable) -> void:
	var button := Button.new()
	button.text = text
	button.alignment = HORIZONTAL_ALIGNMENT_LEFT
	button.pressed.connect(action)
	parent.add_child(button)

func _box(parent: Node3D, size: Vector3, pos: Vector3, color: Color, clear := false) -> MeshInstance3D:
	var mesh_instance := MeshInstance3D.new()
	var mesh := BoxMesh.new()
	mesh.size = size
	mesh_instance.mesh = mesh
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 0.42
	mat.metallic = 0.15
	if clear:
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mesh_instance.material_override = mat
	mesh_instance.position = pos
	parent.add_child(mesh_instance)
	return mesh_instance

func _open_bin(origin: Vector3, color: Color, title: String, subtitle: String) -> void:
	var root := Node3D.new()
	root.position = origin
	add_child(root)
	var w := 0.40
	var d := 0.36
	var h := 0.38
	var t := 0.018
	_box(root, Vector3(w, t, d), Vector3(0, t * 0.5, 0), color.darkened(0.25))
	_box(root, Vector3(w, h, t), Vector3(0, h * 0.5, d * 0.5), color)
	_box(root, Vector3(w, h, t), Vector3(0, h * 0.5, -d * 0.5), color)
	_box(root, Vector3(t, h, d), Vector3(-w * 0.5, h * 0.5, 0), color)
	_box(root, Vector3(t, h, d), Vector3(w * 0.5, h * 0.5, 0), color)
	var label := Label3D.new()
	label.text = title
	label.font_size = 42
	label.position = Vector3(0, h + 0.12, 0)
	label.modulate = color
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.outline_size = 6
	root.add_child(label)
	var sub := Label3D.new()
	sub.text = subtitle
	sub.font_size = 22
	sub.position = Vector3(0, h + 0.02, 0)
	sub.modulate = Color(0.85, 0.84, 0.78)
	sub.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	root.add_child(sub)

func _set_status(text: String) -> void:
	if status_label:
		status_label.text = text

func _paint_counts() -> void:
	if count_label:
		count_label.text = "Yellow %d   Red %d   White %d   Blue %d   Held %d" % [
			_counts.yellow, _counts.red, _counts.white, _counts.blue, _counts.held
		]

func _kill_tween() -> void:
	if _tween != null and _tween.is_valid():
		_tween.kill()

func _clear_piece() -> void:
	for child in piece.get_children():
		child.queue_free()

func _spawn_kind(kind: String) -> void:
	_clear_piece()
	var unit := WasteItems.make(kind)
	piece.add_child(unit)
	piece.visible = true
	piece.position = Vector3(CHUTE_X, 1.55, -0.08)
	piece.rotation_degrees = Vector3(0, 0, 0)  # photo on +Z faces the chute camera
	flap_pivot.rotation_degrees.z = 0.0

func _place_and_classify(kind: String) -> void:
	if _busy:
		_set_status("Wait for the last closed-loop drop.")
		return
	_busy = true
	_await_route = true
	_spawn_kind(kind)
	_kill_tween()
	_tween = create_tween()
	_tween.tween_property(piece, "position", Vector3(CHUTE_X, 1.24, -0.08), 0.35)
	_tween.tween_callback(func() -> void: _capture_chute_to_model(kind))
	_set_status("Placed %s in the chute. Capturing the twin camera for ONNX…" % kind)

func _capture_chute_to_model(kind: String) -> void:
	# One frame after settle so the sample is in view.
	await get_tree().process_frame
	await get_tree().process_frame
	var tex: ViewportTexture = chute_vp.get_texture()
	var img: Image = tex.get_image()
	if img == null:
		_busy = false
		_set_status("Chute camera returned no image.")
		return
	if img.get_format() != Image.FORMAT_RGB8 and img.get_format() != Image.FORMAT_RGBA8:
		img.convert(Image.FORMAT_RGB8)
	var jpg: PackedByteArray = img.save_jpg_to_buffer(0.9)
	if jpg.is_empty():
		_busy = false
		_set_status("Could not encode the chute camera JPEG.")
		return
	var body := JSON.stringify({
		"jpeg_b64": Marshalls.raw_to_base64(jpg),
		"kind": kind,
	})
	_set_status("Sending chute JPEG (%d bytes, %s) to %s/demo/frame" % [jpg.size(), kind, _server])
	var err := http.request(
		_server + "/demo/frame",
		PackedStringArray(["Content-Type: application/json"]),
		HTTPClient.METHOD_POST,
		body
	)
	if err != OK:
		_busy = false
		_set_status("Could not reach the BioSeg server. Start uvicorn on port 8000 first.")

func _on_http_done(_result: int, code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	_busy = false
	if code != 200:
		_await_route = false
		_set_status("Model failed (HTTP %d). Is uvicorn on :8000 with the ONNX file?" % code)
		return
	var parsed = JSON.parse_string(body.get_string_from_utf8())
	if typeof(parsed) != TYPE_DICTIONARY:
		return
	var classified: Dictionary = parsed.get("classified", {})
	_set_status(
		"Model said %s (%.0f%%) → %s. Waiting for WebSocket to move the bins." % [
			str(classified.get("item_class", "?")),
			float(classified.get("confidence", 0.0)) * 100.0,
			str(classified.get("route", "?"))
		]
	)

func _on_classified(_item_id: String, item_class: String, route: String, flap: String) -> void:
	_set_status("Classified %s → %s (flap %s)" % [item_class, route, flap])

func _play(route: String) -> void:
	_kill_tween()
	piece.visible = true
	if piece.position.y < 1.0 and piece.get_child_count() == 0:
		_spawn_kind("gauze")
	if piece.position.y < 1.0:
		piece.position = Vector3(CHUTE_X, 1.24, -0.08)
	_tween = create_tween()
	match route:
		"yellow":
			_counts.yellow += 1
			_tween.tween_property(flap_pivot, "rotation_degrees:z", -42.0, 0.22)
			_tween.tween_property(piece, "position", Vector3(-0.72, 0.24, 0.32), 0.55)
			_set_status("Servo left. Uno LEFT. Sample drops into yellow.")
		"red":
			_counts.red += 1
			_tween.tween_property(flap_pivot, "rotation_degrees:z", 42.0, 0.22)
			_tween.tween_property(piece, "position", Vector3(-0.16, 0.24, 0.32), 0.55)
			_set_status("Servo right. Uno RIGHT. Sample drops into red.")
		"white":
			_counts.held += 1
			_counts.white += 1
			_tween.tween_property(flap_pivot, "rotation_degrees:z", 0.0, 0.18)
			_tween.tween_property(piece, "position", Vector3(0.05, 0.66, 0.02), 0.35)
			_tween.tween_property(piece, "position", Vector3(0.42, 0.24, 0.32), 0.45)
			_set_status("Gate shut. Person carries the sample to white. No Uno command.")
		"blue":
			_counts.held += 1
			_counts.blue += 1
			_tween.tween_property(flap_pivot, "rotation_degrees:z", 0.0, 0.18)
			_tween.tween_property(piece, "position", Vector3(0.20, 0.66, 0.02), 0.35)
			_tween.tween_property(piece, "position", Vector3(0.98, 0.24, 0.32), 0.5)
			_set_status("Gate shut. Person carries the sample to blue. No Uno command.")
		_:
			_counts.held += 1
			_tween.tween_property(flap_pivot, "rotation_degrees:z", 0.0, 0.18)
			_tween.tween_property(piece, "position", Vector3(CHUTE_X, 1.12, -0.08), 0.25)
			_set_status("Gate shut. Uno HOLD. Sample stays in the chute.")
	_tween.tween_callback(_paint_counts)
	_await_route = false

func _reset() -> void:
	_kill_tween()
	_clear_piece()
	piece.visible = false
	piece.position = Vector3(CHUTE_X, 1.55, -0.08)
	flap_pivot.rotation_degrees.z = 0.0
	_counts = {"yellow": 0, "red": 0, "white": 0, "blue": 0, "held": 0}
	_busy = false
	_await_route = false
	_paint_counts()
	_set_status("Reset. Gate shut.")

func _on_routed(_item_id: String, flap: String) -> void:
	if flap == "left":
		_play("yellow")
	elif flap == "right":
		_play("red")
	else:
		_play("shut")

func _on_held(_item_id: String, _reason: String) -> void:
	_play("shut")
