"""Configuration loading and saving for sysdash."""
from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import utils

DEFAULT_THRESHOLDS: dict[str, float] = {
    "cpu_warn": 60.0,
    "cpu_crit": 85.0,
    "ram_warn": 60.0,
    "ram_crit": 85.0,
    "battery_warn": 30.0,
    "battery_crit": 15.0,
    "temp_warn": 45.0,
    "temp_crit": 60.0,
}

DEFAULT_COLLECTORS: dict[str, bool] = {
    "cpu": True,
    "memory": True,
    "battery": True,
    "thermal": True,
    "network": True,
    "storage": True,
    "processes": True,
    "termux": True,
}


@dataclass
class Config:
    interval: float = 1.0
    top_n: int = 5
    wake_lock: bool = False
    color: bool = True
    lang: str = "vi"
    notify: bool = False
    snapshot_keep: int = 20
    thresholds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    collectors: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_COLLECTORS))

    def validate(self) -> "Config":
        self.interval = min(10.0, max(0.5, float(self.interval)))
        self.top_n = min(50, max(1, int(self.top_n)))
        self.snapshot_keep = min(500, max(1, int(self.snapshot_keep)))
        self.lang = self.lang if self.lang in {"vi", "en"} else "vi"
        merged_thresholds = dict(DEFAULT_THRESHOLDS)
        merged_thresholds.update(
            {k: float(v) for k, v in (self.thresholds or {}).items() if k in DEFAULT_THRESHOLDS}
        )
        self.thresholds = merged_thresholds
        merged_collectors = dict(DEFAULT_COLLECTORS)
        merged_collectors.update(
            {k: bool(v) for k, v in (self.collectors or {}).items() if k in DEFAULT_COLLECTORS}
        )
        self.collectors = merged_collectors
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "interval": self.interval,
            "top_n": self.top_n,
            "wake_lock": self.wake_lock,
            "color": self.color,
            "lang": self.lang,
            "notify": self.notify,
            "snapshot_keep": self.snapshot_keep,
            "thresholds": dict(self.thresholds),
            "collectors": dict(self.collectors),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        cfg = cls(
            interval=float(data.get("interval", 1.0)),
            top_n=int(data.get("top_n", 5)),
            wake_lock=bool(data.get("wake_lock", False)),
            color=bool(data.get("color", True)),
            lang=str(data.get("lang", "vi")),
            notify=bool(data.get("notify", False)),
            snapshot_keep=int(data.get("snapshot_keep", 20)),
            thresholds=dict(data.get("thresholds") or {}),
            collectors=dict(data.get("collectors") or {}),
        )
        return cfg.validate()


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_toml(cfg: Config) -> str:
    lines = [
        f"interval = {_toml_scalar(float(cfg.interval))}",
        f"top_n = {_toml_scalar(int(cfg.top_n))}",
        f"wake_lock = {_toml_scalar(bool(cfg.wake_lock))}",
        f"color = {_toml_scalar(bool(cfg.color))}",
        f"lang = {_toml_scalar(cfg.lang)}",
        f"notify = {_toml_scalar(bool(cfg.notify))}",
        f"snapshot_keep = {_toml_scalar(int(cfg.snapshot_keep))}",
        "",
        "[thresholds]",
    ]
    for key in DEFAULT_THRESHOLDS:
        lines.append(f"{key} = {_toml_scalar(float(cfg.thresholds[key]))}")
    lines += ["", "[collectors]"]
    for key in DEFAULT_COLLECTORS:
        lines.append(f"{key} = {_toml_scalar(bool(cfg.collectors[key]))}")
    lines.append("")
    return "\n".join(lines)


def load(path: Path | None = None) -> Config:
    path = Path(path) if path else utils.config_path()
    if not path.exists():
        return Config().validate()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return Config().validate()
    if not isinstance(data, dict):
        return Config().validate()
    try:
        return Config.from_dict(data)
    except (TypeError, ValueError):
        return Config().validate()


def save(cfg: Config, path: Path | None = None) -> Path:
    path = Path(path) if path else utils.config_path()
    cfg.validate()
    if path.exists():
        utils.backup_file(path, utils.state_dir() / "backups" / "config")
    return utils.atomic_write(path, dump_toml(cfg), mode=0o600)


def reset() -> Config:
    cfg = Config().validate()
    save(cfg)
    return cfg


def copy_config(cfg: Config) -> Config:
    return Config.from_dict(copy.deepcopy(cfg.to_dict()))


def color_for(value: float | None, warn: float, crit: float, reverse: bool = False) -> str:
    """Return a rich color name for a value and thresholds."""
    if value is None:
        return "dim"
    if reverse:
        if value <= crit:
            return "red"
        if value <= warn:
            return "yellow"
        return "green"
    if value >= crit:
        return "red"
    if value >= warn:
        return "yellow"
    return "green"
