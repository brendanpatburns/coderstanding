class_name ParticlePool
extends Node

var free: Array[Node] = []


func take():
	return free.pop_back()


func give_back():
	pass
