"""Rendering and terminal helpers for sysdash."""
from __future__ import annotations

import os
import select
import shutil
import sys
import termios
import time
import tty
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterable, Iterator, Sequence

from . import utils
from .config import Config, color_for

try:  # rich is optional; the dashboard still works without it.
    from rich import box
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    HAS_RICH = True
except ImportError:  # pragma: no cover - exercised only on minimal installs
    HAS_RICH = False
    Console = None  # type: ignore[assignment]
    Group = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    Text = None  # type: ignore[assignment]
    Columns = None  # type: ignore[assignment]
    box = None  # type: ignore[assignment]


BLOCKS = "▁▂▃▄▅▆▇█"


def make_console(no_color: bool = False, force_terminal: bool = False) -> Any:
    if not HAS_RICH:
        return None
    color_system = None if no_color else "auto"
    return Console(
        color_system=color_system,
        force_terminal=force_terminal or None,
        highlight=False,
        soft_wrap=False,
    )


def bar_text(percent: float | None, width: int = 18, color: str | None = None) -> Any:
    if not HAS_RICH:
        if percent is None:
            return "N/A"
        filled = int(round(utils.clamp(percent) * width / 100.0))
        return "█" * filled + "░" * (width - filled)
    if percent is None:
        return Text("N/A", style="dim")
    width = max(4, width)
    filled = int(round(utils.clamp(percent) * width / 100.0))
    text = Text()
    text.append("█" * filled, style=color or "green")
    text.append("░" * (width - filled), style="grey37")
    return text


def sparkline(values: Sequence[float], width: int = 24) -> str:
    values = list(values)[-width:]
    if not values:
        return ""
    peak = max(values) or 1.0
    out = []
    for value in values:
        idx = int(max(0.0, min(1.0, value / peak)) * (len(BLOCKS) - 1))
        out.append(BLOCKS[idx])
    return "".join(out)


def _line(label: str, value: str, style: str = "white", width: int = 12) -> Any:
    if not HAS_RICH:
        return f"{label:<{width}} {value}"
    text = Text()
    text.append(f"{label:<{width}}", style="dim")
    text.append(value, style=style)
    return text


def _safe(value: Any, default: str = "N/A") -> str:
    if value is None or value == "":
        return default
    return str(value)


def _threshold(cfg: Config, key: str) -> tuple[float, float]:
    warn = cfg.thresholds.get(f"{key}_warn", 60.0)
    crit = cfg.thresholds.get(f"{key}_crit", 85.0)
    return warn, crit


def _cpu_panel(snap: dict[str, Any], cfg: Config, history: Sequence[float], compact: bool) -> Any:
    cpu = snap.get("cpu") or {}
    pct = cpu.get("percent")
    warn, crit = _threshold(cfg, "cpu")
    color = color_for(pct, warn, crit)
    lines: list[Any] = []
    bar = bar_text(pct, 20 if not compact else 14, color)
    line = Text() if HAS_RICH else ""
    if HAS_RICH:
        line.append("Tổng  ", style="dim")
        line.append_text(bar)
        line.append(f"  {utils.fmt_pct(pct, 1)}", style=color)
        lines.append(line)
    else:
        lines.append(f"Tổng  {bar}  {utils.fmt_pct(pct, 1)}")
    cores = cpu.get("cores") or []
    limit = 4 if compact else 8
    for core in cores[:limit]:
        cpct = core.get("percent")
        ccolor = color_for(cpct, warn, crit)
        if HAS_RICH:
            row = Text()
            row.append(f"cpu{core.get('id', '?')} ", style="dim")
            row.append_text(bar_text(cpct, 14 if not compact else 10, ccolor))
            row.append(f" {utils.fmt_pct(cpct)}", style=ccolor)
            lines.append(row)
        else:
            lines.append(f"cpu{core.get('id', '?')} {bar_text(cpct, 10)} {utils.fmt_pct(cpct)}")
    if not compact and history:
        spark = sparkline(history, 28)
        if HAS_RICH:
            row = Text()
            row.append("60s   ", style="dim")
            row.append(spark, style=color)
            lines.append(row)
        else:
            lines.append(f"60s   {spark}")
    freq = cpu.get("freq_mhz")
    load = cpu.get("load") or []
    extras = []
    if freq:
        extras.append(f"{freq:.0f} MHz")
    if load:
        extras.append("load " + " ".join(f"{v:.2f}" for v in load[:3]))
    if extras:
        lines.append(_line("Tần số", "  ".join(extras), "cyan"))
    if cpu.get("source") == "process-sum":
        lines.append(_line("Ghi chú", "ước tính từ tiến trình (Android chặn /proc/stat)", "dim"))
    if not cores and pct is None:
        lines.append(_line("Trạng thái", "Không có dữ liệu /proc/stat", "yellow"))
    return Panel(Group(*lines) if HAS_RICH else "\n".join(lines), title="CPU", border_style=color)


