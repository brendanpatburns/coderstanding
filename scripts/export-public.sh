#!/usr/bin/env bash
# Export a public copy of this repo as ONE new commit in a separate local repo. Never pushes.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/export-public.sh [--commit REV] [--out DIR] [--check-only]

Builds the public tree from the files tracked at REV (default: main), leaves out the paths in
public-exclude.txt, drops private:start/private:end blocks and unwraps public-line: lines
(scripts/public_check.py prepare), then runs the checker and REFUSES on any hit: generic
patterns (emails, private IPs, keys, tokens, home paths, binary/large files) plus your own
strings in ~/.config/coderstanding/public-denylist.txt (outside the repo; never commit it).

When clean, it replaces the files of the public repo DIR (default ~/AI/coderstanding-public,
made with `git init` if missing) and commits them as one commit "Release <date> from <sha>",
authored by PUBLIC_NAME <PUBLIC_EMAIL> (set at the top of the script), in UTC, unsigned,
no hooks, no trailers. The private history never goes there.

  --commit REV   what to export (a branch, tag or sha)
  --out DIR      the local public repo
  --check-only   build and check only; commit nothing

Publishing is a separate, explicit step (only when the user asks), e.g.:
  git -C ~/AI/coderstanding-public remote add origin https://github.com/brendanpatburns/coderstanding.git   # once
  git -C ~/AI/coderstanding-public push origin main
EOF
}

PUBLIC_NAME="brendanpatburns"
PUBLIC_EMAIL="350258484+brendanpatburns@users.noreply.github.com"  # GitHub numbered no-reply address
commit="main"
out="$HOME/AI/coderstanding-public"
check_only=0
while (($#)); do
    case "$1" in
        --commit) commit="$2"; shift 2 ;;
        --out) out="$2"; shift 2 ;;
        --check-only) check_only=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
checker="$repo/scripts/public_check.py"
denylist="$HOME/.config/coderstanding/public-denylist.txt"
sha="$(git -C "$repo" rev-parse --verify "$commit^{commit}")"
short="$(git -C "$repo" rev-parse --short "$sha")"

case "$(readlink -f "$denylist")" in
    "$repo"/*) echo "export: the denylist must live outside the repo: $denylist" >&2; exit 1 ;;
esac

work="$(mktemp -d "${TMPDIR:-/tmp}/coderstanding-export-XXXXXX")"
trap 'rm -rf "$work"' EXIT
tree="$work/tree"
mkdir -p "$tree"
git -C "$repo" archive --format=tar "$sha" | tar -x -C "$tree"   # = git ls-files at $sha

python3 "$checker" prepare "$tree"
if ! python3 "$checker" check "$tree" --denylist "$denylist"; then
    echo "export: REFUSED ($short): fix the hits above in the private repo (or exclude them), commit, retry" >&2
    exit 1
fi
echo "export: $(find "$tree" -type f | wc -l) files clean at $short"
((check_only)) && exit 0

if [[ ! -d "$out/.git" ]]; then
    mkdir -p "$out"
    git -C "$out" init -q -b main
fi
git -C "$out" config user.name "$PUBLIC_NAME"
git -C "$out" config user.email "$PUBLIC_EMAIL"
find "$out" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a "$tree/." "$out/"
git -C "$out" add -A
if git -C "$out" diff --cached --quiet; then
    echo "export: nothing changed since the last release in $out"
    exit 0
fi
export TZ=UTC GIT_AUTHOR_NAME="$PUBLIC_NAME" GIT_AUTHOR_EMAIL="$PUBLIC_EMAIL" \
       GIT_COMMITTER_NAME="$PUBLIC_NAME" GIT_COMMITTER_EMAIL="$PUBLIC_EMAIL"
git -C "$out" -c core.hooksPath=/dev/null -c commit.gpgsign=false -c commit.template= \
    commit -q --no-verify -m "Release $(date -u +%F) from $short"
git -C "$out" log -1 --format='export: committed %h "%s" by %an <%ae> (committer %cn <%ce>, %ad)'
echo "export: not pushed. Publishing is a separate step: see scripts/export-public.sh --help"
