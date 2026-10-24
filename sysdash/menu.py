"""Interactive menus for sysdash."""
from __future__ import annotations

import sys
from typing import Any, Sequence

from . import app, config as config_mod, installer, ui, utils, widget

try:
    from rich.prompt import Prompt
except ImportError:  # pragma: no cover
    Prompt = None  # type: ignore[assignment]


Item = tuple[str, str, str]


def _console() -> Any:
    return ui.make_console(force_terminal=sys.stdout.isatty())


def _ask(prompt: str, default: str) -> str:
    if Prompt is not None:
        try:
            return Prompt.ask(prompt, default=default)
        except (EOFError, KeyboardInterrupt):
            return default
    try:
        value = input(f"{prompt} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return value or default


def _confirm(prompt: str, default: bool = False) -> bool:
    default_text = "y" if default else "n"
    answer = _ask(f"{prompt} (y/n)", default_text).strip().lower()
    return answer in {"y", "yes", "có", "co"}


def _fallback_menu(title: str, items: Sequence[Item]) -> str | None:
    print(f"\n{title}")
    for key, label, description in items:
        print(f"  {key}) {label}")
        if description:
            print(f"      {description}")
    try:
        choice = input("Chọn: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    keys = {key for key, _, _ in items}
    if choice in keys:
        return choice
    if choice.isdigit() and 1 <= int(choice) <= len(items):
        return items[int(choice) - 1][0]
    return None


def interactive_menu(title: str, items: Sequence[Item], *, selected: int = 0) -> str | None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _fallback_menu(title, items)
    console = _console()
    selected = max(0, min(selected, len(items) - 1))
    with ui.alternate_screen(True), ui.raw_mode(True):
        while True:
            description = items[selected][2] if items else ""
            ui.render_menu(title, items, selected, console, description=description)
            key = ui.read_key(None)
            if key is None:
                continue
            if key in {"q", "esc", "ctrl-c", "ctrl-d"}:
                return None
            if key in {"down", "j"}:
                selected = (selected + 1) % len(items)
            elif key in {"up", "k"}:
                selected = (selected - 1) % len(items)
            elif key == "home":
                selected = 0
            elif key == "end":
                selected = len(items) - 1
            elif key == "enter":
                return items[selected][0]
            elif key.isdigit():
                for item_key, _label, _desc in items:
                    if item_key == key:
                        return item_key


def _wait() -> None:
    ui.wait_enter()


def _print_checks(checks: Sequence[tuple[str, str, str]]) -> None:
    console = _console()
    style = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}
    for status, name, detail in checks:
        line = f"[{style.get(status, 'white')}]{status:<4}[/] {name:<18} {detail}"
        if console:
            console.print(line)
        else:
            print(f"{status:<4} {name:<18} {detail}")


def _print_report(report: dict[str, Any]) -> None:
    console = _console()
    lines = []
    if report.get("dry_run"):
        lines.append("[yellow]Chế độ dry-run: chưa thay đổi gì.[/]")
    if "copy" in report:
        lines.append(f"Source: {report['copy']} → {report['dest']}")
    if "wrapper_status" in report:
        lines.append(f"Wrapper: {report['wrapper_status']} → {report['wrapper']}")
    if "config_status" in report:
        lines.append(f"Config: {report['config_status']} → {report['config']}")
    if report.get("source"):
        lines.append(f"Source: {report['source']}")
    if report.get("wrapper") and "wrapper_status" not in report:
        lines.append(f"Wrapper: {report['wrapper']}")
    if report.get("widget"):
        w = report["widget"]
        app_state = w.get("app_installed")
        lines.append(f"Termux:Widget: {'đã cài' if app_state else 'chưa cài' if app_state is False else 'không rõ'}")
        for path, status in w.get("shortcuts", []):
            lines.append(f"  {status}: {path}")
        for warning in w.get("warnings", []):
            lines.append(f"  [yellow]WARN[/] {warning}")
    for path in report.get("removed", []):
        lines.append(f"Đã xóa: {path}")
    for warning in report.get("warnings", []):
        lines.append(f"[yellow]WARN[/] {warning}")
    if not lines:
        lines.append("Hoàn tất.")
    if console:
        console.print("\n".join(lines))
    else:
        print("\n".join(lines))


def main_menu(cfg: config_mod.Config) -> None:
    while True:
        installed = installer.wrapper_path().exists()
        app_state = widget.app_installed()
        if app_state is True:
            widget_status = "app đã cài"
        elif app_state is False:
            widget_status = "app chưa cài"
        else:
            widget_status = "không kiểm tra được app"
        items: list[Item] = [
            ("1", "Mở Dashboard", "Theo dõi CPU, RAM, pin, mạng, tiến trình theo thời gian thực"),
            ("2", "Snapshot nhanh", "Lưu một ảnh trạng thái vào ~/.local/state/sysdash/snapshots"),
            ("3", "Cài đặt", "Interval, ngưỡng màu, collector, wake-lock, ngôn ngữ"),
            ("4", "Kiểm tra môi trường", "Chạy doctor cho Python, termux-api, quyền đọc, widget"),
            ("5", f"Cài đặt / Sửa lệnh ({'đã cài' if installed else 'chưa cài'})", "Tạo lệnh sysdash trong $PREFIX/bin"),
            ("6", f"Termux:Widget ({widget_status})", "Tạo shortcut dashboard và task snapshot"),
            ("7", "Gỡ cài đặt", "Xóa lệnh, shortcut; tùy chọn xóa config/source"),
            ("8", "Trợ giúp", "Xem hướng dẫn dùng và phím tắt"),
            ("9", "Thoát", "Đóng sysdash"),
        ]
        choice = interactive_menu("sysdash — System Dashboard", items)
        if choice in (None, "9"):
            return
        try:
            if choice == "1":
                action = app.run_dashboard(cfg)
                if action == "settings":
                    settings_menu(cfg)
                # 'menu' and 'quit' both return to this main menu.
            elif choice == "2":
                path = app.save_snapshot(cfg)
                console = _console()
                if console:
                    console.print(f"[green]Đã lưu:[/] {path}")
                else:
                    print(f"Đã lưu: {path}")
                _wait()
            elif choice == "3":
                settings_menu(cfg)
            elif choice == "4":
                _print_checks(installer.doctor())
                _wait()
            elif choice == "5":
                install_menu()
            elif choice == "6":
                widget_menu(cfg)
            elif choice == "7":
                uninstall_menu()
            elif choice == "8":
                ui.render_help(_console())
                _wait()
        except KeyboardInterrupt:
            return
        except Exception as exc:
            console = _console()
            message = f"Lỗi: {type(exc).__name__}: {exc}"
            if console:
                console.print(f"[red]{message}[/]")
            else:
                print(message)
            _wait()


def install_menu() -> None:
    tasks = _confirm("Tạo thêm task snapshot cho Termux:Widget?", False)
    try:
        report = installer.install(with_widget=True, tasks=tasks)
        _print_report(report)
    except Exception as exc:
        console = _console()
        message = f"Lỗi cài đặt: {type(exc).__name__}: {exc}"
        if console:
            console.print(f"[red]{message}[/]")
        else:
            print(message)
    _wait()


def uninstall_menu() -> None:
    if not _confirm("Gỡ lệnh sysdash và shortcut do sysdash tạo?", False):
        return
    purge = _confirm("Xóa cả config, log và snapshot?", False)
    purge_source = _confirm("Xóa source đã cài trong ~/.local/share/sysdash?", False)
    try:
        report = installer.uninstall(purge=purge, keep_config=not purge, purge_source=purge_source)
        _print_report(report)
    except Exception as exc:
        console = _console()
        message = f"Lỗi gỡ cài đặt: {type(exc).__name__}: {exc}"
        if console:
            console.print(f"[red]{message}[/]")
        else:
            print(message)
    _wait()


def print_checks(checks: Sequence[tuple[str, str, str]]) -> None:
    _print_checks(checks)


def print_report(report: dict[str, Any]) -> None:
    _print_report(report)


def _restore(cfg: config_mod.Config, original: config_mod.Config) -> None:
    cfg.interval = original.interval
    cfg.top_n = original.top_n
    cfg.wake_lock = original.wake_lock
    cfg.color = original.color
    cfg.lang = original.lang
    cfg.notify = original.notify
    cfg.snapshot_keep = original.snapshot_keep
    cfg.thresholds = dict(original.thresholds)
    cfg.collectors = dict(original.collectors)


def settings_menu(cfg: config_mod.Config) -> None:
    original = config_mod.copy_config(cfg)
    dirty = False
    while True:
        items: list[Item] = [
            ("1", f"Interval: {cfg.interval:.1f}s", "Thời gian giữa hai lần cập nhật (0.5–10s)"),
            ("2", "Ngưỡng cảnh báo", "Màu vàng/đỏ cho CPU, RAM, pin, nhiệt độ"),
            ("3", "Bật/tắt collector", "Chọn nguồn dữ liệu hiển thị trên dashboard"),
            ("4", f"Wake-lock: {'bật' if cfg.wake_lock else 'tắt'}", "Giữ CPU thức khi chạy nền"),
            ("5", f"Màu: {'bật' if cfg.color else 'tắt'}", "Tắt màu nếu terminal không hỗ trợ"),
            ("6", f"Ngôn ngữ: {cfg.lang}", "vi hoặc en"),
            ("7", "Termux:Widget", "Quản lý shortcut và task snapshot"),
            ("8", "Khôi phục mặc định", "Đưa toàn bộ cấu hình về mặc định"),
            ("9", "Lưu & quay lại", "Ghi thay đổi vào config.toml"),
        ]
        choice = interactive_menu("Cài đặt", items)
        if choice is None or choice == "9":
            if choice == "9":
                config_mod.save(cfg)
                console = _console()
                if console:
                    console.print("[green]Đã lưu cấu hình.[/]")
                else:
                    print("Đã lưu cấu hình.")
            elif dirty:
                _restore(cfg, original)
                console = _console()
                if console:
                    console.print("[yellow]Đã hủy thay đổi chưa lưu.[/]")
                else:
                    print("Đã hủy thay đổi chưa lưu.")
            return
        if choice == "1":
            raw = _ask("Interval giây", f"{cfg.interval:.1f}")
            try:
                cfg.interval = min(10.0, max(0.5, float(raw.replace(",", "."))))
                dirty = True
            except ValueError:
                pass
        elif choice == "2":
            thresholds_menu(cfg)
            dirty = True
        elif choice == "3":
            collectors_menu(cfg)
            dirty = True
        elif choice == "4":
            cfg.wake_lock = not cfg.wake_lock
            dirty = True
        elif choice == "5":
            cfg.color = not cfg.color
            dirty = True
        elif choice == "6":
            cfg.lang = "en" if cfg.lang == "vi" else "vi"
            dirty = True
        elif choice == "7":
            widget_menu(cfg)
        elif choice == "8":
            if _confirm("Khôi phục toàn bộ cấu hình mặc định?", False):
                fresh = config_mod.Config().validate()
                cfg.interval = fresh.interval
                cfg.top_n = fresh.top_n
                cfg.wake_lock = fresh.wake_lock
                cfg.color = fresh.color
                cfg.lang = fresh.lang
                cfg.notify = fresh.notify
                cfg.snapshot_keep = fresh.snapshot_keep
                cfg.thresholds = dict(fresh.thresholds)
                cfg.collectors = dict(fresh.collectors)
                dirty = True


def thresholds_menu(cfg: config_mod.Config) -> None:
    while True:
        items: list[Item] = [
            ("1", f"CPU: vàng {cfg.thresholds['cpu_warn']:.0f}% / đỏ {cfg.thresholds['cpu_crit']:.0f}%", "Ngưỡng phần trăm CPU"),
            ("2", f"RAM: vàng {cfg.thresholds['ram_warn']:.0f}% / đỏ {cfg.thresholds['ram_crit']:.0f}%", "Ngưỡng phần trăm RAM"),
            ("3", f"Pin: vàng {cfg.thresholds['battery_warn']:.0f}% / đỏ {cfg.thresholds['battery_crit']:.0f}%", "Cảnh báo khi pin thấp"),
            ("4", f"Nhiệt độ: vàng {cfg.thresholds['temp_warn']:.0f}°C / đỏ {cfg.thresholds['temp_crit']:.0f}°C", "Cảnh báo quá nhiệt"),
            ("5", "Quay lại", "Trở về cài đặt"),
        ]
        choice = interactive_menu("Ngưỡng cảnh báo", items)
        if choice in (None, "5"):
            return
        prefix = {"1": "cpu", "2": "ram", "3": "battery", "4": "temp"}.get(choice)
        if not prefix:
            continue
        warn_key = f"{prefix}_warn"
        crit_key = f"{prefix}_crit"
        warn = _ask(f"{prefix.upper()} vàng (%)", f"{cfg.thresholds[warn_key]:.0f}")
        crit = _ask(f"{prefix.upper()} đỏ (%)", f"{cfg.thresholds[crit_key]:.0f}")
        try:
            w = float(warn.replace(",", "."))
            c = float(crit.replace(",", "."))
            if w < c:
                cfg.thresholds[warn_key] = w
                cfg.thresholds[crit_key] = c
        except ValueError:
            pass


def collectors_menu(cfg: config_mod.Config) -> None:
    keys = list(config_mod.DEFAULT_COLLECTORS)
    while True:
        items: list[Item] = []
        for idx, key in enumerate(keys, start=1):
            state = "bật" if cfg.collectors.get(key, True) else "tắt"
            items.append((str(idx), f"{key:<10} {state}", "Enter để đổi trạng thái"))
        items.append((str(len(keys) + 1), "Quay lại", "Trở về cài đặt"))
        choice = interactive_menu("Collector", items)
        if choice is None or choice == str(len(keys) + 1):
            return
        try:
            idx = int(choice) - 1
        except ValueError:
            continue
        if 0 <= idx < len(keys):
            key = keys[idx]
            cfg.collectors[key] = not cfg.collectors.get(key, True)


def widget_menu(cfg: config_mod.Config) -> None:
    while True:
        scripts = widget.list_scripts(bin_path=installer.wrapper_path())
        managed = [row for row in scripts if row["managed"]]
        app_state = widget.app_installed()
        status = "đã cài" if app_state is True else "chưa cài" if app_state is False else "không rõ"
        items: list[Item] = [
            ("1", f"Tạo/cập nhật shortcut Dashboard ({len(managed)} script)", "Mở dashboard từ màn hình chính"),
            ("2", "Tạo/cập nhật task Snapshot", "Chạy nền, lưu JSON và rotate"),
            ("3", "Xóa shortcut/task do sysdash tạo", "Chỉ xóa file có marker"),
            ("4", "Kiểm tra tích hợp", "App, thư mục, quyền, marker, wrapper"),
            ("5", "Quay lại", "Trở về menu trước"),
        ]
        choice = interactive_menu(f"Termux:Widget — app {status}", items)
        if choice in (None, "5"):
            return
        try:
            if choice == "1":
                report = widget.install(bin_path=installer.wrapper_path(), force=False,
                                        keep=cfg.snapshot_keep)
                _print_report({"widget": report})
                _wait()
            elif choice == "2":
                report = widget.install(
                    tasks=True,
                    bin_path=installer.wrapper_path(),
                    keep=cfg.snapshot_keep,
                    notify=cfg.notify,
                )
                _print_report({"widget": report})
                _wait()
            elif choice == "3":
                if _confirm("Xóa các shortcut/task do sysdash tạo?", False):
                    removed = widget.remove()
                    console = _console()
                    if console:
                        console.print(f"Đã xóa {len(removed)} file.")
                        for path in removed:
                            console.print(f"  {path}")
                        console.print("Nhớ xóa widget trên launcher nếu còn.")
                    else:
                        print(f"Đã xóa {len(removed)} file.")
                    _wait()
            elif choice == "4":
                _print_checks(widget.doctor(bin_path=installer.wrapper_path()))
                _wait()
        except Exception as exc:
            console = _console()
            message = f"Lỗi Termux:Widget: {type(exc).__name__}: {exc}"
            if console:
                console.print(f"[red]{message}[/]")
            else:
                print(message)
            _wait()