def _memory_panel(snap: dict[str, Any], cfg: Config) -> Any:
    mem = snap.get("memory") or {}
    pct = mem.get("percent")
    warn, crit = _threshold(cfg, "ram")
    color = color_for(pct, warn, crit)
    if not mem.get("available"):
        return Panel("Không đọc được /proc/meminfo", title="Bộ nhớ", border_style="yellow")
    lines: list[Any] = []
    if HAS_RICH:
        row = Text()
        row.append("Đã dùng ", style="dim")
        row.append_text(bar_text(pct, 18, color))
        row.append(f" {utils.fmt_pct(pct, 1)}", style=color)
        lines.append(row)
    else:
        lines.append(f"Đã dùng {bar_text(pct, 18)} {utils.fmt_pct(pct, 1)}")
    lines.append(_line("RAM", f"{utils.fmt_bytes(mem.get('used'))} / {utils.fmt_bytes(mem.get('total'))}"))
    lines.append(_line("Còn trống", utils.fmt_bytes(mem.get("available_bytes"))))
    swap_pct = mem.get("swap_percent")
    if mem.get("swap_total"):
        lines.append(_line("Swap", f"{utils.fmt_bytes(mem.get('swap_used'))} / {utils.fmt_bytes(mem.get('swap_total'))} ({utils.fmt_pct(swap_pct, 0)})"))
    else:
        lines.append(_line("Swap", "không có", "dim"))
    return Panel(Group(*lines) if HAS_RICH else "\n".join(lines), title="Bộ nhớ", border_style=color)


def _battery_panel(snap: dict[str, Any], cfg: Config) -> Any:
    battery = snap.get("battery") or {}
    pct = battery.get("percent")
    warn, crit = _threshold(cfg, "battery")
    color = color_for(pct, warn, crit, reverse=True) if pct is not None else "dim"
    if not battery.get("available"):
        reason = battery.get("error") or "Không có dữ liệu pin"
        return Panel(reason, title="Pin", border_style="yellow")
    lines: list[Any] = []
    if HAS_RICH:
        row = Text()
        row.append_text(bar_text(pct, 18, color))
        row.append(f" {utils.fmt_pct(pct, 0)}", style=color)
        lines.append(row)
    else:
        lines.append(f"{bar_text(pct, 18)} {utils.fmt_pct(pct, 0)}")
    lines.append(_line("Trạng thái", utils.human_status(battery.get("status")), color))
    temp = battery.get("temp_c")
    if temp is not None:
        lines.append(_line("Nhiệt độ", f"{temp:.1f} °C", "cyan"))
    voltage = battery.get("voltage_v")
    current = battery.get("current_ma")
    if voltage:
        lines.append(_line("Điện áp", f"{voltage:.2f} V", "cyan"))
    if current:
        lines.append(_line("Dòng", f"{current:.0f} mA", "cyan"))
    source = battery.get("source")
    if source:
        lines.append(_line("Nguồn", source, "dim"))
    return Panel(Group(*lines) if HAS_RICH else "\n".join(lines), title="Pin", border_style=color)


