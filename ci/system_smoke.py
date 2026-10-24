"""CI smoke test for CMD Toolkit scripts, the registry editor and service control on real Windows."""

import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from ci/: make the package importable

from pcoptimizer import services  # noqa: E402
from pcoptimizer.backend import Backend  # noqa: E402
from pcoptimizer.commands import COMMANDS  # noqa: E402
from pcoptimizer.platform_utils import powershell, stream  # noqa: E402
from pcoptimizer.regedit import RegistryEditor  # noqa: E402

failures = []
TEST_KEY = r"Software\PCOptimizerCI"


def check(name, fn):
    start = time.time()
    try:
        print(f"{name}: {fn()}  ({time.time() - start:.0f}s)")
    except Exception:
        failures.append(name)
        print(f"{name}: FAILED")
        traceback.print_exc(file=sys.stdout)


def scripts_parse():
    bad = []
    for c in COMMANDS:
        if not c.display:
            continue
        f = Path(tempfile.mkdtemp()) / f"{c.id}.ps1"
        f.write_text(c.display, encoding="utf-8")
        res = powershell("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                         f"'{f}', [ref]$null, [ref]$e); if ($e) {{ $e | % {{ $_.Message }}; exit 1 }}")
        if res.returncode:
            bad.append(f"{c.id}: {res.stdout.strip()}")
    assert not bad, bad
    return f"{sum(1 for c in COMMANDS if c.display)} PowerShell scripts parse"


def run_safe_commands():
    out = []
    for cid in ("clear_user_temp", "disk_health", "startup_list", "wifi_passwords", "sleep_blockers"):
        c = next(c for c in COMMANDS if c.id == cid)
        lines = []
        code = stream(c.command, lines.append, timeout=300)
        assert code == 0, f"{cid} exit {code}: {lines[-5:]}"
        out.append(f"{cid}: {lines[-1] if lines else '(no output)'}")
    return "\n  " + "\n  ".join(out)


def registry_roundtrip():
    b = Backend()
    ed = RegistryEditor(b.tweaks, export=b._reg_export, path=Path(tempfile.mkdtemp()) / "changes.json")
    reg = b.registry
    reg.set("HKCU", TEST_KEY, "Keep", "original", "SZ")
    reg.set("HKCU", TEST_KEY, "Bin", bytes.fromhex("0102"), "BINARY")
    try:
        a = ed.set_value("HKCU", TEST_KEY, "Keep", "SZ", "changed")
        n = ed.set_value("HKCU", TEST_KEY, "Num", "DWORD", "0xffffffff")
        d = ed.delete_value("HKCU", TEST_KEY, "Bin")
        m = ed.set_value("HKCU", TEST_KEY, "Multi", "MULTI_SZ", "x\ny")
        assert reg.get("HKCU", TEST_KEY, "Keep")[0] == "changed"
        assert reg.get("HKCU", TEST_KEY, "Num") == (0xFFFFFFFF, "DWORD")
        assert reg.get("HKCU", TEST_KEY, "Multi") == (["x", "y"], "MULTI_SZ")
        assert reg.get("HKCU", TEST_KEY, "Bin") is None
        backups = list(ed.backup_dir.glob("*.reg"))
        assert backups, "no .reg backup written"
        for cid in (a, n, d, m):
            assert b.tweaks.revert(cid)
        assert reg.get("HKCU", TEST_KEY, "Keep") == ("original", "SZ")
        assert reg.get("HKCU", TEST_KEY, "Num") is None and reg.get("HKCU", TEST_KEY, "Multi") is None
        assert reg.get("HKCU", TEST_KEY, "Bin")[0] == bytes.fromhex("0102")
        return f"set/delete/undo ok, backup {backups[0].name}"
    finally:
        powershell(f"Remove-Item 'HKCU:\\{TEST_KEY}' -Recurse -ErrorAction SilentlyContinue")


def service_control():
    running = services.running_services()
    assert running, "no running services returned"
    assert any(s.protected for s in running), [s.name for s in running][:20]
    target = "Spooler"
    ok, msg = services.set_running(target, False)
    assert ok, msg
    ok, msg = services.set_running(target, True)
    assert ok, msg
    assert not services.set_running("RpcSs", False)[0]
    return f"{len(running)} running, {sum(s.protected for s in running)} protected; stop/start {target} ok"


def powershell_json_multiline():
    from pcoptimizer.platform_utils import powershell_json

    data = powershell_json("Get-Service | Select-Object -First 3 | ForEach-Object {\n  [pscustomobject]@{ N = $_.Name }\n}\n")
    assert data and len(data) == 3, data
    return "ok"


def boost_roundtrip():
    from pcoptimizer import boost
    from pcoptimizer.tweaks import TELEMETRY_TASKS, TweakManager

    b = Backend()
    b.tweaks = TweakManager(b.registry, b.tweaks.runner, backup_path=Path(tempfile.mkdtemp()) / "backup.json")
    res = boost.run(b, {"speed", "privacy", "services"}, print)
    assert not res.failed, res.failed
    states = b.tweaks.by_id["telemetry_tasks_off"]._states(b.tweaks)
    assert states and not any(states.values()), states
    reverted = b.tweaks.revert_all()
    assert len(reverted) == len(res.applied), (len(reverted), len(res.applied))
    after = b.tweaks.by_id["telemetry_tasks_off"]._states(b.tweaks)
    return (f"{len(res.applied)} applied, {len(res.already)} already, all undone; "
            f"{sum(after.values())}/{len(TELEMETRY_TASKS)} telemetry tasks enabled again")


check("multi-line powershell_json", powershell_json_multiline)
check("PowerShell scripts parse", scripts_parse)
check("safe commands run", run_safe_commands)
check("registry editor round trip", registry_roundtrip)
check("service control", service_control)
check("one-click boost apply + undo", boost_roundtrip)
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
