class_name Enemy
extends "res://src/base_actor.gd"

signal died


static func spawn_count() -> int:
	return 0


func die() -> void:
	Tools.helper()
	died.emit()
