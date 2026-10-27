import datetime as dt

import pytest

from pcoptimizer import health, network, settings
from pcoptimizer.backend import DemoBackend
from pcoptimizer.background import AutoGaming, helper_needed
from pcoptimizer.display import Monitor, best_rate
from pcoptimizer.tweaks import TweakManager

TODAY = dt.date(2026, 10, 24)


def facts(**kw):
    base = dict(ram_speed=3600, ram_type="DDR4", ram_sticks=2, ram_gb=32, disk_free=400 * 1024 ** 3,
                disk_size=1000 * 1024 ** 3, disk_kind="NVMe SSD", gpu_name="NVIDIA GeForce RTX 4070",
                gpu_driver_date="2026-09-01", cpu_temp=50.0, gpu_temp=45.0, startup_enabled=3,
                last_boot="2026-10-22 09:00", hvci_running=False, rebar=True, today=TODAY,
                monitors=[Monitor("\\\\.\\DISPLAY1", "x", 2560, 1440, 165, 165)])
    base.update(kw)
    return health.Facts(**base)


def statuses(f):
    return {i.id: i.status for i in health.evaluate(f)}


def test_healthy_pc_has_no_problems():
    assert health.score(health.evaluate(facts())) == (0, 0)


@pytest.mark.parametrize("kw,item,status", [
    (dict(ram_speed=2133), "xmp", "warn"), (dict(ram_type="DDR5", ram_speed=4800), "xmp", "warn"),
    (dict(ram_type="DDR5", ram_speed=6000), "xmp", "ok"), (dict(ram_sticks=1), "dual_channel", "warn"),
    (dict(disk_free=50 * 1024 ** 3), "disk_space", "bad"), (dict(disk_free=120 * 1024 ** 3), "disk_space", "warn"),
    (dict(disk_kind="HDD"), "ssd", "warn"), (dict(gpu_driver_date="2025-12-01"), "gpu_driver", "warn"),
    (dict(cpu_temp=92.0), "temps", "bad"), (dict(startup_enabled=14), "startup", "warn"),
    (dict(last_boot="2026-10-01 08:00"), "uptime", "warn"), (dict(rebar=False), "rebar", "warn"),
    (dict(hvci_running=True), "hvci", "info"),
])
def test_individual_findings(kw, item, status):
    assert statuses(facts(**kw))[item] == status


def test_refresh_rate_finding_has_fix():
    f = facts(monitors=[Monitor("\\\\.\\DISPLAY2", "x", 1920, 1080, 60, 144)])
    item = next(i for i in health.evaluate(f) if i.id.startswith("refresh"))
    assert item.status == "bad" and item.fix == "refresh:\\\\.\\DISPLAY2:144"
    assert health.evaluate(f)[0] == item  # problems come first


def test_best_rate_only_counts_current_resolution():
    modes = [(1920, 1080, 32, 60), (1920, 1080, 32, 144), (2560, 1440, 32, 240), (1920, 1080, 16, 165)]
    assert best_rate(modes, 1920, 1080, 32) == 144


def test_parse_rebar():
    on = "FB Memory Usage\n    Total : 12288 MiB\nBAR1 Memory Usage\n    Total : 16384 MiB\n"
    off = "FB Memory Usage\n    Total : 12288 MiB\nBAR1 Memory Usage\n    Total : 256 MiB\n"
    assert health.parse_rebar(on) is True and health.parse_rebar(off) is False and health.parse_rebar("") is None


# ----------------------------------------------------------------------------- Auto Gaming Mode

@pytest.fixture
def b(tmp_path):
    return DemoBackend(data_dir=tmp_path)


