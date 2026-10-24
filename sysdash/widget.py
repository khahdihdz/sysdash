"""Termux:Widget integration.

Termux:Widget has no CLI. It shows executable scripts found in
~/.shortcuts (interactive widget) and ~/.shortcuts/tasks (background task
widget). This module creates and removes those scripts safely.
"""
from __future__ import annotations

import os
import shlex
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any

from . import utils

MARKER = "# managed by sysdash (termux:widget) v1"
DEFAULT_NAME = "System Dashboard"
DEFAULT_TASK_NAME = "Sysdash Snapshot"
FORBIDDEN_CHARS = set('/\\:*?"<>|')
_APP_CACHE: tuple[float, bool | None] | None = None


class WidgetError(RuntimeError):
    pass


def _bash() -> str:
    return shutil.which("bash") or str(utils.prefix() / "bin" / "bash")


def _shortcuts_root(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home) / ".shortcuts"
    return utils.shortcuts_dir()


def _backup_root() -> Path:
    return utils.state_dir() / "backups" / "shortcuts"


def app_installed() -> bool | None:
    """Return True/False, or None when pm is unavailable."""
    global _APP_CACHE
    now = time.monotonic()
    if _APP_CACHE is not None and now - _APP_CACHE[0] < 30.0:
        return _APP_CACHE[1]
    result: bool | None = None
    path = utils.run_cmd(["pm", "path", "com.termux.widget"], timeout=3.0)
    if path is not None:
        result = bool(path.strip())
    else:
        raw = utils.run_cmd(["pm", "list", "packages", "com.termux.widget"], timeout=5.0)
        if raw is not None:
            result = "package:com.termux.widget" in raw.splitlines()
    _APP_CACHE = (now, result)
    return result


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise WidgetError("Tên shortcut không được để trống")
    if name in {".", ".."} or name.startswith("."):
        raise WidgetError("Tên shortcut không được bắt đầu bằng dấu chấm")
    if len(name) > 64:
        raise WidgetError("Tên shortcut tối đa 64 ký tự")
    if any(ch in FORBIDDEN_CHARS or ch in "\x00\r\n" for ch in name):
        raise WidgetError('Tên shortcut không được chứa / \\ : * ? " < > |')
    return name


def dashboard_script(bin_path: Path, name: str = DEFAULT_NAME) -> str:
    bash = _bash()
    quoted = shlex.quote(str(bin_path))
    return f"""#!{bash}
{MARKER}
# Shortcut mở dashboard trong một session Termux mới.
cd "$HOME" || exit 1
if [ ! -x {quoted} ]; then
    printf '%s\\n' "sysdash chưa được cài hoặc wrapper bị thiếu."
    printf '%s\\n' "Chạy: sysdash install"
    read -r -p "Nhấn Enter để đóng..." _
    exit 1
fi
exec {quoted} dashboard
"""


def task_script(bin_path: Path, keep: int = 20, notify: bool = False) -> str:
    bash = _bash()
    quoted = shlex.quote(str(bin_path))
    extra = " --notify" if notify else ""
    return f"""#!{bash}
{MARKER} (task)
# Task chạy nền: không TUI, không đọc stdin.
cd "$HOME" || exit 1
if [ ! -x {quoted} ]; then
    mkdir -p "$HOME/.local/state/sysdash" 2>/dev/null || true
    printf '%s\\n' "$(date '+%Y-%m-%d %H:%M:%S') sysdash wrapper missing: {quoted}" \\
        >> "$HOME/.local/state/sysdash/widget.log" 2>/dev/null || true
    exit 1
fi
exec {quoted} snapshot --keep {int(keep)}{extra}
"""


