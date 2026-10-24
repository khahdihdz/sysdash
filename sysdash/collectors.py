"""Data collectors for sysdash.

Everything is best-effort: a missing file, permission error or missing
Termux API command must never crash the dashboard.
"""
from __future__ import annotations

import glob
import json
import os
import platform
import shutil
import socket
import time
from pathlib import Path
from typing import Any, Callable

from . import utils
from .config import Config


def _read(path: str | Path) -> str:
    return utils.read_text(Path(path), "")


def _read_int(path: str | Path) -> int | None:
    raw = _read(path).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        try:
            return int(float(raw))
        except ValueError:
            return None


def _read_float(path: str | Path) -> float | None:
    raw = _read(path).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class Sampler:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._cache: dict[str, tuple[float, Any]] = {}
        self._prev_cpu: dict[str, tuple[int, int]] = {}
        self._prev_proc_cpu: tuple[float, int] | None = None
        self._prev_net: tuple[float, int, int] | None = None
        self._net_base: tuple[int, int] | None = None
        self._prev_proc: dict[int, int] = {}
        self._prev_proc_time: float | None = None
        self._device_info: dict[str, str] | None = None

    # ------------------------------------------------------------------ cache
    def _cached(self, key: str, ttl: float, fn: Callable[[], Any]) -> Any:
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
        value = fn()
        self._cache[key] = (now, value)
        return value

    # ------------------------------------------------------------------- cpu
    def _cpu_times(self) -> dict[str, tuple[int, int]]:
        result: dict[str, tuple[int, int]] = {}
        for line in _read("/proc/stat").splitlines():
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            try:
                values = [int(v) for v in parts[1:9]]
            except ValueError:
                continue
            if len(values) < 4:
                continue
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values)
            result[name] = (total - idle, total)
        return result

    def _proc_cpu_total(self) -> tuple[float, int]:
        total = 0
        for pid in utils.iter_pids():
            stat = _read(f"/proc/{pid}/stat")
            if not stat or ")" not in stat:
                continue
            rest = stat[stat.rfind(")") + 1 :].split()
            if len(rest) < 13:
                continue
            try:
                total += int(rest[11]) + int(rest[12])
            except ValueError:
                continue
        return time.monotonic(), total

    def _cpu_freq_mhz(self) -> float | None:
        freqs: list[float] = []
        for path in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq"):
            value = _read_int(path)
            if value and value > 0:
                freqs.append(value / 1000.0)
        return round(sum(freqs) / len(freqs), 1) if freqs else None

    def _cpu_from_processes(self) -> float | None:
        """Estimate CPU busy time when /proc/stat is blocked by Android.

        Sums utime+stime of every visible process and normalizes by elapsed
        time and the number of CPUs. It is an estimate, not a kernel counter,
        but works on Android 15 where /proc/stat is often unreadable.
        """
        now, total = self._proc_cpu_total()
        prev = self._prev_proc_cpu
        self._prev_proc_cpu = (now, total)
        if prev is None:
            time.sleep(0.12)
            now2, total2 = self._proc_cpu_total()
            self._prev_proc_cpu = (now2, total2)
            elapsed = now2 - now
            delta = total2 - total
        else:
            elapsed = now - prev[0]
            delta = total - prev[1]
        if elapsed <= 0 or delta < 0:
            return None
        try:
            ticks = os.sysconf("SC_CLK_TCK")
        except (ValueError, OSError):
            ticks = 100
        cpus = os.cpu_count() or 1
        return utils.clamp(delta * 100.0 / (elapsed * ticks * cpus))

    def collect_cpu(self) -> dict[str, Any]:
        times = self._cpu_times()
        if not times:
            pct = self._cpu_from_processes()
            return {
                "percent": None if pct is None else round(pct, 1),
                "cores": [],
                "freq_mhz": self._cpu_freq_mhz(),
                "load": [],
                "source": "process-sum",
            }
        if not self._prev_cpu:
            # First sample has no delta. Take a short second sample so `--once`
            # still shows a meaningful percentage.
            self._prev_cpu = times
            time.sleep(0.12)
            times = self._cpu_times()
        prev = self._prev_cpu
        cores: list[dict[str, Any]] = []
        total_pct: float | None = None
        for name, (busy, total) in times.items():
            old = prev.get(name)
            pct = None
            if old and total > old[1]:
                pct = utils.clamp((busy - old[0]) * 100.0 / (total - old[1]))
            if name == "cpu":
                total_pct = pct
            else:
                try:
                    idx = int(name[3:])
                except ValueError:
                    idx = len(cores)
                cores.append({"id": idx, "percent": pct})
        self._prev_cpu = times
        cores.sort(key=lambda item: item["id"])

        load: list[float] = []
        raw_load = _read("/proc/loadavg").split()
        for item in raw_load[:3]:
            try:
                load.append(float(item))
            except ValueError:
                pass

        return {
            "percent": None if total_pct is None else round(total_pct, 1),
            "cores": cores,
            "freq_mhz": self._cpu_freq_mhz(),
            "load": load,
            "source": "proc-stat",
        }

    # --------------------------------------------------------------- memory
    def collect_memory(self) -> dict[str, Any]:
        info = utils.parse_kv(_read("/proc/meminfo"))
        if not info:
            return {"available": False}

        def kib(key: str) -> int:
            return int(info.get(key, 0)) * 1024

        total = kib("MemTotal")
        available = kib("MemAvailable") or kib("MemFree")
        used = max(0, total - available)
        swap_total = kib("SwapTotal")
        swap_free = kib("SwapFree")
        return {
            "available": bool(total),
            "total": total,
            "available_bytes": available,
            "used": used,
            "free": kib("MemFree"),
            "cached": kib("Cached"),
            "buffers": kib("Buffers"),
            "percent": round(used * 100.0 / total, 1) if total else None,
            "swap_total": swap_total,
            "swap_used": max(0, swap_total - swap_free),
            "swap_percent": round((swap_total - swap_free) * 100.0 / swap_total, 1)
            if swap_total
            else None,
        }

    # -------------------------------------------------------------- battery
    def _battery_termux(self) -> dict[str, Any] | None:
        if not shutil.which("termux-battery-status"):
            return None
        data = utils.read_json_command(["termux-battery-status"], timeout=4.0)
        if not data:
            return None
        temp = data.get("temperature")
        try:
            temp_f = float(temp) if temp is not None else None
        except (TypeError, ValueError):
            temp_f = None
        voltage = data.get("voltage")
        current = data.get("current")
        try:
            voltage_f = float(voltage) if voltage is not None else None
        except (TypeError, ValueError):
            voltage_f = None
        try:
            current_f = float(current) if current is not None else None
        except (TypeError, ValueError):
            current_f = None
        if current_f is not None and abs(current_f) >= 1000:
            # Some Termux API versions report microamps, others milliamps.
            current_f = current_f / 1000.0
        return {
            "available": True,
            "source": "termux-api",
            "percent": _as_float(data.get("percentage")),
            "status": str(data.get("status", "UNKNOWN")),
            "temp_c": utils.normalize_temp(temp_f),
            "voltage_v": (voltage_f / 1000.0) if voltage_f and voltage_f > 100 else voltage_f,
            "current_ma": current_f,
        }

    def _battery_sysfs(self) -> dict[str, Any] | None:
        candidates = [
            Path("/sys/class/power_supply/battery"),
            Path("/sys/class/power_supply/Battery"),
        ]
        candidates += [
            Path(p).parent for p in glob.glob("/sys/class/power_supply/*/type")
            if _read(p).strip().lower() == "battery"
        ]
        for base in candidates:
            capacity = _read_int(base / "capacity")
            if capacity is None:
                continue
            temp = _read_float(base / "temp")
            voltage = _read_float(base / "voltage_now")
            current = _read_float(base / "current_now")
            charge_full = _read_float(base / "charge_full")
            return {
                "available": True,
                "source": "sysfs",
                "percent": float(capacity),
                "status": (_read(base / "status").strip() or "UNKNOWN"),
                "temp_c": utils.normalize_temp(temp),
                "voltage_v": (voltage / 1_000_000.0) if voltage and voltage > 1000 else voltage,
                "current_ma": (current / 1000.0) if current and abs(current) > 100 else current,
                "charge_full_mah": (charge_full / 1000.0) if charge_full else None,
            }
        return None

    def collect_battery(self) -> dict[str, Any]:
        data = self._battery_termux() or self._battery_sysfs()
        if not data:
            return {
                "available": False,
                "error": "Không đọc được pin (termux-api/sysfs)",
            }
        if data.get("percent") is None:
            return {
                "available": False,
                "error": "Thiếu dữ liệu phần trăm pin",
            }
        return data

    # -------------------------------------------------------------- thermal
    def collect_thermal(self) -> dict[str, Any]:
        zones: list[dict[str, Any]] = []
        for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
            name = _read(Path(zone) / "type").strip() or Path(zone).name
            raw = _read_float(Path(zone) / "temp")
            temp = utils.normalize_temp(raw)
            if temp is None:
                continue
            zones.append({"name": name, "temp_c": temp})
        zones.sort(key=lambda item: item["temp_c"], reverse=True)
        return {
            "available": bool(zones),
            "zones": zones[:8],
            "max_c": zones[0]["temp_c"] if zones else None,
            "error": None if zones else "Không đọc được thermal zone",
        }

    # -------------------------------------------------------------- network
    def _wifi_info(self) -> dict[str, Any]:
        data = None
        if shutil.which("termux-wifi-connectioninfo"):
            data = utils.read_json_command(["termux-wifi-connectioninfo"], timeout=4.0)
        if data:
            return {
                "ssid": data.get("ssid") or data.get("SSID"),
                "ip": data.get("ip") or data.get("ip_address"),
                "link_speed_mbps": data.get("link_speed_mbps") or data.get("link_speed"),
            }
        ip = None
        raw = utils.run_cmd(["ip", "-4", "addr"], timeout=3.0)
        if raw:
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("inet ") and not line.startswith("inet 127."):
                    ip = line.split()[1].split("/")[0]
                    break
        return {"ssid": None, "ip": ip, "link_speed_mbps": None}

    def collect_network(self) -> dict[str, Any]:
        wifi = self._cached("wifi", 15.0, self._wifi_info)
        raw = _read("/proc/net/dev")
        rx = tx = 0
        counters = bool(raw)
        error = None
        if raw:
            for line in raw.splitlines()[2:]:
                if ":" not in line:
                    continue
                iface, rest = line.split(":", 1)
                iface = iface.strip()
                if iface == "lo":
                    continue
                parts = rest.split()
                if len(parts) < 9:
                    continue
                try:
                    rx += int(parts[0])
                    tx += int(parts[8])
                except ValueError:
                    continue
        else:
            error = "Android hạn chế /proc/net/dev; chỉ hiển thị thông tin Wi-Fi"
        now = time.monotonic()
        rx_rate = tx_rate = None
        if counters and self._prev_net and now > self._prev_net[0]:
            elapsed = now - self._prev_net[0]
            rx_rate = max(0.0, (rx - self._prev_net[1]) / elapsed)
            tx_rate = max(0.0, (tx - self._prev_net[2]) / elapsed)
        if counters:
            self._prev_net = (now, rx, tx)
            if self._net_base is None:
                self._net_base = (rx, tx)
        return {
            "available": counters or bool(wifi.get("ip") or wifi.get("ssid")),
            "error": error,
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
            "rx_total": rx if counters else None,
            "tx_total": tx if counters else None,
            "rx_session": max(0, rx - self._net_base[0]) if counters and self._net_base else None,
            "tx_session": max(0, tx - self._net_base[1]) if counters and self._net_base else None,
            "ssid": wifi.get("ssid"),
            "ip": wifi.get("ip"),
            "link_speed_mbps": wifi.get("link_speed_mbps"),
        }

    # -------------------------------------------------------------- storage
    def collect_storage(self) -> dict[str, Any]:
        result: dict[str, Any] = {"available": True, "mounts": []}
        targets = [("HOME", Path.home()), ("PREFIX", utils.prefix())]
        seen_devices: set[int] = set()
        for label, path in targets:
            try:
                st = os.stat(path)
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            if st.st_dev in seen_devices:
                continue
            seen_devices.add(st.st_dev)
            result["mounts"].append(
                {
                    "label": label,
                    "path": str(path),
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": round(usage.used * 100.0 / usage.total, 1) if usage.total else None,
                }
            )
        if not result["mounts"]:
            result["available"] = False
        return result

    # ------------------------------------------------------------ processes
    @staticmethod
    def _proc_name(pid: int, stat: str) -> str:
        raw = _read(f"/proc/{pid}/cmdline")
        if raw:
            name = raw.replace("\x00", " ").strip()
            if name:
                return name
        if stat.startswith("("):
            end = stat.rfind(")")
            if end > 1:
                return stat[1:end]
        return f"pid {pid}"

    def _scan_processes(
        self, elapsed: float, clock_ticks: int, page_size: int
    ) -> tuple[list[dict[str, Any]], dict[int, int]]:
        rows: list[dict[str, Any]] = []
        current: dict[int, int] = {}
        for pid in utils.iter_pids():
            stat = _read(f"/proc/{pid}/stat")
            if not stat:
                continue
            rest = stat[stat.rfind(")") + 1 :].split() if ")" in stat else []
            if len(rest) < 13:
                continue
            try:
                ticks = int(rest[11]) + int(rest[12])
            except ValueError:
                continue
            current[pid] = ticks
            statm = _read(f"/proc/{pid}/statm").split()
            rss = 0
            if len(statm) > 1:
                try:
                    rss = int(statm[1]) * page_size
                except ValueError:
                    rss = 0
            cpu_pct = None
            old = self._prev_proc.get(pid)
            if old is not None and elapsed > 0 and ticks >= old:
                cpu_pct = (ticks - old) * 100.0 / (elapsed * clock_ticks)
            name = self._proc_name(pid, stat)
            rows.append(
                {
                    "pid": pid,
                    "name": name,
                    "cpu": None if cpu_pct is None else round(min(cpu_pct, 999.0), 1),
                    "rss": rss,
                }
            )
        return rows, current

    def collect_processes(self) -> dict[str, Any]:
        try:
            clock_ticks = os.sysconf("SC_CLK_TCK")
        except (ValueError, OSError):
            clock_ticks = 100
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError):
            page_size = 4096
        if self._prev_proc_time is None:
            # Prime process counters so `--once` has non-zero CPU values.
            _rows, current = self._scan_processes(0.0, clock_ticks, page_size)
            self._prev_proc = current
            self._prev_proc_time = time.monotonic()
            time.sleep(0.15)
            elapsed = time.monotonic() - self._prev_proc_time
            rows, current = self._scan_processes(elapsed, clock_ticks, page_size)
        else:
            elapsed = time.monotonic() - self._prev_proc_time
            rows, current = self._scan_processes(elapsed, clock_ticks, page_size)
        self._prev_proc = current
        self._prev_proc_time = time.monotonic()
        by_cpu = sorted(rows, key=lambda r: (r["cpu"] or 0.0), reverse=True)[: self.cfg.top_n]
        by_mem = sorted(rows, key=lambda r: r["rss"], reverse=True)[: self.cfg.top_n]
        return {"total": len(rows), "by_cpu": by_cpu, "by_mem": by_mem}

    # ---------------------------------------------------------------- termux
    def collect_termux(self) -> dict[str, Any]:
        commands = {
            "battery": bool(shutil.which("termux-battery-status")),
            "wifi": bool(shutil.which("termux-wifi-connectioninfo")),
            "notification": bool(shutil.which("termux-notification-list")),
            "wake_lock": bool(shutil.which("termux-wake-lock")),
        }
        count = None
        error = None
        if commands["notification"]:
            raw = utils.run_cmd(["termux-notification-list"], timeout=1.5)
            if raw is not None:
                try:
                    data = json.loads(raw)
                    count = len(data) if isinstance(data, list) else None
                except json.JSONDecodeError:
                    error = "JSON notification không hợp lệ"
            else:
                error = "termux-notification-list lỗi"
        else:
            error = "termux-api chưa cài"
        return {
            "api": any(commands.values()),
            "commands": commands,
            "notifications": count,
            "error": error,
        }

    # ---------------------------------------------------------------- device
    def _system_info(self) -> dict[str, str]:
        if self._device_info is not None:
            return self._device_info
        model = utils.run_cmd(["getprop", "ro.product.model"], timeout=2.0)
        android = utils.run_cmd(["getprop", "ro.build.version.release"], timeout=2.0)
        self._device_info = {
            "device": model or socket.gethostname(),
            "android": android or "N/A",
            "kernel": platform.release(),
        }
        return self._device_info

    def _uptime(self) -> float | None:
        raw = _read("/proc/uptime").split()
        if raw:
            try:
                return float(raw[0])
            except ValueError:
                pass
        try:
            return time.clock_gettime(time.CLOCK_BOOTTIME)
        except (AttributeError, OSError):
            return None

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> dict[str, Any]:
        cfg = self.cfg
        data: dict[str, Any] = {
            "timestamp": utils.now_iso(),
            "version": utils.VERSION,
            "uptime": self._uptime(),
            **self._system_info(),
        }
        if cfg.collectors.get("cpu", True):
            data["cpu"] = self._cached("cpu", max(0.2, cfg.interval * 0.5), self.collect_cpu)
        if cfg.collectors.get("memory", True):
            data["memory"] = self._cached("memory", max(1.0, cfg.interval), self.collect_memory)
        if cfg.collectors.get("battery", True):
            data["battery"] = self._cached("battery", 5.0, self.collect_battery)
        if cfg.collectors.get("thermal", True):
            data["thermal"] = self._cached("thermal", 5.0, self.collect_thermal)
        if cfg.collectors.get("network", True):
            data["network"] = self._cached("network", max(1.0, cfg.interval), self.collect_network)
        if cfg.collectors.get("storage", True):
            data["storage"] = self._cached("storage", 10.0, self.collect_storage)
        if cfg.collectors.get("processes", True):
            data["processes"] = self._cached("processes", 2.0, self.collect_processes)
        if cfg.collectors.get("termux", True):
            data["termux"] = self._cached("termux", 60.0, self.collect_termux)
        return data


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
