"""Shared helpers for sysdash."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

APP_NAME = "sysdash"
VERSION = "0.1.0"

TERMUX_PREFIX = Path("/data/data/com.termux/files/usr")


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def prefix() -> Path:
    return _env_path("PREFIX", TERMUX_PREFIX)


def install_dir() -> Path:
    return _env_path("SYSDASH_INSTALL_DIR", Path.home() / ".local" / "share" / "sysdash")


def bin_dir() -> Path:
    return _env_path("SYSDASH_BIN_DIR", prefix() / "bin")


def config_path() -> Path:
    return _env_path("SYSDASH_CONFIG", Path.home() / ".config" / "sysdash" / "config.toml")


def state_dir() -> Path:
    return _env_path("SYSDASH_STATE_DIR", Path.home() / ".local" / "state" / "sysdash")


def cache_dir() -> Path:
    return _env_path("SYSDASH_CACHE_DIR", Path.home() / ".cache" / "sysdash")


def shortcuts_dir() -> Path:
    return _env_path("SYSDASH_SHORTCUTS_DIR", Path.home() / ".shortcuts")


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    try:
        path.chmod(mode)
    except OSError:
        pass
    return path


def read_text(path: Path, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def run_cmd(args: Sequence[str], timeout: float = 3.0) -> str | None:
    """Run a command without a shell and return stripped stdout, or None."""
    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def which(name: str) -> str | None:
    return shutil.which(name)


def atomic_write(path: Path, text: str, mode: int = 0o600) -> Path:
    path = Path(path)
    ensure_dir(path.parent)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8", errors="strict")
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    return path


def backup_file(path: Path, backup_dir: Path) -> Path | None:
    path = Path(path)
    if not path.exists():
        return None
    ensure_dir(backup_dir)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{path.name}.bak-{stamp}"
    n = 1
    while target.exists():
        target = backup_dir / f"{path.name}.bak-{stamp}-{n}"
        n += 1
    shutil.copy2(path, target)
    return target


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def fmt_bytes(value: float | int | None, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    value = float(value)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    idx = 0
    while abs(value) >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{value:.0f} {units[idx]}{suffix}"
    return f"{value:.1f} {units[idx]}{suffix}"


def fmt_rate(value: float | None) -> str:
    if value is None:
        return "N/A"
    return fmt_bytes(value, "/s")


def fmt_pct(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}%"


def fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def normalize_temp(value: float | None) -> float | None:
    """Normalize common Android temperature units to Celsius."""
    if value is None:
        return None
    amount = abs(value)
    if amount >= 1000:
        value = value / 1000.0
    elif amount >= 200:
        value = value / 10.0
    if value < -30 or value > 200:
        return None
    return round(value, 1)


def truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return "…"
    return text[: width - 1] + "…"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_kv(text: str) -> dict[str, int]:
    """Parse /proc/meminfo style 'Key: 123 kB' into a dict of kB ints."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        parts = raw.strip().split()
        if not parts:
            continue
        try:
            out[key.strip()] = int(parts[0])
        except ValueError:
            continue
    return out


def read_json_command(args: Sequence[str], timeout: float = 3.0) -> dict | None:
    raw = run_cmd(args, timeout=timeout)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def iter_pids() -> Iterable[int]:
    try:
        entries = os.scandir("/proc")
    except OSError:
        return []
    pids: list[int] = []
    with entries:
        for entry in entries:
            if entry.name.isdigit():
                pids.append(int(entry.name))
    return pids


def human_status(status: str | None) -> str:
    mapping = {
        "CHARGING": "Đang sạc",
        "DISCHARGING": "Đang xả",
        "FULL": "Đầy",
        "NOT_CHARGING": "Không sạc",
        "UNKNOWN": "Không rõ",
    }
    return mapping.get((status or "").upper(), status or "N/A")
