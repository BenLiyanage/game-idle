extends SceneTree

const MAIN_SCENE_PATH := "res://scenes/main.tscn"

func _initialize() -> void:
	var exit_code := _run()
	quit(exit_code)

func _run() -> int:
	var scene := load(MAIN_SCENE_PATH)
	if scene == null:
		return _fail("failed to load %s" % MAIN_SCENE_PATH)
	if not (scene is PackedScene):
		return _fail("%s did not load as PackedScene" % MAIN_SCENE_PATH)

	var scene_root := (scene as PackedScene).instantiate()
	if scene_root == null:
		return _fail("failed to instantiate %s" % MAIN_SCENE_PATH)

	var center_container := scene_root.get_node_or_null("CenterContainer")
	if center_container == null:
		scene_root.free()
		return _fail("missing CenterContainer")

	var label := center_container.get_node_or_null("Label")
	if label == null:
		scene_root.free()
		return _fail("missing CenterContainer/Label")
	if not label is Label:
		scene_root.free()
		return _fail("CenterContainer/Label is not a Label")
	if (label as Label).text != "Hello Idle":
		var actual_text := (label as Label).text
		scene_root.free()
		return _fail("expected Label text 'Hello Idle', got '%s'" % actual_text)

	scene_root.free()
	print("scene smoke test ok")
	return 0

func _fail(message: String) -> int:
	push_error(message)
	return 1
