class_name Player
extends Node2D

var vfx: VFX
var health: int = 3


func _physics_process(_delta):
	play_step()


func take_hit():
	vfx.push_effect()
	vfx.push_effect()
	AudioBus.play_sfx("hit")


func play_step():
	AudioBus.play_sfx("step_a")
	AudioBus.play_sfx("step_b")
	AudioBus.play_sfx("step_c")


func die():
	vfx.clear_effects()