def _thermal_panel(snap: dict[str, Any], cfg: Config) -> Any:
    thermal = snap.get("thermal") or {}
    zones = thermal.get("zones") or []
    max_c = thermal.get("max_c")
    warn, crit = _threshold(cfg, "temp")
    color = color_for(max_c, warn, crit)
    if not zones:
        return Panel(thermal.get("error") or "Không đọc được nhiệt độ", title="Nhiệt độ", border_style="yellow")
    lines: list[Any] = []
    for zone in zones[:6]:
        temp = zone.get("temp_c")
        zcolor = color_for(temp, warn, crit)
        name = utils.truncate(str(zone.get("name", "zone")), 18)
        lines.append(_line(name, f"{temp:.1f} °C", zcolor, width=18))
    if HAS_RICH:
        lines.append(Text(f"Max {max_c:.1f} °C", style=f"bold {color}"))
    else:
        lines.append(f"Max {max_c:.1f} °C")
    return Panel(Group(*lines) if HAS_RICH else "\n".join(lines), title="Nhiệt độ", border_style=color)


def _network_panel(snap: dict[str, Any], cfg: Config) -> Any:
    net = snap.get("network") or {}
    if not net.get("available"):
        reason = net.get("error") or "Không đọc được thông tin mạng"
        return Panel(reason, title="Mạng", border_style="yellow")
    lines: list[Any] = []
    ssid = net.get("ssid") or "(không rõ)"
    ip = net.get("ip") or "N/A"
    lines.append(_line("Wi-Fi", utils.truncate(str(ssid), 24), "cyan", width=10))
    lines.append(_line("IP", str(ip), "white", width=10))
    lines.append(_line("Xuống", utils.fmt_rate(net.get("rx_rate")), "green", width=10))
    lines.append(_line("Lên", utils.fmt_rate(net.get("tx_rate")), "yellow", width=10))
    if net.get("rx_session") is not None:
        lines.append(
            _line(
                "Phiên",
                f"↓{utils.fmt_bytes(net.get('rx_session'))} ↑{utils.fmt_bytes(net.get('tx_session'))}",
                "dim",
                width=10,
            )
        )
    speed = net.get("link_speed_mbps")
    if speed:
        lines.append(_line("Link", f"{speed} Mbps", "dim", width=10))
    if net.get("error"):
        lines.append(_line("Ghi chú", str(net["error"]), "dim", width=10))
    return Panel(Group(*lines) if HAS_RICH else "\n".join(lines), title="Mạng", border_style="cyan")


def _storage_panel(snap: dict[str, Any], cfg: Config) -> Any:
    storage = snap.get("storage") or {}
    mounts = storage.get("mounts") or []
    if not mounts:
        return Panel("Không đọc được dung lượng", title="Lưu trữ", border_style="yellow")
    lines: list[Any] = []
    for mount in mounts:
        pct = mount.get("percent")
        color = color_for(pct, 75.0, 90.0)
        label = mount.get("label", "?")
        if HAS_RICH:
            row = Text()
            row.append(f"{label:<7}", style="dim")
            row.append_text(bar_text(pct, 12, color))
            row.append(f" {utils.fmt_pct(pct, 0)}", style=color)
            lines.append(row)
        else:
            lines.append(f"{label:<7}{bar_text(pct, 12)} {utils.fmt_pct(pct, 0)}")
        lines.append(_line("", f"{utils.fmt_bytes(mount.get('used'))} / {utils.fmt_bytes(mount.get('total'))}", "dim", width=7))
    return Panel(Group(*lines) if HAS_RICH else "\n".join(lines), title="Lưu trữ", border_style="magenta")


