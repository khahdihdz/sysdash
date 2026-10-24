from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from sysdash import installer, utils, widget


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PREFIX", str(prefix))
    monkeypatch.setenv("SYSDASH_INSTALL_DIR", str(home / ".local" / "share" / "sysdash"))
    monkeypatch.setenv("SYSDASH_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("SYSDASH_CONFIG", str(tmp_path / "config" / "config.toml"))
    monkeypatch.setenv("SYSDASH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SYSDASH_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SYSDASH_SHORTCUTS_DIR", str(home / ".shortcuts"))
    return tmp_path


def make_source(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    (src / "sysdash").mkdir(parents=True)
    (src / "sysdash" / "__init__.py").write_text("VERSION = 'test'\n", encoding="utf-8")
    (src / "README.md").write_text("test\n", encoding="utf-8")
    return src


def test_install_and_uninstall(env, monkeypatch):
    monkeypatch.setattr(widget, "app_installed", lambda: True)
    src = make_source(env)
    report = installer.install(source=src, with_widget=True, tasks=True)
    dest = utils.install_dir()
    wrapper = installer.wrapper_path()
    assert report["copy"] == "installed"
    assert wrapper.exists()
    assert stat.S_IMODE(wrapper.stat().st_mode) == 0o700
    assert (dest / "sysdash" / "__init__.py").exists()
    assert (dest / ".sysdash-installed").exists()
    assert utils.config_path().exists()

    script = utils.shortcuts_dir() / "System Dashboard"
    task = utils.shortcuts_dir() / "tasks" / "Sysdash Snapshot"
    assert script.exists() and task.exists()
    assert stat.S_IMODE(script.stat().st_mode) == 0o700
    assert widget.MARKER in script.read_text(encoding="utf-8")
    assert "dashboard" in script.read_text(encoding="utf-8")
    assert "snapshot --keep 20" in task.read_text(encoding="utf-8")

    report = installer.uninstall(keep_config=True)
    assert report["wrapper"] == "removed"
    assert not wrapper.exists()
    assert not script.exists()
    assert not task.exists()
    assert utils.config_path().exists()


def test_uninstall_purge_and_source(env, monkeypatch):
    monkeypatch.setattr(widget, "app_installed", lambda: True)
    src = make_source(env)
    installer.install(source=src)
    dest = utils.install_dir()
    assert dest.exists()
    report = installer.uninstall(purge=True, purge_source=True)
    assert report["source"] == "removed"
    assert not dest.exists()
    assert not utils.config_path().exists()
    assert not utils.state_dir().exists()


def test_uninstall_keeps_foreign_wrapper(env):
    wrapper = installer.wrapper_path()
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
    report = installer.uninstall(keep_config=True)
    assert report["wrapper"] == "kept"
    assert wrapper.exists()
    assert report["warnings"]


def test_copy_source_refuses_foreign_dest(env):
    src = make_source(env)
    dest = utils.install_dir()
    dest.mkdir(parents=True)
    (dest / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(installer.InstallerError):
        installer.copy_source(src, dest)
    assert (dest / "keep.txt").exists()


def test_dry_run_changes_nothing(env, monkeypatch):
    monkeypatch.setattr(widget, "app_installed", lambda: True)
    src = make_source(env)
    report = installer.install(source=src, with_widget=True, dry_run=True)
    assert report["dry_run"] is True
    assert not utils.install_dir().exists()
    assert not installer.wrapper_path().exists()
    assert not utils.shortcuts_dir().exists()
    assert not utils.config_path().exists()


def test_widget_install_remove(env):
    bin_path = env / "bin" / "sysdash"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(bin_path, 0o700)
    report = widget.install(bin_path=bin_path, home=env / "home", tasks=True, force=True)
    script = env / "home" / ".shortcuts" / "System Dashboard"
    task = env / "home" / ".shortcuts" / "tasks" / "Sysdash Snapshot"
    assert script.exists() and task.exists()
    assert report["shortcuts"]
    foreign = env / "home" / ".shortcuts" / "my-script"
    foreign.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    removed = widget.remove(home=env / "home")
    assert script in removed and task in removed
    assert foreign.exists()
    assert (env / "home" / ".shortcuts").exists()
    assert widget.remove(home=env / "home") == []


def test_widget_dry_run(env):
    bin_path = env / "bin" / "sysdash"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(bin_path, 0o700)
    report = widget.install(bin_path=bin_path, home=env / "home", dry_run=True, force=True)
    assert all(status.startswith("would-") for _, status in report["shortcuts"])
    assert not (env / "home" / ".shortcuts").exists()


def test_widget_refuses_unmanaged_file(env):
    bin_path = env / "bin" / "sysdash"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(bin_path, 0o700)
    root = env / "home" / ".shortcuts"
    root.mkdir(parents=True)
    target = root / "System Dashboard"
    target.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
    with pytest.raises(widget.WidgetError):
        widget.install(bin_path=bin_path, home=env / "home", force=False)
    assert target.read_text(encoding="utf-8").startswith("#!/bin/sh\necho foreign")


def test_scripts_have_valid_bash_syntax(env):
    bin_path = env / "bin" / "sysdash"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(bin_path, 0o700)
    widget.install(bin_path=bin_path, home=env / "home", tasks=True, force=True)
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    for path in (env / "home" / ".shortcuts").rglob("*"):
        if path.is_file():
            result = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr


def test_doctor_does_not_crash(env, monkeypatch):
    monkeypatch.setattr(widget, "app_installed", lambda: True)
    checks = installer.doctor()
    assert checks
    assert all(status in {"PASS", "WARN", "FAIL"} for status, _, _ in checks)
