#!/usr/bin/env bash
# Build the wheel + sdist into dist/, then install the wheel into a fresh throwaway venv and
# smoke-test it (the way a user without the source would run it). Publishes nothing.
# Needs PyPI at build time only (build tools, tree-sitter grammar wheels); no company code is involved.
#   PYTHON=python3.12 scripts/build-release.sh
set -euo pipefail
repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
python="${PYTHON:-python3}"
work="$(mktemp -d "${TMPDIR:-/tmp}/coderstanding-release.XXXXXX")"
server_pid=""
cleanup() {
    [[ -n "$server_pid" ]] && kill "$server_pid" 2>/dev/null || true
    rm -rf "$work"
}
trap cleanup EXIT

cd "$repo"
rm -rf dist build scanner/*.egg-info
"$python" -m venv "$work/build-env"
"$work/build-env/bin/pip" install -q build
"$work/build-env/bin/python" -m build --outdir dist .
rm -rf build scanner/*.egg-info
wheel="$(ls dist/coderstanding-*.whl)"
echo "built: $wheel and $(ls dist/coderstanding-*.tar.gz)"

echo "--- smoke test in a fresh venv"
"$python" -m venv "$work/env"
"$work/env/bin/pip" install -q "$wheel"
cs="$work/env/bin/coderstanding"
# The installed copy must not see the checkout, and must not touch the real config or data.
export HOME="$work/home" CODERSTANDING_REGISTRY="$work/apps.json" CODERSTANDING_DATA_HOME="$work/data"
mkdir -p "$HOME"
cd "$work"

"$cs" --help >/dev/null
"$cs" --version
"$work/env/bin/python" - <<'PY'
import coderstanding, zipfile, glob
from coderstanding import assets
assert "site-packages" in coderstanding.__file__, coderstanding.__file__
assert assets.is_installed(), "viewer is not inside the package"
for name in ("index.html", "vendor/cytoscape.min.js", "vendor/cytoscape.LICENSE"):
    assert (assets.viewer_dir() / name).is_file(), name
assert (assets.docs_dir() / "FOR_CLAUDE.md").is_file() and (assets.docs_dir() / "skill" / "SKILL.md").is_file()
PY

fixture="$work/fixture"
mkdir -p "$fixture"
printf 'class Engine:\n    def run(self):\n        return Wheel()\n\n\nclass Wheel:\n    pass\n' > "$fixture/car.py"
"$cs" add smoke "$fixture"
"$cs" find smoke Engine | tee "$work/graph.txt"
grep -q "Engine" "$work/graph.txt"

port="$("$work/env/bin/python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
"$cs" serve smoke --port "$port" >"$work/serve.log" 2>&1 &
server_pid=$!
"$work/env/bin/python" - "$port" <<'PY'
import json, sys, time, urllib.request
base = f"http://127.0.0.1:{sys.argv[1]}"
for _ in range(50):
    try:
        page = urllib.request.urlopen(base + "/", timeout=2).read().decode()
        break
    except OSError:
        time.sleep(0.2)
else:
    sys.exit("server did not start")
assert "<title>" in page, "viewer page not served"
apps = json.load(urllib.request.urlopen(base + "/api/apps", timeout=5))
assert [a["name"] for a in apps["apps"]] == ["smoke"], apps
js = urllib.request.urlopen(base + "/vendor/cytoscape.min.js", timeout=5)
assert js.headers["Content-Type"].startswith("text/javascript"), js.headers["Content-Type"]
print("serve ok:", base)
PY
kill "$server_pid"; server_pid=""

"$cs" helper install --dry-run --no-tray
"$cs" hook >/dev/null
"$cs" skill install --dest "$work/skill" --yes
test -f "$work/skill/SKILL.md" -a -f "$work/skill/FOR_CLAUDE.md"
echo "--- release smoke test passed: $repo/$wheel"
