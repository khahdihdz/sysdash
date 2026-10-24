"""Dashboard loop and snapshot persistence."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from . import ui, utils
from .collectors import Sampler
from .config import Config

try:
    from rich.live import Live
except ImportError:  # pragma: no cover
    Live = None  # type: ignore[assignment]


HELP_MESSAGE = (
    "Phím: q thoát │ m menu │ , settings │ p pause │ r refresh │ "
    "+/- interval │ s sort │ h trợ giúp"
)


def _log_error(message: str) -> None:
    try:
        utils.ensure_dir(utils.state_dir())
        with (utils.state_dir() / "sysdash.log").open("a", encoding="utf-8") as handle:
            stamp = datetime.now().isoformat(timespec="seconds")
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def _wake(enable: bool) -> None:
    if not enable:
        return
    if shutil.which("termux-wake-lock"):
        utils.run_cmd(["termux-wake-lock"], timeout=3.0)


def _unwake(enable: bool) -> None:
    if not enable:
        return
    if shutil.which("termux-wake-unlock"):
        utils.run_cmd(["termux-wake-unlock"], timeout=3.0)


def _take_snapshot(sampler: Sampler) -> dict[str, Any]:
    try:
        return sampler.snapshot()
    except Exception as exc:  # keep the dashboard alive
        _log_error(f"snapshot failed: {type(exc).__name__}: {exc}")
        return {
            "timestamp": utils.now_iso(),
            "version": utils.VERSION,
            "device": "lỗi thu thập",
            "android": "N/A",
            "kernel": "N/A",
            "uptime": None,
        }


def run_dashboard(cfg: Config) -> str:
    """Run the interactive dashboard. Returns 'quit', 'menu' or 'settings'."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print_snapshot(cfg)
        return "quit"

    console = ui.make_console(no_color=not cfg.color, force_terminal=True)
    sampler = Sampler(cfg)
    history: deque[float] = deque(maxlen=60)
    snap = _take_snapshot(sampler)
    if (snap.get("cpu") or {}).get("percent") is not None:
        history.append(float(snap["cpu"]["percent"]))

    if console is None or Live is None:
        return _run_plain_dashboard(cfg, sampler)

    paused = False
    sort_mode = "cpu"
    message: str | None = None
    last = 0.0
    wake = bool(cfg.wake_lock)
    _wake(wake)
    try:
        with ui.raw_mode(True):
            with Live(
                ui.render_dashboard(
                    snap,
                    cfg,
                    paused=paused,
                    sort_mode=sort_mode,
                    cpu_history=list(history),
                    message=message,
                    width=console.size.width,
                ),
                console=console,
                screen=True,
                auto_refresh=False,
                transient=False,
            ) as live:
                live.refresh()
                while True:
                    now = time.monotonic()
                    if not paused and (now - last >= cfg.interval):
                        snap = _take_snapshot(sampler)
                        pct = (snap.get("cpu") or {}).get("percent")
                        if pct is not None:
                            history.append(float(pct))
                        last = now
                        live.update(
                            ui.render_dashboard(
                                snap,
                                cfg,
                                paused=paused,
                                sort_mode=sort_mode,
                                cpu_history=list(history),
                                message=message,
                                width=console.size.width,
                            ),
                            refresh=True,
                        )
                    key = ui.read_key(0.1)
                    if key is None:
                        continue
                    if key in {"q", "ctrl-c", "ctrl-d"}:
                        return "quit"
                    if key == "m":
                        return "menu"
                    if key == ",":
                        return "settings"
                    if key == "p":
                        paused = not paused
                        message = "Đã tạm dừng" if paused else None
                    elif key == "r":
                        last = 0.0
                        message = "Đã làm mới"
                    elif key in {"+", "="}:
                        cfg.interval = min(10.0, round(cfg.interval + 0.5, 1))
                        message = f"Interval {cfg.interval:.1f}s"
                    elif key == "-":
                        cfg.interval = max(0.5, round(cfg.interval - 0.5, 1))
                        message = f"Interval {cfg.interval:.1f}s"
                    elif key == "s":
                        sort_mode = "mem" if sort_mode == "cpu" else "cpu"
                        message = f"Sắp xếp theo {'RAM' if sort_mode == 'mem' else 'CPU'}"
                    elif key in {"h", "?"}:
                        message = HELP_MESSAGE
                    live.update(
                        ui.render_dashboard(
                            snap,
                            cfg,
                            paused=paused,
                            sort_mode=sort_mode,
                            cpu_history=list(history),
                            message=message,
                            width=console.size.width,
                        ),
                        refresh=True,
                    )
    except KeyboardInterrupt:
        return "quit"
    finally:
        _unwake(wake)


def _run_plain_dashboard(cfg: Config, sampler: Sampler) -> str:
    """Minimal fallback when rich is not installed."""
    try:
        while True:
            snap = _take_snapshot(sampler)
            print("\x1b[H\x1b[2J", end="")
            print(ui.render_dashboard(snap, cfg, width=shutil.get_terminal_size((80, 24)).columns))
            time.sleep(cfg.interval)
    except KeyboardInterrupt:
        return "quit"


def snapshot_json(cfg: Config) -> str:
    sampler = Sampler(cfg)
    return json.dumps(_take_snapshot(sampler), ensure_ascii=False, indent=2)


def print_snapshot(cfg: Config) -> None:
    console = ui.make_console(no_color=not cfg.color, force_terminal=sys.stdout.isatty())
    sampler = Sampler(cfg)
    ui.render_once(_take_snapshot(sampler), cfg, console=console)


def snapshot_dir() -> Path:
    return utils.state_dir() / "snapshots"


def save_snapshot(
    cfg: Config,
    *,
    keep: int | None = None,
    notify: bool | None = None,
    out_dir: Path | None = None,
) -> Path:
    keep = cfg.snapshot_keep if keep is None else max(1, int(keep))
    notify = cfg.notify if notify is None else bool(notify)
    target_dir = Path(out_dir) if out_dir else snapshot_dir()
    utils.ensure_dir(target_dir)
    sampler = Sampler(cfg)
    data = _take_snapshot(sampler)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = target_dir / f"snapshot-{stamp}.json"
    utils.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=0o600)
    _rotate(target_dir, keep)
    if notify:
        _notify(path, data)
    return path


def _rotate(target_dir: Path, keep: int) -> None:
    try:
        files = sorted(
            target_dir.glob("snapshot-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def _notify(path: Path, data: dict[str, Any]) -> None:
    if not shutil.which("termux-notification"):
        return
    cpu = (data.get("cpu") or {}).get("percent")
    mem = (data.get("memory") or {}).get("percent")
    battery = (data.get("battery") or {}).get("percent")
    content = (
        f"CPU {utils.fmt_pct(cpu)} │ RAM {utils.fmt_pct(mem)} │ Pin {utils.fmt_pct(battery)}\n"
        f"{path}"
    )
    try:
        utils.run_cmd(
            [
                "termux-notification",
                "--id",
                "sysdash-snapshot",
                "--title",
                "Sysdash snapshot",
                "--content",
                content,
            ],
            timeout=4.0,
        )
    except Exception:
        pass
