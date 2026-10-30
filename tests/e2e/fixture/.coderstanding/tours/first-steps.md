---
id: first-steps
title: How the game starts
by: sam
steps:
  - key: s1
    targets: [res://src/main.gd#_ready, res://src/main.gd>Player]
    code: {at: res://src/main.gd#_ready, lines: +0..+5, mark: [+3, +4]}
  - key: s2
    targets: [Player#spawn]
    code: {at: Player#spawn, lines: +0..+2, mark: [+1, +2], fp: deadbeef00, was: 6-8}
    by: claude
    ai: true
  - key: s3
    targets: [Player#tick, Player>Big]
    code: {at: Player#tick, lines: +0..+2, mark: [+1, +2]}
---

## Where the game starts
Godot runs the main scene's script first. [[res://src/main.gd#_ready]] makes the player and the big helper, then announces the start on the bus.

## The player spawns
[[Player#spawn]] tells everyone on the bus that the player is here, then asks the big helper for its third step.

## Every frame
Each frame the player ticks, and [[Player>Big]] carries two more steps of work.
