from pcoptimizer import boost
from pcoptimizer.backend import DemoBackend
from pcoptimizer.tweaks import TELEMETRY_TASKS


def make(tmp_path):
    return DemoBackend(data_dir=tmp_path)


def test_full_boost_then_undo_all(tmp_path):
    b = make(tmp_path)
    reg = b.registry
    res = boost.run(b, {s.id for s in boost.STEPS}, lambda _m: None)
    assert not res.failed and res.needs_restart and res.freed_bytes > 0
    # privacy registry, services and scheduled tasks were all changed
    assert reg.get("HKCU", r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled") == (0, "DWORD")
    assert reg.get("HKLM", r"SYSTEM\CurrentControlSet\Services\DiagTrack", "Start")[0] == 4
    assert b.tweaks.runner.disabled_tasks >= set(TELEMETRY_TASKS)
    # optional ones were left alone
    assert reg.get("HKLM", r"SYSTEM\CurrentControlSet\Services\Spooler", "Start")[0] == 2
    assert reg.get("HKCU", r"Software\Policies\Microsoft\Windows\WindowsCopilot", "TurnOffWindowsCopilot") is None

    b.tweaks.revert_all()
    assert reg.get("HKCU", r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", "Enabled") is None
    assert reg.get("HKLM", r"SYSTEM\CurrentControlSet\Services\DiagTrack", "Start")[0] == 2
    assert not b.tweaks.runner.disabled_tasks


def test_second_boost_changes_nothing(tmp_path):
    b = make(tmp_path)
    boost.run(b, {"speed", "privacy", "services"}, lambda _m: None)
    again = boost.run(b, {"speed", "privacy", "services"}, lambda _m: None)
    assert again.applied == [] and again.already


def test_only_chosen_steps_run(tmp_path):
    b = make(tmp_path)
    res = boost.run(b, {"dns"}, lambda _m: None)
    assert res.applied == [] and res.freed_bytes == 0
    assert not any(b.tweaks.has_backup(t.id) for t in b.tweaks.tweaks if t.category != "game")


def test_tasks_tweak_restores_only_previously_enabled(tmp_path):
    b = make(tmp_path)
    b.tweaks.runner.disabled_tasks = {TELEMETRY_TASKS[0]}  # user had already disabled one
    b.tweaks.apply("telemetry_tasks_off")
    b.tweaks.revert("telemetry_tasks_off")
    assert b.tweaks.runner.disabled_tasks == {TELEMETRY_TASKS[0]}


def test_ai_boost_tool(tmp_path):
    from types import SimpleNamespace

    from pcoptimizer.ai_assistant import Assistant

    b = make(tmp_path)
    a = Assistant(b, client=SimpleNamespace(), approve=lambda *_: True)
    out, err = a.run_tool("run_one_click_boost", {"steps": ["privacy"], "reason": "less tracking"})
    assert not err and "settings changed" in out
