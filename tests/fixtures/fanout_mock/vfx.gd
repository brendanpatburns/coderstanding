class_name VFX
extends Node2D

signal effect_played

var pool: ParticlePool


func push_effect():
	spawn_sparks()
	effect_played.connect(AudioBus._on_effect_played)
	effect_played.emit()


func spawn_sparks():
	pool.take()


func clear_effects():
	pass


func _on_effect_done():
	pool.give_back()
