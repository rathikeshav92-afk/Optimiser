import os
import time

import pytest

from pcoptimizer import cleaner, processes, profiles
from pcoptimizer.backend import _DemoRunner
from pcoptimizer.rating import cpu_generation_factor, gpu_factor, rate
from pcoptimizer.registry import FakeRegistry
from pcoptimizer.system_info import Disk, Gpu, SystemSpecs, demo_specs
from pcoptimizer.tweaks import TweakManager

GIB = 1024 ** 3


@pytest.fixture
def mgr(tmp_path):
    return TweakManager(FakeRegistry(), _DemoRunner(), backup_path=tmp_path / "backup.json")


# ----------------------------------------------------------------------------- rating

def test_demo_pc_rates_well():
    r = rate(demo_specs())
    assert 70 <= r.total <= 95
    assert sum(c.max_score for c in r.components) == 100


def test_high_end_beats_low_end():
    high = SystemSpecs(cpu_name="Intel(R) Core(TM) i9-14900K", cores=24, threads=32, max_clock_mhz=3200,
                       total_ram_bytes=32 * GIB, ram_speed_mhz=6000, os_name="Microsoft Windows 11 Pro",
                       gpus=[Gpu("NVIDIA GeForce RTX 4090", vram_bytes=24 * GIB)],
                       disks=[Disk("NVMe", "SSD", "NVMe", is_system=True)],
                       sys_drive_free=600 * GIB, sys_drive_size=1000 * GIB)
    low = SystemSpecs(cpu_name="Intel(R) Celeron(R) N4020", cores=2, threads=2, max_clock_mhz=1100,
                      total_ram_bytes=4 * GIB, ram_speed_mhz=2400, os_name="Microsoft Windows 10 Home",
                      gpus=[Gpu("Intel(R) UHD Graphics 600", vram_bytes=128 * 1024 ** 2)],
                      disks=[Disk("HDD", "HDD", "SATA", is_system=True)],
                      sys_drive_free=10 * GIB, sys_drive_size=500 * GIB)
    hi, lo = rate(high), rate(low)
    assert hi.total >= 90 and hi.grade == "Elite"
    assert lo.total < 35
    assert 0 <= lo.total < hi.total <= 100


@pytest.mark.parametrize("better,worse", [
    ("NVIDIA GeForce RTX 4080", "NVIDIA GeForce RTX 3060"),
    ("NVIDIA GeForce RTX 3060", "NVIDIA GeForce GTX 1650"),
    ("AMD Radeon RX 7900 XTX", "AMD Radeon RX 6600"),
    ("NVIDIA GeForce GTX 1650", "Intel(R) UHD Graphics 630"),
])
def test_gpu_ordering(better, worse):
    assert gpu_factor(better) > gpu_factor(worse)


@pytest.mark.parametrize("better,worse", [
    ("Intel(R) Core(TM) i7-12700K", "Intel(R) Core(TM) i7-7700K"),
    ("AMD Ryzen 9 7950X 16-Core Processor", "AMD Ryzen 5 2600 Six-Core Processor"),
    ("Intel(R) Core(TM) i5-10400", "Intel(R) Pentium(R) Gold G6400"),
])
def test_cpu_ordering(better, worse):
    assert cpu_generation_factor(better) > cpu_generation_factor(worse)


def test_rating_handles_empty_specs():
    r = rate(SystemSpecs())
    assert 0 <= r.total <= 100


# ----------------------------------------------------------------------------- tweaks

def test_apply_and_revert_restores_original(mgr):
    reg = mgr.registry
    reg.set("HKCU", r"Control Panel\Desktop", "MenuShowDelay", "400", "SZ")
    assert not mgr.is_applied("menu_delay")
    mgr.apply("menu_delay")
    assert mgr.is_applied("menu_delay")
    assert reg.get("HKCU", r"Control Panel\Desktop", "MenuShowDelay")[0] == "100"
    assert mgr.revert("menu_delay")
    assert reg.get("HKCU", r"Control Panel\Desktop", "MenuShowDelay") == ("400", "SZ")
    assert not mgr.has_backup("menu_delay")


def test_revert_deletes_values_that_did_not_exist(mgr):
    mgr.apply("game_dvr_off")
    assert mgr.registry.get("HKCU", r"System\GameConfigStore", "GameDVR_Enabled") == (0, "DWORD")
    mgr.revert("game_dvr_off")
    assert mgr.registry.get("HKCU", r"System\GameConfigStore", "GameDVR_Enabled") is None


def test_first_snapshot_is_kept(mgr):
    reg = mgr.registry
    reg.set("HKCU", r"Control Panel\Desktop", "MenuShowDelay", "400", "SZ")
    mgr.apply("menu_delay")
    mgr.apply("menu_delay")  # second apply must not overwrite the original backup
    mgr.revert("menu_delay")
    assert reg.get("HKCU", r"Control Panel\Desktop", "MenuShowDelay")[0] == "400"


