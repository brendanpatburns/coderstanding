---
id: start-viewer
title: "Start here: the viewer page"
start: true
by: claude
ai: true
steps:
  - key: 042bf4
    targets: [app.js]
    code: {at: app.js, lines: +0..+7, fp: 69c689fed7, was: 1-8}
  - key: f6da68
    targets: [server]
    code: {at: server, lines: +0..+7, fp: 8a5f998926, was: 1-8}
  - key: 35a860
    targets: [views]
    code: {at: views, lines: +0..+7, fp: d159188406, was: 1-8}
  - key: d061b0
    targets: [notes]
    code: {at: notes, lines: +0..+7, fp: 218013e9c6, was: 1-8}
---

## app.js
[[app.js]]: The viewer page: draws the map the helper sends and handles clicks The heart of the viewer web page. It asks the local helper for a view and draws it; it holds no map logic itself. It wires up the toolbar, the depth ruler, the details panel and the graph, and the other viewer scripts plug into it.

## server
[[server]]: The local helper: serves the viewer and answers its questions The local helper: a small web server on this PC only, which serves the viewer and answers its questions about every registered project. The hosted web page also talks to it. It reads saved maps and never scans by itself.

## views
[[views]]: Decides what to show at each level, zoom and filter, for both CLI and viewer The one place that decides what is on screen: which classes show at a level, how hidden classes fold into visible ones, zoom, focus, filters and badges. The command line and the viewer both ask it, so they always agree. Also answers the reading questions: outline, find, impact and brief.

## notes
[[notes]]: Keeps the hints, descriptions and other notes people and AI add to the map Stores everything people and AI write about the map: hints, descriptions, levels, domains, status, rules and review notes, each with who wrote it. A person's value always beats an AI draft. Notes also remember the code they describe, so they can be flagged when the code changes.
