from types import SimpleNamespace

import pytest

from pcoptimizer import autocare, background, drivers, health, notify, settings
from pcoptimizer.backend import DemoBackend
from pcoptimizer.system_info import demo_specs

GIB = 1024 ** 3


@pytest.fixture
def b(tmp_path):
    b = DemoBackend(data_dir=tmp_path)
    b._junk = {t.id: 0 for t in b.clean_targets}
    b._junk["user_temp"] = int(0.4 * GIB)
    b._junk["win_temp"] = int(0.3 * GIB)
    b._junk["browser_cache"] = 2 * GIB  # not "safe junk": must never be cleaned automatically
    return b


def test_fixes_safe_problems_by_itself(b):
    b._ram_used = 0.9
    rep = autocare.run(b, demo_specs())
    assert any("memory" in f for f in rep.fixed)
    assert any("junk" in f for f in rep.fixed)
    assert any("144 Hz" in f for f in rep.fixed) and b._hz == 144
    assert b._junk["user_temp"] == 0 and b._junk["win_temp"] == 0
    assert b._junk["browser_cache"] == 2 * GIB
    assert not rep.failed


def test_explains_what_needs_the_user(b):
    rep = autocare.run(b, demo_specs())
    titles = {p.title for p in rep.attention}
    assert "Your PC needs a restart" in titles
    assert "Your graphics driver is out of date" in titles
    assert "Your PC is missing its chipset drivers" in titles
    order = [p.severity for p in rep.problems]
    assert order == sorted(order, key=[autocare.BAD, autocare.WARN, autocare.TIP].index)  # most serious first
    # BIOS and hardware upgrades are optional tips, not alarms; the security trade-off is never mentioned
    assert {"xmp", "rebar"} <= {p.id for p in rep.tips}
    assert "hvci" not in {p.id for p in rep.problems}
    assert all(p.action for p in rep.attention if p.id != "temps")


def test_auto_fix_off_only_reports(b):
    b._ram_used = 0.9
    rep = autocare.run(b, demo_specs(), auto_fix=False)
    assert rep.fixed == []
    assert b._hz == 60 if hasattr(b, "_hz") else True
    ids = {p.id for p in rep.attention}
    assert {"ram", "junk"} <= ids and any(i.startswith("refresh:") for i in ids)
    assert b._junk["user_temp"] > 0


def test_failed_fix_is_reported_not_raised(b):
    b.set_refresh_rate = lambda device, hz: (False, "The monitor refused")
    b.problem_devices = lambda: (_ for _ in ()).throw(RuntimeError("PnP query failed"))
    rep = autocare.run(b, demo_specs())
    assert any("refused" in f for f in rep.failed)
    assert any("check your devices" in f for f in rep.failed)
    assert not rep.healthy


def test_disk_space_problem_wording():
    p = autocare.explain(health.Finding("disk_space", "Free space", health.BAD, "Only 9 GB (4%) free."))
    assert p.severity == autocare.BAD and p.action == "page:cleaner" and "almost full" in p.title
    assert autocare.explain(health.Finding("x", "x", health.OK, "fine")) is None


def test_state_history_and_notification_dedupe(tmp_path):
    state = autocare.CareState(tmp_path / "care.json")
    rep = autocare.CareReport(fixed=["Deleted 1 GB of junk files"],
                              problems=[autocare.Problem("uptime", "Your PC needs a restart", "", autocare.WARN),
                                        autocare.Problem("xmp", "Memory", "", autocare.TIP)],
                              failed=["Couldn't clean junk files: access denied"])
    state.record(rep)
    first = state.to_notify(rep, now=1000)
    assert first == ["Your PC needs a restart", "Couldn't clean junk files: access denied"]  # tips never notify
    assert state.to_notify(rep, now=1000 + 3600) == []  # already told within the last day
    assert len(state.to_notify(rep, now=1000 + autocare.RENOTIFY_SECONDS)) == 2

    again = autocare.CareState(tmp_path / "care.json")  # shared with the background helper through the file
    assert again.history[0]["text"] == "Deleted 1 GB of junk files"
    assert again.last_report().attention[0].id == "uptime"


def test_summary():
    assert autocare.summary(autocare.CareReport()) == "Your PC is in good shape."
    rep = autocare.CareReport(fixed=["a", "b"], problems=[autocare.Problem("p", "P", "", autocare.BAD)])
    assert autocare.summary(rep) == "Auto Care fixed 2 things and 1 needs your attention."


def test_background_checkup_notifies_once(b):
    sent = []
    background.care_checkup(lambda: b, True, lambda _m: None, notify=sent.append)
    background.care_checkup(lambda: b, True, lambda _m: None, notify=sent.append)
    assert sent[0] and sent[1] == []
    assert b.care_state().history


def test_helper_runs_for_auto_care():
    assert background.helper_needed({"auto_care": True})
    assert not background.helper_needed({"screentime": False, "auto_gaming": False, "auto_care": False})
    assert settings.DEFAULTS["auto_care"] and settings.DEFAULTS["auto_fix"]


def test_toast_script_escapes_text():
    script = notify.toast_script("Disk <full>", "It's 95% & rising")
    assert "&lt;full&gt;" in script and "&amp;" in script and "It''s" in script


def dev(name, dev_id):
    return drivers.ProblemDevice(name, "", 28, dev_id)


def test_missing_chipset_drivers_are_one_problem():
    devices = [dev("PCI Device", r"PCI\VEN_8086&DEV_7A4D&SUBSYS_00000000&REV_11&11583659&0&A0"),
               dev("PCI Device", r"PCI\VEN_8086&DEV_7A24&SUBSYS_00000000&REV_11&11583659&0&A1"),
               dev("SM Bus Controller", r"PCI\VEN_8086&DEV_7A23&SUBSYS_50011458&REV_11&1"),
               dev("Unknown device", r"ACPI\VEN_INTC&DEV_1070&daba3ff&0"),
               dev("Resource Hub proxy device", r"ACPI\VEN_MSFT&DEV_8000")]
    specs = SimpleNamespace(motherboard="Gigabyte Technology Co., Ltd. B760M GAMING DDR4")
    (p,) = autocare.device_problems(devices, specs)
    assert p.id == "chipset" and "5 parts" in p.detail and "Intel" in p.detail
    assert p.action.startswith("url:https://") and "B760M" in p.action


def test_other_broken_devices_are_grouped_by_name():
    devices = [dev("USB Audio", r"USB\VID_1"), dev("USB Audio", r"USB\VID_2"), dev("Webcam", r"USB\VID_3")]
    (p,) = autocare.device_problems(devices)
    assert p.title == "2 devices aren't working" and p.action == "page:drivers"
    (one,) = autocare.device_problems(devices[2:])
    assert one.title == "A device isn't working: Webcam"
    # without a known chipset vendor, a lone Windows placeholder is just a broken device
    assert autocare.device_problems([dev("Resource Hub proxy device", r"ACPI\VEN_MSFT&DEV_8000")])[0].id.startswith("device:")