def _process_content(rows: Sequence[dict[str, Any]], compact: bool) -> Any:
    name_width = 14 if compact else 26
    if not HAS_RICH:
        lines = []
        for row in rows:
            lines.append(
                f"{utils.truncate(str(row.get('name')), name_width)}  pid {row.get('pid')}"
            )
            lines.append(
                f"  CPU {utils.fmt_pct(row.get('cpu'), 1)}  RAM {utils.fmt_bytes(row.get('rss'))}"
            )
        return "\n".join(lines)
    lines: list[Any] = []
    for row in rows:
        name = utils.truncate(str(row.get("name", "")), name_width)
        cpu = row.get("cpu")
        color = color_for(cpu, 25.0, 60.0) if cpu is not None else "dim"
        first = Text()
        first.append(name, style="bold")
        first.append(f"  pid {row.get('pid')}", style="dim")
        second = Text("  CPU ", style="dim")
        second.append(utils.fmt_pct(cpu, 1), style=color)
        second.append("   RAM ", style="dim")
        second.append(utils.fmt_bytes(row.get("rss")), style="cyan")
        lines.extend([first, second])
    return Group(*lines)


def _process_panel(snap: dict[str, Any], cfg: Config, sort_mode: str, compact: bool) -> Any:
    procs = snap.get("processes") or {}
    rows = procs.get("by_mem" if sort_mode == "mem" else "by_cpu") or []
    if not rows:
        return Panel("Không đọc được tiến trình", title="Tiến trình", border_style="yellow")
    title = f"Tiến trình ({'RAM' if sort_mode == 'mem' else 'CPU'})"
    return Panel(_process_content(rows, compact), title=title, border_style="blue")


def _termux_panel(snap: dict[str, Any], cfg: Config) -> Any:
    termux = snap.get("termux") or {}
    lines: list[Any] = []
    count = termux.get("notifications")
    lines.append(_line("Thông báo", "N/A" if count is None else str(count), "cyan", width=14))
    commands = termux.get("commands") or {}
    missing = [name for name, ok in commands.items() if not ok]
    if missing:
        lines.append(_line("Thiếu", ", ".join(missing), "yellow", width=14))
    if termux.get("error"):
        lines.append(_line("Ghi chú", str(termux["error"]), "dim", width=14))
    lines.append(_line("Wake-lock", "bật" if cfg.wake_lock else "tắt", "white", width=14))
    return Panel(Group(*lines) if HAS_RICH else "\n".join(lines), title="Termux", border_style="green")


def _overview_panel(snap: dict[str, Any], cfg: Config) -> Any:
    cpu = (snap.get("cpu") or {}).get("percent")
    mem = (snap.get("memory") or {}).get("percent")
    battery = (snap.get("battery") or {}).get("percent")
    temp = (snap.get("thermal") or {}).get("max_c")
    lines: list[Any] = [
        _line("Thiết bị", utils.truncate(_safe(snap.get("device")), 24), "bold cyan", width=10),
        _line("Android", _safe(snap.get("android")), "white", width=10),
        _line("Uptime", utils.fmt_uptime(snap.get("uptime")), "white", width=10),
    ]
    for label, value, key, reverse in (
        ("CPU", cpu, "cpu", False),
        ("RAM", mem, "ram", False),
        ("Pin", battery, "battery", True),
    ):
        warn, crit = _threshold(cfg, key)
        color = color_for(value, warn, crit, reverse=reverse)
        if HAS_RICH:
            row = Text()
            row.append(f"{label:<10}", style="dim")
            row.append_text(bar_text(value, 12, color))
            row.append(f" {utils.fmt_pct(value, 0)}", style=color)
            lines.append(row)
        else:
            lines.append(f"{label:<10}{bar_text(value, 12)} {utils.fmt_pct(value, 0)}")
    if temp is not None:
        warn, crit = _threshold(cfg, "temp")
        lines.append(_line("Nhiệt độ", f"{temp:.1f} °C", color_for(temp, warn, crit), width=10))
    return Panel(Group(*lines) if HAS_RICH else "\n".join(lines), title="Tổng quan", border_style="cyan")


