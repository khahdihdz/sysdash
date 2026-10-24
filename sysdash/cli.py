"""Command line interface for sysdash."""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import Sequence

from . import app, config as config_mod, installer, menu, ui, utils, widget


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=argparse.SUPPRESS, help="Đường dẫn config.toml")
    parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS, help="Tắt màu")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sysdash",
        description="System Dashboard cho Termux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ví dụ:\n"
            "  sysdash                     mở menu\n"
            "  sysdash dashboard           mở dashboard\n"
            "  sysdash once                in snapshot\n"
            "  sysdash install --widget    cài lệnh + shortcut Termux:Widget\n"
        ),
    )
    parser.add_argument("--config", help="Đường dẫn config.toml")
    parser.add_argument("--no-color", action="store_true", help="Tắt màu")
    parser.add_argument("--version", action="version", version=f"sysdash {utils.VERSION}")
    sub = parser.add_subparsers(dest="command")

    dash = sub.add_parser("dashboard", help="Mở dashboard tương tác")
    _add_common(dash)
    dash.add_argument("--interval", type=float, help="Giây giữa hai lần cập nhật")
    dash.add_argument("--once", action="store_true", help="In một snapshot rồi thoát")
    dash.add_argument("--json", action="store_true", help="Xuất JSON rồi thoát")
    dash.add_argument("--wake-lock", action="store_true", help="Bật termux-wake-lock")
    dash.add_argument("--top", type=int, help="Số tiến trình hiển thị")

    for name, help_text in (("once", "In snapshot một lần"), ("json", "Xuất snapshot JSON")):
        cmd = sub.add_parser(name, help=help_text)
        _add_common(cmd)

    snap = sub.add_parser("snapshot", help="Lưu snapshot và rotate")
    _add_common(snap)
    snap.add_argument("--keep", type=int, default=None, help="Số file giữ lại")
    snap.add_argument("--notify", action="store_true", default=None, help="Gửi thông báo Termux")
    snap.add_argument("--dir", default=None, help="Thư mục lưu snapshot")

    settings = sub.add_parser("settings", help="Mở menu cài đặt")
    _add_common(settings)

    inst = sub.add_parser("install", help="Cài lệnh sysdash")
    _add_common(inst)
    inst.add_argument("--dry-run", action="store_true")
    inst.add_argument("--yes", action="store_true")
    inst.add_argument("--widget", action="store_true", help="Tạo shortcut Termux:Widget")
    inst.add_argument("--shortcut", action="store_true", help="Alias của --widget")
    inst.add_argument("--tasks", action="store_true", help="Tạo thêm task snapshot")
    inst.add_argument("--force", action="store_true")
    inst.add_argument("--source", default=None, help="Thư mục source (dùng cho installer)")
    inst.add_argument("--dest", default=None, help="Thư mục cài đặt")

    rep = sub.add_parser("repair", help="Sửa wrapper/config")
    _add_common(rep)
    rep.add_argument("--dry-run", action="store_true")
    rep.add_argument("--widget", action="store_true")
    rep.add_argument("--force", action="store_true")

    doc = sub.add_parser("doctor", help="Kiểm tra môi trường")
    _add_common(doc)

    uni = sub.add_parser("uninstall", help="Gỡ cài đặt")
    _add_common(uni)
    uni.add_argument("--dry-run", action="store_true")
    uni.add_argument("--yes", action="store_true")
    uni.add_argument("--keep-config", action="store_true", help="Giữ config (mặc định)")
    uni.add_argument("--purge", action="store_true", help="Xóa config, log, cache")
    uni.add_argument("--purge-source", action="store_true", help="Xóa source đã cài")

    wgt = sub.add_parser("widget", help="Quản lý Termux:Widget")
    wsub = wgt.add_subparsers(dest="widget_command", required=True)
    wi = wsub.add_parser("install", help="Tạo shortcut")
    _add_common(wi)
    wi.add_argument("--tasks", action="store_true")
    wi.add_argument("--name", default=widget.DEFAULT_NAME)
    wi.add_argument("--task-name", default=widget.DEFAULT_TASK_NAME)
    wi.add_argument("--keep", type=int, default=20)
    wi.add_argument("--notify", action="store_true")
    wi.add_argument("--dry-run", action="store_true")
    wi.add_argument("--force", action="store_true")
    wr = wsub.add_parser("remove", help="Xóa shortcut do sysdash tạo")
    _add_common(wr)
    wr.add_argument("--dry-run", action="store_true")
    wr.add_argument("--yes", action="store_true")
    wl = wsub.add_parser("list", help="Liệt kê shortcut")
    _add_common(wl)
    wd = wsub.add_parser("doctor", help="Kiểm tra tích hợp widget")
    _add_common(wd)

    hlp = sub.add_parser("help", help="Trợ giúp")
    _add_common(hlp)
    return parser


def _load_cfg(args: argparse.Namespace) -> config_mod.Config:
    path = Path(args.config).expanduser() if getattr(args, "config", None) else None
    cfg = config_mod.load(path)
    if getattr(args, "no_color", False):
        cfg.color = False
    if getattr(args, "interval", None) is not None:
        cfg.interval = min(10.0, max(0.5, float(args.interval)))
    if getattr(args, "top", None) is not None:
        cfg.top_n = min(50, max(1, int(args.top)))
    if getattr(args, "wake_lock", False):
        cfg.wake_lock = True
    return cfg


