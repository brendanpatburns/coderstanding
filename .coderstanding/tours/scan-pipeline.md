---
id: scan-pipeline
title: How a scan works
by: claude
ai: true
steps:
  - key: start
    targets: [cli#run_scan, cli>scan]
    code: {at: cli#run_scan, lines: +0..+8, mark: [+6, +7], fp: d922a32a66, was: 28-36}
    notes: [{lines: +6..+6, text: "Builds the whole graph: the next steps walk through this one call.", cue: builds the graph}, {lines: +7..+7, text: "Saves it as graph.json, the file the viewer and the CLI read.", cue: saves it}]
  - key: pipeline
    targets: [scan#scan_repo]
    code: {at: scan#scan_repo, lines: +18..+26, mark: [+18, +19, +20, +21, +22, +23, +24, +25, +26], fp: 3ba1f261ad, was: 102-110}
    notes: [{lines: +18..+18, text: Unchanged files come from the scan cache instead of being parsed again., cue: unchanged files}]
  - key: files
    targets: [scan>filewalk, filewalk#assign_files]
    code: {at: filewalk#assign_files, lines: +0..+4, mark: [+2, +3, +4], fp: d5e212c36d, was: 121-125}
  - key: reader
    targets: [scan>language, PythonModule#parse]
    code: {at: PythonModule, lines: +0..+5, mark: [+1, +2, +4, +5], fp: 50b676058e, was: 343-348}
  - key: resolve
    targets: [scan>resolver, resolver#build_graph, resolver>model]
    code: {at: resolver#build_graph, lines: +0..+15, mark: [+5, +6, +11, +12], fp: 96ccc38b22, was: 238-253}
    notes: [{lines: +5..+6, text: "Pass one: every declaration becomes a node.", cue: First every declaration}, {lines: +11..+13, text: "Pass two: each reference becomes an edge, or an outside reference.", cue: Then every raw}]
  - key: levels
    targets: [scan>levels, levels#guess_levels]
    code: {at: levels#guess_levels, lines: +14..+28, mark: [+14, +15, +16, +17, +18, +19, +23, +24, +25, +26, +28], fp: a2caf2b82e, was: 107-121}
  - key: fingerprints
    targets: [scan>fingerprints, fingerprints#compute_fingerprints]
    code: {at: fingerprints#compute_fingerprints, lines: +0..+16, mark: [+7, +10, +15], fp: 9b2e84240c, was: 74-90}
  - key: save
    targets: [cli>graph_json, graph_json#write_graph]
    code: {at: graph_json#write_graph, lines: +0..+4, mark: [+3, +4], fp: 8898f0b499, was: 111-115}
---

## Where a scan starts
Everything begins with the scan command. [[cli#run_scan]] checks the folder and picks where graph.json goes; with `-o` the cache goes next to it, so the scanned repo stays untouched. The two marked lines are the whole job: [[scan#scan_repo]] builds the graph and [[graph_json#write_graph]] saves it.

## The pipeline in ten lines
[[scan#scan_repo]] is the pipeline: parse every file except vendored, minified and excluded ones (unchanged files come from the scan cache), read import path aliases (tsconfig), build the graph, read the tech stack, link HTTP clients to server routes and network-message senders to their handlers, note why imports found no file and which files had parse errors, mark tests, index the names on every line of code and non-code files (for `uses`, `env`, `dupes`), guess levels, run the checks and take code fingerprints. The next steps follow these calls in order.

## Which files, read by whom
First the file walk. [[filewalk#assign_files]] lists the repo's files, from git when it can (only files git doesn't ignore, plus any ignored folders the app's `include` setting names), else by walking the folders, and pairs each one with every language module whose patterns match. One file can be read by more than one module.

## A language module reads one file
Each pair goes to its language module. The Python reader, [[PythonModule]], only gives its name and file pattern and parses one file into a FileResult. It never resolves names: that is the shared core's job.

## Declarations become nodes, refs become edges
[[resolver#build_graph]] makes the graph in two passes over the parsed files. First every declaration becomes a node. Then every raw reference is resolved into an edge, or into an outside reference when nothing in the repo matches.

## A first guess at levels
[[levels#guess_levels]] gives each node a rough level. The top nodes (app starts, else tools, else holders nothing holds) and hubs are level one; when the app has a start, tools sit a fixed step lower. Everything else is one more than the node that holds it, found breadth first. Supporting files (configuration, data, build files, docs) start one level lower again, so the top picture is mostly code. Curation fixes the rest later.

## Fingerprints of the code
Last, [[fingerprints#compute_fingerprints]] hashes the code of every class, function and connection, with all whitespace removed. Notes and tour steps keep the hash from when they were written, so a later scan can tell which ones describe code that has changed since. This tour's "code changed" marks come from here.

## Saved as graph.json
Back in the command, [[graph_json#write_graph]] saves the graph as one indented JSON file. The viewer and the slim text CLI both read it from there.
