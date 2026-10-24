"""CI smoke test for Health Check, Auto Gaming Mode, Fast DNS and Defender exclusions on real Windows."""

import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from ci/: make the package importable

from pcoptimizer import display, health, network  # noqa: E402
from pcoptimizer.background import AutoGaming, running_exe_names  # noqa: E402
from pcoptimizer.backend import Backend  # noqa: E402
from pcoptimizer.games import GameLibrary  # noqa: E402
from pcoptimizer.tweaks import TweakManager  # noqa: E402

failures = []


def check(name, fn):
    start = time.time()
    try:
        print(f"{name}: {fn()}  ({time.time() - start:.0f}s)")
    except Exception:
        failures.append(name)
        print(f"{name}: FAILED")
        traceback.print_exc(file=sys.stdout)


def monitors():
    return [(m.device, m.width, m.height, m.current_hz, m.max_hz) for m in display.monitors()] or "none (headless)"


def processes():
    names = running_exe_names()
    assert "python.exe" in names, sorted(names)[:20]
    return f"{len(names)} processes, python.exe found"


def health_check():
    b = Backend()
    findings = health.evaluate(b.health_facts(b.specs()))
    assert findings
    return "\n  " + "\n  ".join(f"{f.status:4} {f.title}: {f.detail[:90]}" for f in findings)


def auto_gaming():
    b = Backend()
    path = Path(tempfile.mkdtemp()) / "backup.json"
    running = {"explorer.exe"}
    auto = AutoGaming(lambda: TweakManager(b.registry, b.tweaks.runner, backup_path=path), lambda: running,
                      lambda: {"fakegame.exe"}, state_path=path.with_name("state.json"), log=print)
    running.add("fakegame.exe")
    auto.tick()
    on = TweakManager(b.registry, b.tweaks.runner, backup_path=path)
    assert on.is_applied("power_ultimate") and on.is_applied("game_dvr_off")
    running.discard("fakegame.exe")
    for _ in range(3):
        auto.tick()
    off = TweakManager(b.registry, b.tweaks.runner, backup_path=path)
    assert not auto.state["active"] and not off.is_applied("power_ultimate")
    return f"switched on ({len(auto.state.get('changed', [])) or 'all'} settings) and back off"


def dns():
    adapters = network.list_adapters()
    assert adapters, "no active adapters"
    before = network.current_provider(adapters)
    times = {k: network.measure(s) for k, (_, s) in network.PROVIDERS.items()}
    try:
        log = network.set_provider("cloudflare", adapters)
        assert all(line.startswith("[OK]") for line in log), log
        assert network.current_provider(network.list_adapters()) == "cloudflare"
    finally:
        log = network.set_provider("auto", adapters)
    after = network.current_provider(network.list_adapters())
    assert after == before, (before, after)
    return f"{[a.name for a in adapters]} {before} -> cloudflare -> {after}; lookup ms {times}"


def defender():
    b = Backend()
    folder = Path(tempfile.mkdtemp()) / "Game"
    folder.mkdir()
    lib = GameLibrary(TweakManager(b.registry, b.tweaks.runner, backup_path=folder / "b.json"), folder / "g.json")
    game = lib.add(str(folder / "game.exe"))
    code, out = lib._ps("(Get-MpPreference).ExclusionPath -ne $null")
    if code != 0:
        return f"skipped - Defender not available on this runner ({out.strip()[:80]})"
    lib.update(game, defender_exclusion=True)
    code, out = lib._ps(f"(Get-MpPreference).ExclusionPath -contains '{game.folder}'")
    assert out.strip() == "True", out
    lib.remove(game)
    code, out = lib._ps(f"(Get-MpPreference).ExclusionPath -contains '{game.folder}'")
    assert out.strip() == "False", out
    return "exclusion added and removed"


check("monitors", monitors)
check("running processes", processes)
check("memory integrity", lambda: health.hvci_running())
check("health check", health_check)
check("auto gaming mode", auto_gaming)
check("defender exclusion", defender)
check("fast dns", dns)
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
