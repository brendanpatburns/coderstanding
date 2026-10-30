## Global event bus.
extends Node

signal said(text)


func announce(text):
	said.emit(text)


func clear():
	pass