def _header(snap: dict[str, Any], cfg: Config, paused: bool) -> Any:
    stamp = datetime.now().strftime("%H:%M:%S")
    device = _safe(snap.get("device"), "Termux")
    status = "  PAUSED" if paused else ""
    raw = f" sysdash {utils.VERSION} │ {device} │ {stamp} │ {cfg.interval:.1f}s{status} "
    if HAS_RICH:
        return Text(raw, style="bold white on dark_blue")
    return raw


def _footer(message: str | None = None) -> Any:
    if message:
        raw = f" {message} "
        return Text(raw, style="bold white on dark_green") if HAS_RICH else raw
    raw = " q thoát │ m menu │ , settings │ p pause │ r refresh │ +/- interval │ s sort "
    return Text(raw, style="dim") if HAS_RICH else raw


def render_dashboard(
    snap: dict[str, Any],
    cfg: Config,
    *,
    paused: bool = False,
    sort_mode: str = "cpu",
    cpu_history: Sequence[float] | None = None,
    message: str | None = None,
    width: int | None = None,
) -> Any:
    width = width or (shutil.get_terminal_size((80, 24)).columns)
    compact = width < 60
    panels = [_overview_panel(snap, cfg)]
    if cfg.collectors.get("cpu", True):
        panels.append(_cpu_panel(snap, cfg, cpu_history or [], compact))
    if cfg.collectors.get("memory", True):
        panels.append(_memory_panel(snap, cfg))
    if cfg.collectors.get("battery", True):
        panels.append(_battery_panel(snap, cfg))
    if cfg.collectors.get("thermal", True):
        panels.append(_thermal_panel(snap, cfg))
    if cfg.collectors.get("network", True):
        panels.append(_network_panel(snap, cfg))
    if cfg.collectors.get("storage", True):
        panels.append(_storage_panel(snap, cfg))
    if cfg.collectors.get("processes", True):
        panels.append(_process_panel(snap, cfg, sort_mode, compact))
    if cfg.collectors.get("termux", True):
        panels.append(_termux_panel(snap, cfg))

    if not HAS_RICH:
        parts = [str(_header(snap, cfg, paused)), ""]
        for panel in panels:
            parts.append(str(panel))
            parts.append("")
        parts.append(str(_footer(message)))
        return "\n".join(parts)

    if compact:
        body: Any = Group(*panels)
    else:
        columns = 2 if width < 90 else 3
        grid = Table.grid(expand=True, padding=(0, 1))
        for _ in range(columns):
            grid.add_column(ratio=1)
        for start in range(0, len(panels), columns):
            row = list(panels[start : start + columns])
            while len(row) < columns:
                row.append(Text(""))
            grid.add_row(*row)
        body = grid
    return Group(_header(snap, cfg, paused), body, _footer(message))


def render_once(snap: dict[str, Any], cfg: Config, console: Any = None) -> None:
    console = console or make_console(no_color=not cfg.color, force_terminal=False)
    if console is None:
        print(render_dashboard(snap, cfg))
        return
    console.print(render_dashboard(snap, cfg, width=console.size.width))


def render_help(console: Any = None) -> None:
    text = """
sysdash — bảng điều khiển hệ thống cho Termux

Cách dùng:
  sysdash                     Mở menu
  sysdash dashboard           Mở dashboard
  sysdash once                In snapshot một lần
  sysdash json                Xuất JSON
  sysdash snapshot            Lưu snapshot và rotate
  sysdash settings            Mở cài đặt
  sysdash install             Cài lệnh sysdash
  sysdash repair              Sửa lệnh/cấu hình
  sysdash doctor              Kiểm tra môi trường
  sysdash uninstall           Gỡ cài đặt
  sysdash widget install      Tạo shortcut Termux:Widget
  sysdash widget remove       Xóa shortcut Termux:Widget
  sysdash widget list         Liệt kê shortcut
  sysdash widget doctor       Kiểm tra tích hợp widget

Phím trong dashboard:
  q / Ctrl+C   thoát
  m            menu
  ,            cài đặt
  p            tạm dừng
  r            làm mới
  + / -        đổi interval
  s            đổi kiểu sắp xếp tiến trình
  h / ?        trợ giúp
"""
    if console:
        console.print(text)
    else:
        print(text)


