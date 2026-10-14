extends SceneTree
# Renders characters beside a 1.7 m reference box to calibrate scale. Usage: godot --script tools/preview.gd
var frames := 0
func _init() -> void:
	var cam := Camera3D.new()
	cam.position = Vector3(0, 1.2, 5)
	root.add_child(cam)
	cam.look_at(Vector3(0, 0.9, 0))
	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-50, 30, 0)
	root.add_child(light)
	var env := WorldEnvironment.new()
	env.environment = Environment.new()
	env.environment.background_mode = Environment.BG_COLOR
	env.environment.background_color = Color(0.8, 0.85, 0.9)
	env.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.environment.ambient_light_color = Color(0.6, 0.6, 0.6)
	root.add_child(env)
	var box := MeshInstance3D.new()
	box.mesh = BoxMesh.new()
	box.mesh.size = Vector3(0.4, 1.7, 0.4)
	box.position = Vector3(-0.6, 0.85, 0)
	root.add_child(box)
	var x := 0.0
	for s in [0.35, 0.37]:
		var c: Node3D = (load("res://assets/characters/Smooth_Female_Casual.fbx") as PackedScene).instantiate()
		c.scale = Vector3.ONE * s
		c.position = Vector3(x, 0, 0)
		root.add_child(c)
		var ap: AnimationPlayer = c.find_child("AnimationPlayer", true, false)
		ap.play("HumanArmature|Female_Idle")
		x += 0.8
func _process(_d: float) -> bool:
	frames += 1
	if frames == 30:
		root.get_viewport().get_texture().get_image().save_png("/tmp/claude-1000/-home-mudassir-Projects-sih26-qualcomm-retail/325a3dae-1f15-4c33-9128-2b303d9d31a2/scratchpad/preview.png")
		return true
	return false
