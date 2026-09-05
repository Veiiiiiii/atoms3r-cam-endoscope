#!/usr/bin/env bash
# Pull the newest code from GitHub, then leave the Endoscope icon ready to test.
# This is what the "Update Endoscope" desktop icon runs.
set -uo pipefail

REPO_URL="${ENDOSCOPE_REPO:-https://github.com/Veiiiiiii/atoms3r-cam-endoscope.git}"
DEST="${ENDOSCOPE_DIR:-${HOME}/atoms3r-cam-endoscope}"

pause() {
    echo
    read -r -p "Press Enter to close this window... " _ || true
}

echo "=== Updating Endoscope ==="
echo "target: ${DEST}"
echo

# A running viewer holds /dev/video0. Stopping it first means the icon is
# immediately usable after the update instead of failing once and then working.
pkill -f "endoscope.py" 2>/dev/null && echo "stopped the running viewer" || true

if [[ ! -d "${DEST}/.git" ]]; then
    if [[ -d "${DEST}" ]]; then
        BACKUP="${DEST}.before-update.$(date +%Y%m%d-%H%M%S)"
        echo "${DEST} exists but is not a git clone; moving it to ${BACKUP}"
        mv "${DEST}" "${BACKUP}" || { echo "could not move it"; pause; exit 1; }
    fi
    echo "cloning ${REPO_URL}"
    git clone "${REPO_URL}" "${DEST}" || { echo "CLONE FAILED"; pause; exit 1; }
else
    cd "${DEST}"
    # Keep whatever was edited on the Pi rather than destroying it silently.
    if ! git diff --quiet || ! git diff --cached --quiet; then
        STAMP="local-$(date +%Y%m%d-%H%M%S)"
        echo "local edits found; saving them as stash '${STAMP}'"
        git stash push -u -m "${STAMP}" >/dev/null || true
        echo "recover them later with: git stash list / git stash pop"
    fi
    echo "fetching..."
    git fetch --prune origin || { echo "FETCH FAILED (network?)"; pause; exit 1; }
    BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo main)"
    git reset --hard "origin/${BRANCH}" || {
        echo "could not reset to origin/${BRANCH}"; pause; exit 1; }
fi

cd "${DEST}"
chmod +x ./*.sh 2>/dev/null || true

echo
echo "now at: $(git log -1 --format='%h  %ad  %s' --date=short 2>/dev/null || echo unknown)"

# Refresh the desktop icons too, so a renamed or moved script cannot leave a
# shortcut pointing at a file that no longer exists.
if [[ -x "${DEST}/install_desktop.sh" ]]; then
    echo
    "${DEST}/install_desktop.sh" || echo "(icon refresh failed; icons unchanged)"
fi

echo
echo "=== Update complete. Double-click Endoscope to test. ==="
pause
