from pcoptimizer import fixall, health
from pcoptimizer.backend import DemoBackend


def test_fix_everything_in_demo(tmp_path):
    b = DemoBackend(data_dir=tmp_path)
    logs = []
    res = fixall.run(b, {k for k, *_ in fixall.ACTIONS}, logs.append)
    assert not res.failed, logs
    assert {"One-Click Boost", "Refresh rate", "Fast DNS", "Driver updates", "Auto Gaming Mode"} <= set(res.done)
    assert b.monitors()[0].current_hz == 144
    assert b._dns == "cloudflare"  # fastest in the demo
    assert b.auto_gaming_enabled() and not b.search_driver_updates()
    # second run finds nothing left to do
    again = fixall.run(b, {"refresh", "dns", "drivers"}, logs.append)
    assert set(again.skipped) == {"Refresh rate", "Fast DNS", "Driver updates"}


def test_auto_gaming_skipped_without_games(tmp_path):
    b = DemoBackend(data_dir=tmp_path)
    b.games.remove(b.games.games[0])
    res = fixall.run(b, {"auto_gaming"}, lambda _m: None)
    assert res.skipped == ["Auto Gaming Mode"] and not b.auto_gaming_enabled()


def test_manual_steps_only_for_open_findings():
    findings = [health.Finding("xmp", "", "warn", ""), health.Finding("rebar", "", "ok", ""),
                health.Finding("ssd", "", "warn", "")]
    steps = fixall.manual_steps(findings)
    assert len(steps) == 2 and "XMP" in steps[0]
