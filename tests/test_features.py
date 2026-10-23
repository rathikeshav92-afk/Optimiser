import pytest

from pcoptimizer import benchmark, sensors
from pcoptimizer.backend import _DemoRunner
from pcoptimizer.games import COMPAT_LAYERS, GPU_PREFS, IFEO, GameLibrary
from pcoptimizer.registry import FakeRegistry
from pcoptimizer.services import SERVICES_KEY
from pcoptimizer.tweaks import TweakManager

GAME = r"C:\Games\Cool Game\cool.exe"


@pytest.fixture
def mgr(tmp_path):
    return TweakManager(FakeRegistry(), _DemoRunner(), backup_path=tmp_path / "backup.json")


# ----------------------------------------------------------------------------- game profiles

def test_add_game_sets_priority_and_gpu(mgr, tmp_path):
    lib = GameLibrary(mgr, tmp_path / "games.json")
    lib.add(GAME)
    reg = mgr.registry
    assert reg.get("HKLM", IFEO + r"\cool.exe\PerfOptions", "CpuPriorityClass") == (3, "DWORD")
    assert reg.get("HKCU", GPU_PREFS, GAME) == ("GpuPreference=2;", "SZ")
    assert reg.get("HKCU", COMPAT_LAYERS, GAME) is None


def test_game_options_and_remove_restore_everything(mgr, tmp_path):
    reg = mgr.registry
    reg.set("HKCU", GPU_PREFS, GAME, "GpuPreference=1;", "SZ")  # user had chosen power-saving before
    lib = GameLibrary(mgr, tmp_path / "games.json")
    game = lib.add(GAME)
    lib.update(game, disable_fso=True, high_priority=False)
    assert reg.get("HKCU", COMPAT_LAYERS, GAME)[0].startswith("~")
    assert reg.get("HKLM", IFEO + r"\cool.exe\PerfOptions", "CpuPriorityClass") is None
    lib.remove(game)
    assert reg.get("HKCU", GPU_PREFS, GAME) == ("GpuPreference=1;", "SZ")
    assert reg.get("HKCU", COMPAT_LAYERS, GAME) is None
    assert not mgr.has_backup(game.tweak_id)


def test_game_library_persists(mgr, tmp_path):
    GameLibrary(mgr, tmp_path / "games.json").add(GAME)
    fresh_mgr = TweakManager(mgr.registry, mgr.runner, backup_path=mgr.backup_path)
    lib = GameLibrary(fresh_mgr, tmp_path / "games.json")
    assert [g.name for g in lib.games] == ["cool"]
    assert fresh_mgr.is_applied(lib.games[0].tweak_id)


def test_revert_all_handles_orphaned_game_backup(mgr, tmp_path):
    """If games.json is lost, Undo All must still restore the registry from the backup."""
    GameLibrary(mgr, tmp_path / "games.json").add(GAME)
    fresh = TweakManager(mgr.registry, mgr.runner, backup_path=mgr.backup_path)  # game not registered
    assert fresh.revert_all()
    assert mgr.registry.get("HKCU", GPU_PREFS, GAME) is None


# ----------------------------------------------------------------------------- services

def test_service_disable_and_restore(mgr):
    calls = []
    runner = mgr.runner
    mgr.runner = lambda cmd: (calls.append(cmd), runner(cmd))[1]
    key = SERVICES_KEY + r"\DiagTrack"
    mgr.registry.set("HKLM", key, "Start", 2, "DWORD")
    mgr.apply("svc_diagtrack")
    assert mgr.registry.get("HKLM", key, "Start")[0] == 4
    assert mgr.is_applied("svc_diagtrack")
    mgr.revert("svc_diagtrack")
    assert mgr.registry.get("HKLM", key, "Start")[0] == 2
    assert calls == ['sc stop "DiagTrack"', 'sc start "DiagTrack"']


def test_missing_service_is_never_created(mgr):
    tweak = mgr.by_id["svc_fax"]
    assert not tweak.is_available(mgr)
    with pytest.raises(RuntimeError):
        mgr.apply("svc_fax")
    assert mgr.registry.get("HKLM", SERVICES_KEY + r"\Fax", "Start") is None


# ----------------------------------------------------------------------------- sensors

def test_sensor_parsing():
    r = sensors.parse('{"cpu_temp": 54.3, "gpu_temp": "61", "gpu_load": "37", "gpu_fan": "[N/A]"}')
    assert (r.cpu_temp, r.gpu_temp, r.gpu_load, r.gpu_fan) == (54.3, 61.0, 37.0, None)
    fake = sensors.parse('{"cpu_temp": 27.85}')  # bogus ACPI zone found on many desktops
    assert fake.cpu_temp is None
    assert sensors.parse("not json") is None


# ----------------------------------------------------------------------------- benchmark

def test_benchmark_components_run_quickly(tmp_path):
    assert benchmark.cpu_test(2, seconds=0.2) > 0
    assert benchmark.ram_test(seconds=0.2, size=8 * 1024 ** 2) > 0
    seq, iops = benchmark.disk_test(str(tmp_path), size=8 * 1024 ** 2, seconds=0.2)
    assert seq > 0 and iops > 0
    assert not list(tmp_path.iterdir())  # temp file cleaned up


def test_benchmark_history_and_change(tmp_path):
    hist = benchmark.BenchmarkHistory(tmp_path / "b.json")
    vals = {"cpu_single": 1000, "cpu_multi": 8000, "ram_copy": 10, "disk_write": 1000, "disk_iops": 1000,
            "bg_cpu": 0, "free_ram": 8}
    hist.add(benchmark.BenchResult("t1", vals, benchmark.overall_score(vals)))
    assert hist.baseline.score == 1000 and hist.latest is None
    better = dict(vals, disk_write=1210, bg_cpu=0)
    hist.add(benchmark.BenchResult("t2", better, benchmark.overall_score(better)))
    reloaded = benchmark.BenchmarkHistory(tmp_path / "b.json")
    assert reloaded.latest.score > reloaded.baseline.score
    assert benchmark.change("disk_write", 1000, 1210) == pytest.approx(21.0)
    assert benchmark.change("bg_cpu", 12, 4) == 8
    reloaded.set_baseline_to_latest()
    assert len(reloaded.results) == 1
