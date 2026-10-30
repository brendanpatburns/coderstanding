class_name Player extends CharacterBody2D

const Weapon := preload("res://src/weapon.gd")
const Hud := preload("res://scenes/hud.tscn")
const SPEED := 200.0

@export var target: Enemy
var weapon := Weapon.new()
var spare: Weapon
var buff := Buff.new()
var cam: PhantomCam


class Buff extends RefCounted:
	var amount: int

	func boost(by: Weapon) -> void:
		by.reload()


func _ready() -> void:
	spare = Weapon.new()
	weapon.fire()
	spare.reload()
	var hud := Hud.instantiate()
	add_child(hud)
	var tmp := Weapon.new()
	tmp.reload()
	Game.add_score(1)
	Game.score_changed.connect(_on_score)
	target.died.connect(_on_died)
	Enemy.spawn_count()
	var fancy := preload("res://addons/fancy/fancy.gd").new()
	fancy.follow(self)


func hit(by: Enemy) -> Weapon:
	return weapon


func _on_score(_value: int) -> void:
	pass


func _on_died() -> void:
	pass
