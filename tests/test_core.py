from __future__ import annotations

from pathlib import Path

from sysdash import config, utils, widget


def test_normalize_temp():
    assert utils.normalize_temp(45000) == 45.0
    assert utils.normalize_temp(450) == 45.0
    assert utils.normalize_temp(45.5) == 45.5
    assert utils.normalize_temp(999999) is None


def test_format_helpers():
    assert utils.fmt_bytes(1024) == "1.0 KiB"
    assert utils.fmt_bytes(None) == "N/A"
    assert utils.fmt_pct(50.4, 0) == "50%"
    assert utils.fmt_uptime(3661) == "01:01:01"


def test_parse_meminfo():
    data = utils.parse_kv("MemTotal: 100 kB\nMemFree: 20 kB\n")
    assert data == {"MemTotal": 100, "MemFree": 20}


def test_config_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SYSDASH_STATE_DIR", str(tmp_path / "state"))
    path = tmp_path / "config.toml"
    cfg = config.Config(interval=2.5, top_n=7, lang="en")
    cfg.thresholds["cpu_warn"] = 55
    cfg.collectors["network"] = False
    config.save(cfg, path)
    loaded = config.load(path)
    assert loaded.interval == 2.5
    assert loaded.top_n == 7
    assert loaded.lang == "en"
    assert loaded.thresholds["cpu_warn"] == 55
    assert loaded.collectors["network"] is False
    # Unknown/invalid values fall back to safe defaults.
    path.write_text('interval = 99\ntop_n = 0\nlang = "zz"\n', encoding="utf-8")
    loaded = config.load(path)
    assert loaded.interval == 10.0
    assert loaded.top_n == 1
    assert loaded.lang == "vi"


def test_widget_name_validation():
    assert widget.validate_name("System Dashboard") == "System Dashboard"
    for bad in ("", ".", "..", ".hidden", "a/b", "a:b", "x" * 65):
        try:
            widget.validate_name(bad)
        except widget.WidgetError:
            pass
        else:
            raise AssertionError(f"expected rejection for {bad!r}")


def test_color_thresholds():
    assert config.color_for(10, 60, 85) == "green"
    assert config.color_for(70, 60, 85) == "yellow"
    assert config.color_for(90, 60, 85) == "red"
    assert config.color_for(10, 30, 15, reverse=True) == "red"
    assert config.color_for(50, 30, 15, reverse=True) == "green"