def test_binary_values_roundtrip_through_json(mgr, tmp_path):
    reg = mgr.registry
    original = bytes.fromhex("9e1e078012000000")
    reg.set("HKCU", r"Control Panel\Desktop", "UserPreferencesMask", original, "BINARY")
    mgr.apply("visual_fx")
    assert mgr.is_applied("visual_fx")
    # Reload from disk to prove the backup survives an app restart.
    fresh = TweakManager(reg, mgr.runner, backup_path=mgr.backup_path)
    fresh.revert("visual_fx")
    assert reg.get("HKCU", r"Control Panel\Desktop", "UserPreferencesMask")[0] == original


def test_dword_ffffffff_matches_signed(mgr):
    mgr.apply("mmcss_games")
    key = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
    mgr.registry.set("HKLM", key, "NetworkThrottlingIndex", -1, "DWORD")
    assert mgr.is_applied("mmcss_games")


def test_revert_all(mgr):
    for t in mgr.in_category("speed"):
        if t.recommended:
            mgr.apply(t.id)
    reverted = mgr.revert_all()
    assert reverted and not any(mgr.has_backup(t.id) for t in mgr.tweaks)


# ----------------------------------------------------------------------------- profiles

def test_gaming_then_balanced_roundtrip(mgr):
    runner = mgr.runner
    start_plan = runner.active
    results = profiles.apply_gaming(mgr, log=lambda _: None)
    assert all(r.ok for r in results)
    assert profiles.current_profile(mgr) == "Gaming"
    assert runner.plans[runner.active] == "Ultimate Performance"

    profiles.apply_balanced(mgr, log=lambda _: None)
    assert profiles.current_profile(mgr) == "Balanced"
    assert runner.active == start_plan
    assert not mgr.is_applied("power_throttling_off")
    assert mgr.is_applied("game_mode")

    # And back again - the Ultimate plan must be reused, not duplicated.
    profiles.apply_gaming(mgr, log=lambda _: None)
    assert profiles.current_profile(mgr) == "Gaming"
    assert list(runner.plans.values()).count("Ultimate Performance") == 1


# ----------------------------------------------------------------------------- cleaner

def test_cleaner_deletes_only_old_files(tmp_path):
    old = tmp_path / "old.tmp"
    new = tmp_path / "new.tmp"
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.log").write_text("x" * 100)
    old.write_text("x" * 50)
    new.write_text("x" * 10)
    past = time.time() - 5 * 3600
    for p in (old, sub / "nested.log"):
        os.utime(p, (past, past))
    target = cleaner.CleanTarget("t", "Test", "", [str(tmp_path / "*")], min_age_hours=2)
    assert cleaner.scan(target) == (150, 2)
    freed = cleaner.clean(target, log=lambda _: None)
    assert freed == 150
    assert new.exists() and not old.exists() and not sub.exists()
    assert tmp_path.exists()


def test_cleaner_ignores_relative_patterns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Temp").mkdir()
    keep = tmp_path / "Temp" / "important.txt"
    keep.write_text("data")
    target = cleaner.CleanTarget("t", "Test", "", [os.path.join("Temp", "*")])
    cleaner.clean(target, log=lambda _: None)
    assert keep.exists()


def test_human_sizes():
    assert cleaner.human(512) == "512 B"
    assert cleaner.human(5 * 1024 ** 2) == "5.0 MB"
    assert cleaner.human(3 * GIB) == "3.0 GB"


# ----------------------------------------------------------------------------- startup apps

def test_startup_disable_enable():
    reg = FakeRegistry()
    hive, key = processes.RUN_KEYS[0]
    reg.set(hive, key, "Spotify", r"C:\Spotify.exe", "SZ")
    [app] = processes.list_startup_apps(reg)
    assert app.enabled
    processes.set_startup_enabled(reg, app, False)
    assert not processes.list_startup_apps(reg)[0].enabled
    assert reg.get(hive, key, "Spotify") is not None  # never deleted
    processes.set_startup_enabled(reg, app, True)
    assert processes.list_startup_apps(reg)[0].enabled


def test_protected_processes_cannot_be_ended():
    ok, msg = processes.end_process("csrss.exe")
    assert not ok and "protected" in msg


def test_powershell_json_keeps_pipe_on_last_line(monkeypatch):
    from pcoptimizer import platform_utils

    seen = {}

    def fake(script, timeout=None):
        seen["script"] = script
        return platform_utils.subprocess.CompletedProcess([], 0, "[1]", "")

    monkeypatch.setattr(platform_utils, "powershell", fake)
    assert platform_utils.powershell_json("Get-X | ForEach-Object {\n  1\n}\n") == [1]
    assert "\n|" not in seen["script"].replace(" ", "")