def _has_marker(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(5):
                line = handle.readline()
                if not line:
                    break
                if MARKER in line:
                    return True
    except OSError:
        return False
    return False


def _validate_shell(content: str, target: Path) -> None:
    bash = _bash()
    if not Path(bash).exists():
        return
    tmp_dir = utils.state_dir() / "tmp"
    utils.ensure_dir(tmp_dir)
    tmp = tmp_dir / f"{target.name}.check.{os.getpid()}"
    try:
        tmp.write_text(content, encoding="utf-8")
        result = utils.run_cmd([bash, "-n", str(tmp)], timeout=5.0)
        if result is None:
            raise WidgetError("Script widget không hợp lệ (bash -n thất bại)")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _write_script(
    target: Path,
    content: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> str:
    target = Path(target)
    if target.exists():
        managed = _has_marker(target)
        if not managed and not force:
            raise WidgetError(
                f"{target} đã tồn tại và không do sysdash quản lý; dùng --force để ghi đè"
            )
        if managed:
            try:
                if target.read_text(encoding="utf-8", errors="replace") == content:
                    try:
                        target.chmod(0o700)
                    except OSError:
                        pass
                    return "unchanged"
            except OSError:
                pass
        if dry_run:
            return "would-overwrite"
        utils.backup_file(target, _backup_root())
    elif dry_run:
        return "would-create"

    if dry_run:
        return "would-create"

    _validate_shell(content, target)
    utils.ensure_dir(target.parent)
    tmp = utils.state_dir() / "tmp"
    utils.ensure_dir(tmp)
    temp_file = tmp / f"{target.name}.tmp.{os.getpid()}"
    try:
        temp_file.write_text(content, encoding="utf-8")
        os.chmod(temp_file, 0o700)
        os.replace(temp_file, target)
    finally:
        try:
            temp_file.unlink()
        except OSError:
            pass
    try:
        target.chmod(0o700)
    except OSError:
        pass
    return "created"


def install(
    *,
    tasks: bool = False,
    name: str = DEFAULT_NAME,
    task_name: str = DEFAULT_TASK_NAME,
    bin_path: Path | None = None,
    home: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    keep: int = 20,
    notify: bool = False,
) -> dict[str, Any]:
    name = validate_name(name)
    task_name = validate_name(task_name)
    root = _shortcuts_root(home)
    tasks_root = root / "tasks"
    bin_path = Path(bin_path) if bin_path else utils.bin_dir() / "sysdash"

    report: dict[str, Any] = {
        "app_installed": app_installed(),
        "shortcuts": [],
        "warnings": [],
    }
    if report["app_installed"] is False and not force:
        raise WidgetError(
            "Termux:Widget chưa cài (com.termux.widget). "
            "Cài Termux:Widget từ F-Droid rồi chạy lại, hoặc dùng --force."
        )
    if report["app_installed"] is None:
        report["warnings"].append("Không kiểm tra được Termux:Widget (thiếu pm)")
    if not bin_path.exists() and not dry_run and not force:
        raise WidgetError(
            f"Chưa có lệnh {bin_path}. Chạy 'sysdash install' trước, "
            "hoặc dùng --force nếu muốn tạo script trước."
        )
    if not os.access(bin_path, os.X_OK) and bin_path.exists():
        report["warnings"].append(f"{bin_path} không có quyền thực thi")

    dashboard = root / name
    status = _write_script(dashboard, dashboard_script(bin_path, name), dry_run=dry_run, force=force)
    report["shortcuts"].append((dashboard, status))

    if tasks:
        task = tasks_root / task_name
        status = _write_script(
            task,
            task_script(bin_path, keep=keep, notify=notify),
            dry_run=dry_run,
            force=force,
        )
        report["shortcuts"].append((task, status))
    return report


def remove(*, home: Path | None = None, dry_run: bool = False) -> list[Path]:
    root = _shortcuts_root(home)
    removed: list[Path] = []
    for directory in (root, root / "tasks"):
        if not directory.is_dir():
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if not path.is_file() or path.is_symlink():
                    continue
            except OSError:
                continue
            if not _has_marker(path):
                continue
            if dry_run:
                removed.append(path)
                continue
            try:
                path.unlink()
                removed.append(path)
            except OSError:
                pass
    return removed


def list_scripts(*, home: Path | None = None, bin_path: Path | None = None) -> list[dict[str, Any]]:
    root = _shortcuts_root(home)
    bin_path = Path(bin_path) if bin_path else utils.bin_dir() / "sysdash"
    rows: list[dict[str, Any]] = []
    for directory, kind in ((root, "dashboard"), (root / "tasks", "task")):
        if not directory.is_dir():
            continue
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if not path.is_file():
                    continue
                mode = stat.S_IMODE(path.stat().st_mode)
            except OSError:
                continue
            managed = _has_marker(path)
            rows.append(
                {
                    "kind": kind,
                    "path": path,
                    "name": path.name,
                    "managed": managed,
                    "mode": oct(mode),
                    "executable": bool(mode & 0o100),
                    "target_ok": bin_path.exists() and os.access(bin_path, os.X_OK),
                }
            )
    return rows


def doctor(*, home: Path | None = None, bin_path: Path | None = None) -> list[tuple[str, str, str]]:
    root = _shortcuts_root(home)
    bin_path = Path(bin_path) if bin_path else utils.bin_dir() / "sysdash"
    checks: list[tuple[str, str, str]] = []
    app = app_installed()
    if app is True:
        checks.append(("PASS", "Termux:Widget", "đã cài (com.termux.widget)"))
    elif app is False:
        checks.append(("WARN", "Termux:Widget", "chưa cài; cài từ F-Droid nếu muốn dùng widget"))
    else:
        checks.append(("WARN", "Termux:Widget", "không kiểm tra được (thiếu pm)"))

    if root.is_dir():
        checks.append(("PASS", "Thư mục shortcut", str(root)))
    else:
        checks.append(("WARN", "Thư mục shortcut", f"chưa có {root}"))

    scripts = [row for row in list_scripts(home=home, bin_path=bin_path) if row["managed"]]
    if not scripts:
        checks.append(("WARN", "Script widget", "chưa tạo; chạy 'sysdash widget install'"))
    for row in scripts:
        status = "PASS"
        detail = f"{row['kind']}: {row['path']} ({row['mode']})"
        if not row["executable"]:
            status = "FAIL"
            detail += " — thiếu quyền thực thi"
        if not row["target_ok"]:
            status = "FAIL"
            detail += f" — wrapper không chạy được: {bin_path}"
        checks.append((status, "Script widget", detail))
    return checks
