"""The scan pipeline: file walk -> language modules -> resolver -> levels -> Graph."""

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from itertools import repeat
from pathlib import Path

from .cache import CACHE_DIR_ENV, ScanCache, default_cache_dir, file_hash, write_gitignore
from .checks import check_graph
from .filewalk import DEFAULT_SKIP_DIRS, assign_files
from .fingerprints import compute_fingerprints
from .language import FileContext, load_builtin_modules, track_dependencies
from .levels import AUTO, guess_levels
from .resolver import Parsed, build_graph
from .stack import read_stack
from .testcode import mark_tests


PARALLEL_MIN_FILES = 150  # fewer files to parse than this: stay serial (pool start-up costs)


def parse_one(root, rel_path, module):
    """("ok", FileResult, dependency paths) or ("error", message). Runs in pool workers too."""
    try:
        with track_dependencies() as deps:
            result = module.parse(FileContext(root, rel_path))
        return "ok", result, deps
    except Exception as error:  # one broken file must not stop the scan
        return "error", str(error), set()


def parse_all(root, jobs, workers, parallel_min):
    """parse_one for every (rel_path, module) job, in job order; pooled when there are many."""
    if workers > 1 and len(jobs) >= parallel_min:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            chunk = max(1, len(jobs) // (workers * 4))
            return list(pool.map(parse_one, repeat(root), *zip(*jobs), chunksize=chunk))
    return [parse_one(root, rel, module) for rel, module in jobs]


def parse_files(root, modules, skip_dirs, cache=None, workers=None,
                parallel_min=PARALLEL_MIN_FILES):
    workers = workers or os.cpu_count() or 1
    assigned = assign_files(root, modules, skip_dirs)
    results = {}  # (rel_path, module name) -> FileResult
    digests = {}
    todo = []
    for rel_path, module in assigned:
        digests[rel_path] = file_hash(root / rel_path)
        hit = cache and cache.load(rel_path, module.name, digests[rel_path])
        if hit:
            results[(rel_path, module.name)] = hit[0]
        else:
            todo.append((rel_path, module))
    outcomes = parse_all(root, todo, workers, parallel_min)
    for (rel_path, module), (status, result, deps) in zip(todo, outcomes):
        if status == "error":
            print(f"warning: {module.name} could not read {rel_path}: {result}",
                  file=sys.stderr)
            continue
        results[(rel_path, module.name)] = result
        if cache:
            cache.store(rel_path, module.name, digests[rel_path], result, deps)
    if cache:
        cache.prune()
    return [Parsed(rel, module, results[(rel, module.name)]) for rel, module in assigned
            if (rel, module.name) in results]


def scan_repo(root, modules=None, skip_dirs=DEFAULT_SKIP_DIRS, use_cache=True,
              cache_dir=None, workers=None, parallel_min=PARALLEL_MIN_FILES, hub_threshold=AUTO):
    """cache_dir: where the per-file cache lives (default: <root>/.coderstanding/cache).
    hub_threshold: see levels.parse_hub_threshold."""
    root = Path(root).resolve()
    if modules is None:
        modules = load_builtin_modules()
    for module in modules:
        module.start_scan()
    cache = None
    if use_cache:
        if not (cache_dir or os.environ.get(CACHE_DIR_ENV)):
            write_gitignore(root)
        cache = ScanCache(root, cache_dir or default_cache_dir(root))
    parsed = parse_files(root, modules, skip_dirs, cache, workers, parallel_min)
    graph = build_graph(root, parsed, repo=root.name)
    read_stack(root, graph, skip_dirs)
    mark_tests(graph)
    guess_levels(graph, hub_threshold=hub_threshold)
    check_graph(graph)
    graph.fingerprints = compute_fingerprints(root, graph)
    graph.scanned_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return graph


def default_output(root):
    return Path(root) / ".coderstanding" / "graph.json"
