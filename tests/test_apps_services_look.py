from types import SimpleNamespace

import pytest

from pcoptimizer import appx, servicemgr
from pcoptimizer.ai_assistant import Assistant
from pcoptimizer.backend import DemoBackend
from pcoptimizer.tweaks import CLASSIC_MENU


@pytest.fixture
def b(tmp_path):
    return DemoBackend(data_dir=tmp_path)


# ----------------------------------------------------------------------------- services manager

def test_parse_services_and_modes():
    svcs = servicemgr.parse_all([
        {"Name": "BITS", "DisplayName": "Background Intelligent Transfer", "State": "Running", "StartMode": "Auto",
         "Delayed": True},
        {"Name": "Fax", "DisplayName": "Fax", "State": "Stopped", "StartMode": "Manual"},
        {"Name": "RpcSs", "DisplayName": "RPC", "State": "Running", "StartMode": "Auto"},
        {"Name": "cbdhsvc_4f2a1", "DisplayName": "Clipboard_4f2a1", "State": "Running", "StartMode": "Auto"}])
    by = {s.name: s for s in svcs}
    assert by["BITS"].mode == "delayed-auto" and by["BITS"].start_label == "Automatic (Delayed)"
    assert by["Fax"].mode == "demand" and not by["Fax"].protected
    assert by["RpcSs"].protected and by["cbdhsvc_4f2a1"].per_user and by["cbdhsvc_4f2a1"].protected


@pytest.mark.parametrize("text,mode", [("START_TYPE         : 2   AUTO_START  (DELAYED)", "delayed-auto"),
                                       ("START_TYPE         : 2   AUTO_START", "auto"),
                                       ("START_TYPE         : 3   DEMAND_START", "demand"),
                                       ("START_TYPE         : 4   DISABLED", "disabled"), ("garbage", None)])
def test_parse_sc_qc(text, mode):
    assert servicemgr.parse_sc_qc(text) == mode


def test_set_start_twice_then_undo(b):
    sm = b.servicemgr
    svc = next(s for s in b.all_services() if s.name == "SysMain")
    sm.set_start(svc, "demand")
    svc = next(s for s in b.all_services() if s.name == "SysMain")
    sm.set_start(svc, "disabled")
    assert b.tweaks.runner.service_modes["SysMain"] == "disabled"
    assert sm.changed_names() == {"SysMain"}
    b.tweaks.revert_all()  # from Backup & Restore
    assert b.tweaks.runner.service_modes["SysMain"] == "auto"
    assert sm.changed_names() == set()


def test_protected_and_per_user_services_are_refused(b):
    for name in ("RpcSs", "cbdhsvc_4f2a1"):
        svc = next(s for s in b.all_services() if s.name == name)
        with pytest.raises(ValueError):
            b.servicemgr.set_start(svc, "disabled")
    ok, msg = servicemgr.control(next(s for s in b.all_services() if s.name == "RpcSs"), "stop")
    assert not ok and "needed by Windows" in msg


def test_service_change_persists(b, tmp_path):
    svc = next(s for s in b.all_services() if s.name == "Fax")
    b.servicemgr.set_start(svc, "disabled")
    fresh = servicemgr.ServiceManager(b.tweaks, path=tmp_path / "service_changes.json")
    assert "Fax" in fresh.changes and b.tweaks.is_applied(fresh.tweak_id("Fax"))


# ----------------------------------------------------------------------------- built-in apps

def test_app_groups():
    apps = appx.parse_apps([{"Name": "Microsoft.WindowsStore", "Full": "x"}, {"Name": "Microsoft.BingNews", "Full": "y"},
                            {"Name": "Microsoft.GamingApp", "Full": "z"}, {"Name": "Contoso.Tool", "Full": "w"}])
    assert [(a.friendly, a.group) for a in apps] == [("News", "bloat"), ("Xbox app", "optional"),
                                                     ("Tool", "other"), ("Microsoft Store", "protected")]
    assert "Game Pass" in apps[1].warning


def test_protected_app_never_removed():
    ok, msg = appx.remove(appx.App("Microsoft.WindowsStore", "Microsoft.WindowsStore_1_x64__8wekyb3d8bbwe", "f",
                                   "Microsoft Store", "protected"))
    assert not ok and "needed" in msg
    ok, msg = appx.remove(appx.App("Evil", "x'; Remove-Item C:\\ ;'", "f", "Evil", "bloat"))
    assert not ok and "Invalid" in msg


def test_remove_and_reinstall_demo(b):
    news = next(a for a in b.list_apps() if a.name == "Microsoft.BingNews")
    assert b.remove_app(news)[0]
    assert "Microsoft.BingNews" not in {a.name for a in b.list_apps()}
    assert "Microsoft.BingNews" in b.removed_apps()
    b.reinstall_app("Microsoft.BingNews")
    assert "Microsoft.BingNews" in {a.name for a in b.list_apps()}


# ----------------------------------------------------------------------------- look & feel

def test_appearance_tweaks_roundtrip(b):
    m, reg = b.tweaks, b.registry
    ids = [t.id for t in m.in_category("appearance")]
    assert len(ids) >= 12 and not any(t.recommended for t in m.in_category("appearance"))
    for i in ids:
        m.apply(i)
    assert reg.key_exists("HKCU", CLASSIC_MENU)
    m.revert_all()
    assert not reg.key_exists("HKCU", CLASSIC_MENU)
    assert reg.get("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", "HideFileExt") is None


def test_ai_tools_for_services_and_apps(b):
    a = Assistant(b, client=SimpleNamespace(), approve=lambda *_: True)
    out, err = a.run_tool("set_service_startup", {"name": "Fax", "mode": "disabled", "reason": "unused"})
    assert not err and b.tweaks.runner.service_modes["Fax"] == "disabled"
    out, err = a.run_tool("list_all_services", {"search": "xbox"})
    assert not err and "XblGameSave" in out and "Fax" not in out
    out, err = a.run_tool("remove_builtin_apps", {"names": ["Microsoft.WindowsStore"], "reason": "x"})
    assert err and "Protected" in out
    out, err = a.run_tool("remove_builtin_apps", {"names": ["Microsoft.GetHelp"], "reason": "unused"})
    assert not err and "Removed Get Help" in out
