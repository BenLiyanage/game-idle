extends Control

const SNAPSHOT_DIR := "user://snapshots"

func _ready() -> void:
	print("[game-idle] startup: main scene ready")
	print("[game-idle] snapshot: press F12 to save a PNG to user://snapshots")

func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_F12:
			_take_snapshot()

func _take_snapshot() -> void:
	var dir := DirAccess.open("user://")
	if dir == null:
		push_error("[game-idle] snapshot: failed to open user://")
		return
	# Create snapshots directory if needed
	dir.make_dir_recursive("snapshots")

	var ts := Time.get_datetime_string_from_system().replace(":", "-")
	var path := "%s/%s.png" % [SNAPSHOT_DIR, ts]

	var img := get_viewport().get_texture().get_image()
	var err := img.save_png(path)
	if err != OK:
		push_error("[game-idle] snapshot: save failed (%s) to %s" % [str(err), path])
		return

	print("[game-idle] snapshot saved: %s" % path)
