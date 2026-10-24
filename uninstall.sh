#!/data/data/com.termux/files/usr/bin/bash
# Gỡ sysdash. Chạy: curl -fsSL .../uninstall.sh | bash -s -- --yes
set -euo pipefail

if command -v sysdash >/dev/null 2>&1; then
    exec sysdash uninstall "$@"
fi

DEST="${SYSDASH_INSTALL_DIR:-$HOME/.local/share/sysdash}"
if [ -d "$DEST/sysdash" ]; then
    PYTHONPATH="$DEST" exec python3 -m sysdash uninstall "$@"
fi

printf '%s\n' "Không tìm thấy sysdash đã cài." >&2
exit 1
