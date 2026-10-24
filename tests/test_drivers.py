import datetime as dt
from types import SimpleNamespace

import pytest

from pcoptimizer import drivers
from pcoptimizer.ai_assistant import Assistant
from pcoptimizer.backend import DemoBackend

G1 = "8a1e2d4c-1111-4c3b-9f00-000000000001"


def test_parse_updates_skips_bad_ids():
    data = [{"Id": G1, "Title": "Realtek - Audio", "Class": "MEDIA", "Manufacturer": "Realtek",
             "Model": "HD Audio", "Date": "2026-08-14", "Size": 1024},
            {"Id": "not-a-guid", "Title": "x"}]
    ups = drivers.parse_updates(data)
    assert [u.id for u in ups] == [G1] and ups[0].size_bytes == 1024
    # PowerShell returns a bare object for a single result
    assert len(drivers.parse_updates(data[0])) == 1
    assert drivers.parse_updates(None) == []


def test_install_script_only_accepts_guids():
    script = drivers.install_script([G1])
    assert f"$ids = @('{G1}')" in script
    for bad in (["x'); Remove-Item C:\\ -Recurse; ('"], [], [G1, "abc"]):
        with pytest.raises(ValueError):
            drivers.install_script(bad)


def test_parse_install_output():
    lines = ["Downloading 1 driver(s)...", 'RESULT:{"installed":"Realtek - Audio","failed":[],"reboot":true,'
             '"missing":false}']
    res = drivers.parse_install_output(lines)
    assert res.installed == ["Realtek - Audio"] and res.reboot and not res.missing
    with pytest.raises(RuntimeError):
        drivers.parse_install_output(["Exception: 0x80240438"])


def test_installed_driver_age_and_order():
    data = [{"Device": "B", "Date": "2024-01-01"}, {"Device": "A", "Date": "2019-06-01"}, {"Device": "C", "Date": ""}]
    ds = drivers.parse_installed(data)
    assert [d.device for d in ds] == ["C", "A", "B"]
    assert ds[1].age_years(dt.date(2026, 6, 1)) == pytest.approx(7.0, abs=0.01)
    assert ds[0].age_years() is None


def test_problem_devices():
    ps = drivers.parse_problems([{"Name": "Unknown", "Code": 28}, {"Name": "Cam", "Code": 22}, {"Name": "ok", "Code": 0}])
    assert [(p.problem, p.severity) for p in ps] == [("No driver installed", "problem"), ("Disabled", "info")]


@pytest.mark.parametrize("name,vendor", [("NVIDIA GeForce RTX 4070", "NVIDIA"), ("AMD Radeon RX 7800 XT", "AMD"),
                                         ("Intel(R) Arc(TM) A770", "Intel"), ("Microsoft Basic Display", None)])
def test_gpu_download(name, vendor):
    link = drivers.gpu_download(name)
    assert (link[0] if link else None) == vendor


def test_install_drivers_through_backend_runs_powershell():
    calls = []

    def fake_run(cmd, on_line, timeout=None):
        calls.append(cmd)
        on_line("Installing...")
        on_line('RESULT:{"installed":["X"],"failed":[],"reboot":false,"missing":false}')
        return 0

    logged = []
    res = drivers.install_updates([G1], fake_run, logged.append)
    assert res.installed == ["X"] and logged == ["Installing..."]
    assert calls[0][0] == "powershell.exe" and G1 in calls[0][-1]


def test_ai_installs_driver_after_approval(tmp_path):
    b = DemoBackend(data_dir=tmp_path)
    uid = b.DEMO_DRIVER_UPDATES[0].id
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=None)))
    a = Assistant(b, client=client, approve=lambda *_: True)
    out, err = a.run_tool("install_driver_updates", {"ids": [uid], "reason": "Fix audio"})
    assert not err and "Realtek" in out
    assert uid not in {u.id for u in b.search_driver_updates()}
    out, err = a.run_tool("install_driver_updates", {"ids": ["bogus"], "reason": "x"})
    assert err
