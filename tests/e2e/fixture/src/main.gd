## Starts the game: spawns the player and wires the bus.
extends Node

var player: Player
var big: Big


func _ready():
	player = Player.new()
	big = Big.new()
	Bus.announce("start")
	player.spawn()
	big.step_01()


func _process(_delta):
	player.tick()
	big.step_02()
