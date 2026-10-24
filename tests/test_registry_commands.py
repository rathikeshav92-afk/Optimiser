import base64

import pytest

from pcoptimizer import regedit, services
from pcoptimizer.backend import DemoBackend
from pcoptimizer.commands import COMMANDS

KEY = r"Control Panel\Desktop"


@pytest.fixture
def b(tmp_path):
    return DemoBackend(data_dir=tmp_path)


def test_parse_path_forms():
    assert regedit.parse_path(r"Computer\HKEY_LOCAL_MACHINE\SOFTWARE\X") == ("HKLM", r"SOFTWARE\X")
    assert regedit.parse_path("hkcu/Software/Y/") == ("HKCU", r"Software\Y")
    with pytest.raises(ValueError):
        regedit.parse_path(r"SOFTWARE\X")


@pytest.mark.parametrize("kind,text,value", [("DWORD", "0x10", 16), ("DWORD", "4294967295", 0xFFFFFFFF),
                                             ("QWORD", "5", 5), ("BINARY", "90 12, 03 80", "90120380"),
                                             ("MULTI_SZ", "a\nb", ["a", "b"]), ("SZ", " x ", " x ")])
def test_parse_value(kind, text, value):
    assert regedit.parse_value(kind, text) == value


@pytest.mark.parametrize("kind,text", [("DWORD", "-1"), ("DWORD", "4294967296"), ("DWORD", "abc"),
                                       ("BINARY", "zz"), ("BINARY", "123"), ("NONE", "")])
def test_parse_value_rejects(kind, text):
    with pytest.raises(ValueError):
        regedit.parse_value(kind, text)


def test_set_edit_twice_then_undo_restores_original(b):
    ed = b.regedit
    cid = ed.set_value("HKCU", KEY, "MenuShowDelay", "SZ", "100")
    ed.set_value("HKCU", KEY, "MenuShowDelay", "SZ", "50")  # second edit keeps the true original
    assert b.registry.get("HKCU", KEY, "MenuShowDelay")[0] == "50"
    assert b.tweaks.revert(cid)
    assert b.registry.get("HKCU", KEY, "MenuShowDelay") == ("400", "SZ")


def test_new_value_undo_deletes_it(b):
    cid = b.regedit.set_value("HKCU", KEY, "NewThing", "DWORD", "7")
    assert b.registry.get("HKCU", KEY, "NewThing") == (7, "DWORD")
    b.tweaks.revert(cid)
    assert b.registry.get("HKCU", KEY, "NewThing") is None


def test_delete_and_restore_binary(b, tmp_path):
    original = b.registry.get("HKCU", KEY, "UserPreferencesMask")
    cid = b.regedit.delete_value("HKCU", KEY, "UserPreferencesMask")
    assert b.registry.get("HKCU", KEY, "UserPreferencesMask") is None
    assert b.tweaks.is_applied(cid)
    # survives an app restart: a fresh editor re-registers the change
    fresh = regedit.RegistryEditor(b.tweaks, path=tmp_path / "registry_changes.json")
    assert cid in {c.id for c in fresh.changes}
    b.tweaks.revert(cid)
    assert b.registry.get("HKCU", KEY, "UserPreferencesMask") == original


def test_backup_reg_file_written_once_per_key(b):
    b.regedit.set_value("HKCU", KEY, "A", "SZ", "1")
    b.regedit.set_value("HKCU", KEY, "B", "SZ", "2")
    assert len(list(b.regedit.backup_dir.iterdir())) == 1


def test_other_types_are_read_only(b):
    b.registry.set("HKCU", KEY, "Weird", b"\x00", "OTHER")
    with pytest.raises(ValueError):
        b.regedit.delete_value("HKCU", KEY, "Weird")
    with pytest.raises(ValueError):
        b.regedit.set_value("HKCU", KEY, "Weird", "SZ", "x")


def test_dangerous_keys():
    assert regedit.is_dangerous(r"SYSTEM\CurrentControlSet\Services\Foo")
    assert not regedit.is_dangerous(KEY)


def test_service_protection():
    ok, msg = services.set_running("RpcSs", False)
    assert not ok and "needed by Windows" in msg
    assert not services.set_running("x'; Remove-Item C:\\ ;'", True)[0]
    svc = services.parse_running({"Name": "cbdhsvc_4f2a", "DisplayName": "Clipboard", "CanStop": True})[0]
    assert svc.protected


def test_commands_are_unique_and_encoded_scripts_roundtrip():
    ids = [c.id for c in COMMANDS]
    assert len(ids) == len(set(ids))
    for c in COMMANDS:
        if "-EncodedCommand" in c.command:
            decoded = base64.b64decode(c.command.split()[-1]).decode("utf-16-le")
            assert decoded == c.display
    destructive = {"clear_prefetch", "empty_recycle_bin", "wifi_passwords", "reset_windows_update"}
    assert all(next(c for c in COMMANDS if c.id == i).warning for i in destructive)