def test_auto_gaming_switches_and_restores_only_its_changes(b, tmp_path):
    mgr_path = b.tweaks.backup_path
    b.tweaks.apply("mouse_accel_off")  # the user already had this on
    running = {"explorer.exe"}
    auto = AutoGaming(lambda: TweakManager(b.registry, b.tweaks.runner, backup_path=mgr_path),
                      lambda: running, lambda: {"cs2.exe"}, state_path=tmp_path / "state.json")
    auto.tick()
    assert not auto.state["active"]

    running.add("cs2.exe")
    auto.tick()
    assert auto.state["active"] and "mouse_accel_off" not in auto.state["changed"]
    fresh = TweakManager(b.registry, b.tweaks.runner, backup_path=mgr_path)
    assert fresh.is_applied("game_dvr_off") and fresh.is_applied("power_ultimate")

    running.discard("cs2.exe")
    auto.tick()
    auto.tick()
    assert auto.state["active"]  # waits for the game to stay closed
    auto.tick()
    assert not auto.state["active"]
    fresh = TweakManager(b.registry, b.tweaks.runner, backup_path=mgr_path)
    assert not fresh.is_applied("game_dvr_off") and not fresh.is_applied("power_ultimate")
    assert fresh.is_applied("mouse_accel_off")  # left as the user had it


def test_auto_gaming_state_survives_restart(b, tmp_path):
    state = tmp_path / "state.json"
    mk = lambda: TweakManager(b.registry, b.tweaks.runner, backup_path=b.tweaks.backup_path)  # noqa: E731
    a1 = AutoGaming(mk, lambda: {"cs2.exe"}, lambda: {"cs2.exe"}, state_path=state)
    a1.tick()
    a2 = AutoGaming(mk, lambda: set(), lambda: {"cs2.exe"}, state_path=state)  # helper restarted after the game
    for _ in range(3):
        a2.tick()
    assert not a2.state["active"] and not mk().is_applied("game_dvr_off")


def test_settings_and_helper_needed(tmp_path):
    p = tmp_path / "s.json"
    assert settings.load(p)["screentime"] is True and settings.load(p)["auto_gaming"] is False
    settings.save({"auto_gaming": True}, p)
    assert settings.load(p)["auto_gaming"] is True
    assert helper_needed({"screentime": False, "auto_gaming": True})
    assert not helper_needed({"screentime": False, "auto_gaming": False})


# ----------------------------------------------------------------------------- DNS & Defender

def test_dns_provider_detection():
    A = network.Adapter
    assert network.current_provider([A(1, "Ethernet", "", ["192.168.1.1"], [])]) == "auto"
    assert network.current_provider([A(1, "Ethernet", "", ["1.1.1.1"], ["1.1.1.1", "1.0.0.1"])]) == "cloudflare"
    assert network.current_provider([A(1, "Ethernet", "", ["10.0.0.53"], ["10.0.0.53"])]) == "custom"
    ads = network.parse_adapters({"Index": 7, "Name": "Wi-Fi", "Dns": "8.8.8.8", "Static": "8.8.8.8,8.8.4.4"})
    assert ads[0].static == ["8.8.8.8", "8.8.4.4"] and network.current_provider(ads) == "google"


def test_defender_exclusion_keeps_preexisting(b):
    g = b.games.games[0]
    b.tweaks.runner.exclusions = {g.folder}  # the user had excluded it already
    b.games.update(g, defender_exclusion=True)
    b.games.update(g, defender_exclusion=False)
    assert b.tweaks.runner.exclusions == {g.folder}
    b.tweaks.runner.exclusions = set()
    b.games.update(g, defender_exclusion=True)
    b.games.remove(g)
    assert b.tweaks.runner.exclusions == set()


@pytest.mark.parametrize("gpu,capable", [("NVIDIA GeForce GTX 1630", False), ("NVIDIA GeForce RTX 2060", False),
                                         ("NVIDIA GeForce RTX 3060", True), ("NVIDIA GeForce RTX 4070", True),
                                         ("AMD Radeon RX 580", False), ("AMD Radeon RX 6700 XT", True),
                                         ("Intel(R) Arc(TM) A770 Graphics", True), ("Intel(R) UHD Graphics 770", False),
                                         ("Microsoft Basic Display Adapter", False)])
def test_rebar_only_suggested_for_capable_cards(gpu, capable):
    assert health.rebar_capable(gpu) is capable
    assert ("rebar" in statuses(facts(gpu_name=gpu, rebar=False))) is capable
