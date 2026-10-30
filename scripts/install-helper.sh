#!/usr/bin/env bash
# Install the helper and tray as systemd user units and enable them (they start at login).
# Run once by hand; it does not start anything now (use: systemctl --user start coderstanding-helper).
set -euo pipefail
repo="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
dest="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$dest"
for unit in coderstanding-helper.service coderstanding-tray.service; do
    # Units hold the repo path (quoted in ExecStart), so render them instead of linking.
    sed "s|@REPO@|$repo|g" "$repo/deploy/systemd/$unit" > "$dest/$unit"
    echo "installed $dest/$unit"
done
systemctl --user daemon-reload
systemctl --user enable coderstanding-helper.service coderstanding-tray.service
echo "Enabled. Start now with: systemctl --user start coderstanding-helper coderstanding-tray"