def render_menu(
    title: str,
    items: Sequence[tuple[str, str, str]],
    selected: int,
    console: Any = None,
    description: str = "",
    footer: str = "↑/↓ chọn │ Enter mở │ 1-9 chọn nhanh │ q thoát",
) -> None:
    """Render a menu. items = (key, label, description)."""
    console = console or make_console()
    if console is None:
        print(title)
        for key, label, desc in items:
            print(f"  {key}) {label} — {desc}")
        if description:
            print(description)
        return
    color_system = console.color_system
    if color_system is None:
        # Plain fallback keeps the menu usable when color is disabled.
        console.clear()
        console.print(title, style="bold")
        console.print()
        for idx, (key, label, _desc) in enumerate(items):
            marker = ">" if idx == selected else " "
            style = "reverse" if idx == selected else ""
            console.print(f" {marker} {key}) {label}", style=style)
        console.print()
        if description:
            console.print(description, style="dim")
        console.print(footer, style="dim")
        return
    table = Table.grid(padding=(0, 1))
    table.add_column(width=3, no_wrap=True)
    table.add_column(no_wrap=False)
    for idx, (key, label, _desc) in enumerate(items):
        marker = "❯" if idx == selected else " "
        style = "bold reverse" if idx == selected else ""
        table.add_row(Text(marker, style="cyan"), Text(f"{key}) {label}", style=style))
    body = Group(
        Text(title, style="bold cyan"),
        Text(""),
        table,
        Text(""),
        Text(description, style="white") if description else Text(""),
        Text(footer, style="dim"),
    )
    console.clear()
    console.print(Panel(body, border_style="cyan", padding=(0, 1)))


@contextmanager
def alternate_screen(enabled: bool = True) -> Iterator[None]:
    active = enabled and sys.stdout.isatty()
    if active:
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        sys.stdout.flush()
    try:
        yield
    finally:
        if active:
            sys.stdout.write("\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()


@contextmanager
def raw_mode(enabled: bool = True) -> Iterator[bool]:
    if not enabled or not sys.stdin.isatty():
        yield False
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key(timeout: float | None = None) -> str | None:
    """Read one key. Returns None on timeout, or a symbolic name."""
    if not sys.stdin.isatty():
        return None
    fd = sys.stdin.fileno()
    try:
        ready, _, _ = select.select([fd], [], [], timeout)
    except (OSError, ValueError):
        return None
    if not ready:
        return None
    try:
        first = os.read(fd, 1)
    except OSError:
        return None
    if not first:
        return "ctrl-d"
    if first == b"\x03":
        return "ctrl-c"
    if first == b"\x04":
        return "ctrl-d"
    if first in (b"\r", b"\n"):
        return "enter"
    if first == b"\t":
        return "tab"
    if first == b"\x7f":
        return "backspace"
    if first == b"\x1b":
        try:
            ready, _, _ = select.select([fd], [], [], 0.03)
        except (OSError, ValueError):
            return "esc"
        if not ready:
            return "esc"
        second = os.read(fd, 1)
        if second == b"[":
            third = os.read(fd, 1)
            mapping = {
                b"A": "up",
                b"B": "down",
                b"C": "right",
                b"D": "left",
                b"H": "home",
                b"F": "end",
                b"Z": "shift-tab",
            }
            if third == b"Z":
                return "shift-tab"
            return mapping.get(third, "esc")
        return "esc"
    try:
        return first.decode("utf-8", errors="ignore").lower()
    except Exception:
        return None


def wait_enter(prompt: str = "Nhấn Enter để tiếp tục...") -> None:
    try:
        input(prompt)
    except (EOFError, KeyboardInterrupt):
        pass