def _confirm(prompt: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("Cần --yes khi chạy không tương tác.", file=sys.stderr)
        return False
    try:
        return input(f"{prompt} (y/n): ").strip().lower() in {"y", "yes", "có", "co"}
    except (EOFError, KeyboardInterrupt):
        return False


def _print_widget_list() -> int:
    rows = widget.list_scripts(bin_path=installer.wrapper_path())
    if not rows:
        print("Chưa có shortcut Termux:Widget nào.")
        return 0
    print(f"{'LOẠI':<10} {'TÊN':<26} {'QUYỀN':<7} {'MARKER':<7} {'TARGET'}")
    for row in rows:
        print(
            f"{row['kind']:<10} {utils.truncate(row['name'], 26):<26} "
            f"{row['mode']:<7} {'có' if row['managed'] else 'không':<7} "
            f"{'OK' if row['target_ok'] else 'THIẾU'}"
        )
    return 0


def _run(args: argparse.Namespace) -> int:
    parser = build_parser()
    if args.command is None:
        if sys.stdin.isatty() and sys.stdout.isatty():
            menu.main_menu(_load_cfg(args))
            return 0
        parser.print_help()
        return 2

    cfg = _load_cfg(args)

    if args.command == "dashboard":
        if args.json:
            print(app.snapshot_json(cfg))
            return 0
        if args.once:
            app.print_snapshot(cfg)
            return 0
        action = app.run_dashboard(cfg)
        if action == "menu":
            menu.main_menu(cfg)
        elif action == "settings":
            menu.settings_menu(cfg)
        return 0

    if args.command == "once":
        app.print_snapshot(cfg)
        return 0

    if args.command == "json":
        print(app.snapshot_json(cfg))
        return 0

    if args.command == "snapshot":
        path = app.save_snapshot(
            cfg,
            keep=args.keep,
            notify=args.notify,
            out_dir=Path(args.dir).expanduser() if args.dir else None,
        )
        print(path)
        return 0

    if args.command == "settings":
        menu.settings_menu(cfg)
        return 0

    if args.command == "install":
        report = installer.install(
            source=Path(args.source).expanduser() if args.source else None,
            dest=Path(args.dest).expanduser() if args.dest else None,
            with_widget=bool(args.widget or args.shortcut),
            tasks=bool(args.tasks),
            dry_run=bool(args.dry_run),
            force=bool(args.force),
            yes=bool(args.yes),
        )
        menu.print_report(report)
        return 0

    if args.command == "repair":
        report = installer.repair(
            dry_run=bool(args.dry_run),
            with_widget=bool(args.widget),
            force=bool(args.force),
        )
        menu.print_report(report)
        return 0

    if args.command == "doctor":
        checks = installer.doctor()
        menu.print_checks(checks)
        return 1 if any(status == "FAIL" for status, _, _ in checks) else 0

    if args.command == "uninstall":
        if not args.dry_run and not _confirm("Gỡ lệnh sysdash và shortcut widget?", args.yes):
            return 2
        report = installer.uninstall(
            dry_run=bool(args.dry_run),
            keep_config=not bool(args.purge),
            purge=bool(args.purge),
            purge_source=bool(args.purge_source),
            yes=bool(args.yes),
        )
        menu.print_report(report)
        return 0

    if args.command == "widget":
        if args.widget_command == "install":
            report = widget.install(
                tasks=bool(args.tasks),
                name=args.name,
                task_name=args.task_name,
                bin_path=installer.wrapper_path(),
                dry_run=bool(args.dry_run),
                force=bool(args.force),
                keep=args.keep,
                notify=bool(args.notify),
            )
            menu.print_report({"widget": report})
            return 0
        if args.widget_command == "remove":
            if not args.dry_run and not _confirm("Xóa shortcut/task do sysdash tạo?", args.yes):
                return 2
            removed = widget.remove(dry_run=bool(args.dry_run))
            if args.dry_run:
                print("Sẽ xóa:")
            for path in removed:
                print(path)
            if not removed:
                print("Không có file nào do sysdash quản lý.")
            return 0
        if args.widget_command == "list":
            return _print_widget_list()
        if args.widget_command == "doctor":
            checks = widget.doctor(bin_path=installer.wrapper_path())
            menu.print_checks(checks)
            return 1 if any(status == "FAIL" for status, _, _ in checks) else 0

    if args.command == "help":
        ui.render_help(ui.make_console(force_terminal=sys.stdout.isatty()))
        return 0
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except (installer.InstallerError, widget.WidgetError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        try:
            utils.ensure_dir(utils.state_dir())
            with (utils.state_dir() / "sysdash.log").open("a", encoding="utf-8") as handle:
                handle.write(f"{utils.now_iso()} {type(exc).__name__}: {exc}\n")
        except OSError:
            pass
        if os.environ.get("SYSDASH_DEBUG"):
            traceback.print_exc()
        print(f"Lỗi: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
