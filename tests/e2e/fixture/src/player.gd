class_name Player
extends Node2D

var big: Big
var health: int = 3


func spawn():
	Bus.announce("spawn")
	big.step_03()


func tick():
	big.step_04()
	big.step_05()


func take_hit():
	health -= 1
	Bus.announce("hit")
