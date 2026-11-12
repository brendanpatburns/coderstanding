---
id: start-cli
title: "Start here: cli"
by: claude
ai: true
start: true
steps:
  - key: 8ad04c
    targets: [cli]
    code: {at: cli, lines: +0..+7, fp: e6d6e5268f, was: 1-8}
  - key: a034e7
    targets: [views]
    code: {at: views, lines: +0..+7, fp: d159188406, was: 1-8}
  - key: 9fdf51
    targets: [checks]
    code: {at: checks, lines: +0..+7, fp: 84e01a9e64, was: 1-8}
  - key: 8bbeb7
    targets: [scan]
    code: {at: scan, lines: +0..+7, fp: d3027e520f, was: 1-8}
  - key: 56cd0f
    targets: [offline]
    code: {at: offline, lines: +0..+7, fp: d3ab81d057, was: 1-8}
---

## cli
[[cli]]: The coderstanding command line: one command per thing you can ask The coderstanding command: one command for each thing you can do or ask (scan, graph, zoom, node, set, specs, tours and many more). Most commands only read the saved map and never scan by themselves. It is the main entry point for both people and AI agents.

## views
[[views]]: Decides what to show at each level, zoom and filter, for both CLI and viewer The one place that decides what is on screen: which classes show at a level, how hidden classes fold into visible ones, zoom, focus, filters and badges. The command line and the viewer both ask it, so they always agree. Also answers the reading questions: outline, find, impact and brief.

## checks
[[checks]]: Spots possible problems: unused classes, oversized classes, loops The warnings on the map: unused classes, classes that are too big, loops between classes, unsure matches, Godot signals nobody listens to, and the project's own architecture rules. Users can ignore warnings per class or per folder; this is the one place that decides what shows.

## scan
[[scan]]: Reads a whole repo and turns it into the map; every scan goes through here The start of everything: it reads a repo and produces the map that every other part shows. It finds the files, hands each to the reader for its language, matches names to classes, guesses levels and runs the checks. Scans never happen on their own; you or the viewer ask for one, and unchanged files are taken from the cache so rescans are quick.

## offline
[[offline]]: Makes sure the tool never talks to another machine The privacy guarantee: it blocks every attempt to reach another machine, so no code ever leaves this PC. It is switched on at every way the tool can start.
