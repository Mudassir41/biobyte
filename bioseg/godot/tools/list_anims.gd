extends SceneTree
# godot --headless --script tools/list_anims.gd : prints animations found in each character model
func _init() -> void:
	var dir := DirAccess.open("res://assets/characters")
	for f in dir.get_files():
		if not f.ends_with(".fbx"):
			continue
		var scene: PackedScene = load("res://assets/characters/" + f)
		var inst := scene.instantiate()
		var ap: AnimationPlayer = inst.find_child("AnimationPlayer", true, false)
		var aabb := AABB()
		for m in inst.find_children("*", "MeshInstance3D", true, false):
			aabb = aabb.merge((m as MeshInstance3D).get_aabb())
		print(f, " anims=", ap.get_animation_list() if ap else [], " size=", aabb.size)
		inst.free()
	quit()
