#!/data/data/com.termux/files/usr/bin/bash
# sysdash one-line installer.
#   curl -fsSL https://raw.githubusercontent.com/khahdihdz/sysdash/main/install.sh | bash
# Biến môi trường:
#   SYSDASH_REPO, SYSDASH_BRANCH, SYSDASH_NO_WIDGET=1, SYSDASH_TASKS=1, SYSDASH_FORCE=1
set -euo pipefail

REPO="${SYSDASH_REPO:-khahdihdz/sysdash}"
BRANCH="${SYSDASH_BRANCH:-main}"
TMP_ROOT="${TMPDIR:-${PREFIX:-/data/data/com.termux/files/usr}/tmp}"

say() { printf '%s\n' "$*"; }
die() { printf 'Lỗi: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "Chưa có python3. Chạy: pkg install python"
command -v curl >/dev/null 2>&1 || die "Chưa có curl. Chạy: pkg install curl"
command -v tar >/dev/null 2>&1 || die "Chưa có tar. Chạy: pkg install tar"
mkdir -p "$TMP_ROOT"
TMP="$(mktemp -d "$TMP_ROOT/sysdash-install.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

say "Đang tải sysdash từ $REPO ($BRANCH)..."
URL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
if ! curl -fsSL "$URL" -o "$TMP/src.tar.gz"; then
    die "Không tải được source. Kiểm tra mạng hoặc repo $REPO."
fi
tar -xzf "$TMP/src.tar.gz" -C "$TMP"
SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[ -n "$SRC" ] || die "Không giải nén được source."
[ -f "$SRC/sysdash/__init__.py" ] || die "Source tải về không hợp lệ."

ARGS=(install --yes --source "$SRC")
if [ "${SYSDASH_NO_WIDGET:-0}" != "1" ]; then
    ARGS+=(--widget)
fi
if [ "${SYSDASH_TASKS:-0}" = "1" ]; then
    ARGS+=(--tasks)
fi
if [ "${SYSDASH_FORCE:-0}" = "1" ]; then
    ARGS+=(--force)
fi

say "Đang cài lệnh sysdash..."
PYTHONPATH="$SRC" python3 -m sysdash "${ARGS[@]}"

if command -v sysdash >/dev/null 2>&1; then
    say ""
    say "Đã cài xong. Chạy:"
    say "  sysdash"
    say "  sysdash install --widget   # nếu muốn tạo lại shortcut Termux:Widget"
    say "  sysdash doctor"
else
    say ""
    say "Đã cài source nhưng chưa thấy lệnh sysdash trong PATH."
    say "Kiểm tra: echo \"\$PATH\" và thư mục \$PREFIX/bin."
fi
