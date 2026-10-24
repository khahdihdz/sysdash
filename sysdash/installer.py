"""Install, repair, doctor and uninstall for sysdash."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import utils, widget

WRAPPER_MARKER = "# managed by sysdash"
SOURCE_MARKER = ".sysdash-installed"
MIN_PYTHON = (3, 10)


class InstallerError(RuntimeError):
    pass


def source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def wrapper_path() -> Path:
    return utils.bin_dir() / "sysdash"


def _python_bin() -> str:
    return shutil.which("python3") or sys.executable or "/usr/bin/python3"


def _is_managed_wrapper(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:500]
    except OSError:
        return False
    return WRAPPER_MARKER in head


def _wrapper_text(dest: Path) -> str:
    python = _python_bin()
    return f"""#!{python}
{WRAPPER_MARKER}
# Wrapper tự sinh; đừng sửa tay. Gỡ bằng: sysdash uninstall
import sys
sys.path.insert(0, {str(dest)!r})
from sysdash.cli import main
raise SystemExit(main())
"""


def write_wrapper(
    dest: Path | None = None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> str:
    dest = Path(dest) if dest else utils.install_dir()
    target = wrapper_path()
    content = _wrapper_text(dest)
    existed = target.exists()
    if existed:
        if not _is_managed_wrapper(target) and not force:
            raise InstallerError(
                f"{target} đã tồn tại và không phải wrapper của sysdash; "
                "dùng --force để ghi đè"
            )
        if dry_run:
            return "would-update"
        utils.backup_file(target, utils.state_dir() / "backups" / "bin")
    elif dry_run:
        return "would-create"

    if dry_run:
        return "would-create"
    utils.ensure_dir(target.parent)
    utils.atomic_write(target, content, mode=0o700)
    return "updated" if existed else "created"


def _ignore(_directory: str, names: list[str]) -> set[str]:
    ignored = {
        ".git",
        ".hg",
        ".svn",
        ".pytest_cache",
        ".mypy_cache",
        "__pycache__",
        ".venv",
        "venv",
        "tests",
        "node_modules",
    }
    return {name for name in names if name in ignored or name.endswith(".pyc")}


def _source_marker(dest: Path) -> bool:
    return (dest / SOURCE_MARKER).exists()


def copy_source(
    src: Path | None = None,
    dest: Path | None = None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> str:
    src = Path(src) if src else source_root()
    dest = Path(dest) if dest else utils.install_dir()
    src = src.resolve()
    dest = dest.resolve()
    if src == dest:
        return "already-installed"

    if dest.exists():
        if not _source_marker(dest) and not force:
            raise InstallerError(
                f"{dest} đã tồn tại và không do sysdash quản lý; dùng --force để ghi đè"
            )
        if dry_run:
            return "would-replace"
    elif dry_run:
        return "would-install"

    if not (src / "sysdash" / "__init__.py").exists():
        raise InstallerError(f"Không tìm thấy source sysdash trong {src}")
    if not dry_run:
        utils.ensure_dir(dest.parent)
    tmp = dest.with_name(f"{dest.name}.new.{os.getpid()}")
    backup: Path | None = None
    try:
        if dest.exists():
            utils.ensure_dir(utils.state_dir() / "backups")
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = utils.state_dir() / "backups" / f"source-{stamp}"
            if backup.exists():
                backup = backup.with_name(f"{backup.name}-{os.getpid()}")
            if not dry_run:
                shutil.move(str(dest), str(backup))
        if not dry_run:
            shutil.copytree(src, tmp, ignore=_ignore)
            marker = {
                "app": "sysdash",
                "version": utils.VERSION,
                "source": str(src),
                "installed_at": utils.now_iso(),
            }
            utils.atomic_write(tmp / SOURCE_MARKER, json.dumps(marker, ensure_ascii=False, indent=2) + "\n", mode=0o600)
            os.replace(tmp, dest)
        return "replaced" if backup else "installed"
    except Exception:
        if not dry_run:
            shutil.rmtree(tmp, ignore_errors=True)
            if backup and backup.exists() and not dest.exists():
                shutil.move(str(backup), str(dest))
        raise


def install(
    *,
    source: Path | None = None,
    dest: Path | None = None,
    with_widget: bool = False,
    tasks: bool = False,
    dry_run: bool = False,
    force: bool = False,
    yes: bool = False,
) -> dict[str, Any]:
    if sys.version_info < MIN_PYTHON:
        raise InstallerError("Cần Python >= 3.10")
    src = Path(source) if source else source_root()
    dest = Path(dest) if dest else utils.install_dir()
    report: dict[str, Any] = {
        "dest": dest,
        "wrapper": wrapper_path(),
        "config": utils.config_path(),
        "widget": None,
        "warnings": [],
        "dry_run": dry_run,
    }

    report["copy"] = copy_source(src, dest, dry_run=dry_run, force=force)
    report["wrapper_status"] = write_wrapper(dest, dry_run=dry_run, force=force)

    if not utils.config_path().exists():
        if dry_run:
            report["config_status"] = "would-create"
        else:
            config_mod.save(config_mod.Config())
            report["config_status"] = "created"
    else:
        cfg = config_mod.load()
        if not utils.config_path().read_text(encoding="utf-8", errors="replace").strip():
            report["config_status"] = str(config_mod.save(cfg))
        else:
            report["config_status"] = "kept"

    if with_widget:
        try:
            report["widget"] = widget.install(
                tasks=tasks,
                bin_path=wrapper_path(),
                dry_run=dry_run,
                force=force,
                keep=config_mod.load().snapshot_keep,
            )
        except widget.WidgetError as exc:
            report["warnings"].append(f"Termux:Widget: {exc}")
    return report


def repair(
    *,
    dry_run: bool = False,
    with_widget: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    dest = utils.install_dir()
    report: dict[str, Any] = {"dest": dest, "warnings": [], "dry_run": dry_run}
    if not (dest / "sysdash" / "__init__.py").exists():
        raise InstallerError(f"Không tìm thấy bản cài tại {dest}. Chạy 'sysdash install' trước.")
    report["wrapper_status"] = write_wrapper(dest, dry_run=dry_run, force=force)
    if not utils.config_path().exists():
        report["config_status"] = str(config_mod.save(config_mod.Config()))
    else:
        cfg = config_mod.load()
        report["config_status"] = str(config_mod.save(cfg))
    if with_widget:
        try:
            report["widget"] = widget.install(
                bin_path=wrapper_path(),
                dry_run=dry_run,
                force=force,
            )
        except widget.WidgetError as exc:
            report["warnings"].append(f"Termux:Widget: {exc}")
    return report


def _remove_managed_wrapper(dry_run: bool) -> tuple[str, list[str]]:
    target = wrapper_path()
    warnings: list[str] = []
    if not target.exists():
        return "missing", warnings
    if not _is_managed_wrapper(target):
        warnings.append(f"{target} không phải wrapper của sysdash; giữ nguyên")
        return "kept", warnings
    if dry_run:
        return "would-remove", warnings
    try:
        target.unlink()
        return "removed", warnings
    except OSError as exc:
        warnings.append(f"Không xóa được {target}: {exc}")
        return "failed", warnings


def _safe_rmtree(path: Path) -> None:
    path = Path(path)
    if path.is_symlink():
        raise InstallerError(f"Từ chối xóa symlink: {path}")
    resolved = path.resolve()
    allowed = [Path.home().resolve(), utils.prefix().resolve()]
    if not any(resolved == root or resolved.is_relative_to(root) for root in allowed):
        raise InstallerError(f"Từ chối xóa ngoài thư mục cho phép: {resolved}")
    if not (resolved / SOURCE_MARKER).exists():
        raise InstallerError(f"Từ chối xóa {resolved}: thiếu marker {SOURCE_MARKER}")
    shutil.rmtree(resolved)


def uninstall(
    *,
    dry_run: bool = False,
    keep_config: bool = True,
    purge: bool = False,
    purge_source: bool = False,
    yes: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "removed": [],
        "warnings": [],
        "dry_run": dry_run,
    }
    report["widget_removed"] = [str(p) for p in widget.remove(dry_run=dry_run)]
    status, warnings = _remove_managed_wrapper(dry_run)
    report["wrapper"] = status
    report["warnings"].extend(warnings)

    if purge:
        for path in (utils.config_path(), utils.state_dir(), utils.cache_dir()):
            path = Path(path)
            if not path.exists():
                continue
            if dry_run:
                report["removed"].append(str(path))
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                report["removed"].append(str(path))
            except OSError as exc:
                report["warnings"].append(f"Không xóa được {path}: {exc}")
    elif not keep_config:
        cfg_path = utils.config_path()
        if cfg_path.exists():
            if dry_run:
                report["removed"].append(str(cfg_path))
            else:
                try:
                    cfg_path.unlink()
                    report["removed"].append(str(cfg_path))
                except OSError as exc:
                    report["warnings"].append(f"Không xóa được {cfg_path}: {exc}")

    if purge_source:
        dest = utils.install_dir()
        if not dest.exists():
            report["source"] = "missing"
        elif dry_run:
            report["source"] = "would-remove"
            report["removed"].append(str(dest))
        else:
            try:
                _safe_rmtree(dest)
                report["source"] = "removed"
                report["removed"].append(str(dest))
            except InstallerError as exc:
                report["warnings"].append(str(exc))
                report["source"] = "kept"
    return report


def doctor(*, include_widget: bool = True) -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= MIN_PYTHON:
        checks.append(("PASS", "Python", f"{version} ({sys.executable})"))
    else:
        checks.append(("FAIL", "Python", f"{version}; cần >= 3.10"))

    if importlib.util.find_spec("rich"):
        checks.append(("PASS", "rich", "đã cài"))
    else:
        checks.append(("WARN", "rich", "chưa cài; dùng fallback ANSI"))
    if importlib.util.find_spec("psutil"):
        checks.append(("PASS", "psutil", "đã cài (tùy chọn)"))
    else:
        checks.append(("WARN", "psutil", "chưa cài (không bắt buộc)"))

    commands = [
        "termux-battery-status",
        "termux-wifi-connectioninfo",
        "termux-notification-list",
        "termux-wake-lock",
        "pm",
    ]
    missing = [cmd for cmd in commands if not shutil.which(cmd)]
    if missing:
        checks.append(("WARN", "Termux API", "thiếu: " + ", ".join(missing)))
    else:
        checks.append(("PASS", "Termux API", "các lệnh cần thiết có sẵn"))

    for label, path in (("/proc", Path("/proc")), ("/sys", Path("/sys"))):
        if path.is_dir() and os.access(path, os.R_OK):
            checks.append(("PASS", f"Quyền đọc {label}", "OK"))
        else:
            checks.append(("WARN", f"Quyền đọc {label}", "không đọc được; một số chỉ số sẽ N/A"))

    dest = utils.install_dir()
    wrapper = wrapper_path()
    running_from_source = source_root().resolve() == dest.resolve()
    if running_from_source:
        checks.append(("PASS", "Source", f"đang chạy từ source: {source_root()}"))
    elif (dest / "sysdash" / "__init__.py").exists() and _source_marker(dest):
        checks.append(("PASS", "Source", str(dest)))
    elif dest.exists():
        checks.append(("WARN", "Source", f"{dest} tồn tại nhưng thiếu marker"))
    else:
        checks.append(("WARN", "Source", f"chưa cài vào {dest}"))

    if wrapper.exists() and _is_managed_wrapper(wrapper) and os.access(wrapper, os.X_OK):
        checks.append(("PASS", "Wrapper", str(wrapper)))
    elif wrapper.exists():
        checks.append(("FAIL", "Wrapper", f"{wrapper} không phải wrapper sysdash hoặc thiếu quyền"))
    else:
        checks.append(("WARN", "Wrapper", f"chưa có {wrapper}; chạy 'sysdash install'"))

    cfg_path = utils.config_path()
    if not cfg_path.exists():
        checks.append(("WARN", "Config", f"chưa có {cfg_path}"))
    else:
        try:
            cfg_path.read_text(encoding="utf-8")
            cfg = config_mod.load(cfg_path)
            if cfg.interval and cfg.collectors:
                checks.append(("PASS", "Config", str(cfg_path)))
            else:
                checks.append(("FAIL", "Config", "giá trị không hợp lệ"))
        except OSError as exc:
            checks.append(("FAIL", "Config", str(exc)))

    if include_widget:
        checks.extend(widget.doctor(bin_path=wrapper))
    return checks
