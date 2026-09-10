#!/usr/bin/env bash
# Install/refresh the Endoscope and Update Endoscope desktop shortcuts.
# Safe to run repeatedly; the updater calls it on every pull.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null || echo "${HOME}/Desktop")"
APPS="${HOME}/.local/share/applications"
BACKUP="${HOME}/.local/share/endoscope-icon-backup"

mkdir -p "${DESKTOP}" "${APPS}" "${BACKUP}"
chmod +x "${SCRIPT_DIR}"/*.sh 2>/dev/null || true

# Retire any older shortcut, whatever it was named. Without this the desktop
# ends up with two Endoscope icons, one of them pointing at an old checkout.
shopt -s nullglob
for f in "${DESKTOP}"/*.desktop; do
    case "$(basename "${f}")" in
        Endoscope.desktop|"Update Endoscope.desktop") continue ;;
    esac
    if grep -qiE 'endoscope|run_usb\.sh' "${f}" 2>/dev/null; then
        echo "retiring old shortcut: $(basename "${f}")"
        mv -f "${f}" "${BACKUP}/" 2>/dev/null || true
    fi
done
shopt -u nullglob

write_entry() {
    local path="$1" name="$2" comment="$3" exec_line="$4" icon="$5" term="$6"
    cat > "${path}" <<ENTRY
[Desktop Entry]
Type=Application
Version=1.0
Name=${name}
Comment=${comment}
Exec=${exec_line}
Path=${SCRIPT_DIR}
Icon=${icon}
Terminal=${term}
Categories=Utility;
StartupNotify=false
ENTRY
    chmod +x "${path}"
    # PCManFM and the Wayland file managers both refuse to launch a .desktop
    # file they do not consider trusted, and say nothing about why.
    gio set "${path}" metadata::trusted true 2>/dev/null || true
}

for target_dir in "${DESKTOP}" "${APPS}"; do
    write_entry "${target_dir}/Endoscope.desktop" \
        "Endoscope" \
        "AtomS3R-CAM one-cable USB-C viewer (fullscreen)" \
        '"'"${SCRIPT_DIR}/run_usb.sh"'"' \
        "camera-video" "false"

    write_entry "${target_dir}/Update Endoscope.desktop" \
        "Update Endoscope" \
        "Pull the newest Endoscope code from GitHub, then test it" \
        '"'"${SCRIPT_DIR}/update_endoscope.sh"'"' \
        "system-software-update" "true"
done

update-desktop-database "${APPS}" 2>/dev/null || true

echo "Installed into ${DESKTOP}:"
echo "  Endoscope           -> ${SCRIPT_DIR}/run_usb.sh   (fullscreen)"
echo "  Update Endoscope    -> ${SCRIPT_DIR}/update_endoscope.sh"
[[ -d "${BACKUP}" ]] && ls -1 "${BACKUP}" 2>/dev/null | grep -q . && \
    echo "Older shortcuts were moved to ${BACKUP}"
exit 0
