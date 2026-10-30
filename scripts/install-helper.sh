#!/usr/bin/env bash
# Thin wrapper kept for checkouts: the real work is `coderstanding helper install` (any platform).
# Run once by hand; it does not start anything now (use: coderstanding helper start).
set -euo pipefail
exec "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../bin/coderstanding" helper install "$@"
