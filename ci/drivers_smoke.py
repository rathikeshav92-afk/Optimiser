"""CI smoke test for the Windows-only driver code. Prints a report and exits non-zero on failure."""

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from ci/: make the package importable

from pcoptimizer import drivers  # noqa: E402
from pcoptimizer.platform_utils import powershell, stream  # noqa: E402

failures = []


def check(name, fn):
    start = time.time()
    try:
        print(f"{name}: {fn()}  ({time.time() - start:.0f}s)")
    except Exception:
        failures.append(name)
        print(f"{name}: FAILED")
        traceback.print_exc(file=sys.stdout)


def installed():
    ds = drivers.installed_drivers()
    assert ds, "no third-party drivers found"
    return f"{len(ds)} drivers, oldest: {ds[0].device} {ds[0].date}"


def problems():
    return [(p.name, p.problem) for p in drivers.problem_devices()] or "none"


def search():
    ups = drivers.search_updates()
    return f"{len(ups)} update(s): " + json.dumps([u.title for u in ups[:5]])


def install_script_parses():
    # Parse (never run) the install script to catch PowerShell syntax errors.
    script = drivers.install_script(["8a1e2d4c-1111-4c3b-9f00-000000000001"])
    src = Path(tempfile.mkdtemp()) / "install.ps1"
    src.write_text(script, encoding="utf-8")
    res = powershell(
        "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
        f"'{src}', [ref]$null, [ref]$e); if ($e) {{ $e | ForEach-Object {{ $_.Message }}; exit 1 }}")
    assert res.returncode == 0, res.stdout + res.stderr
    return "ok"


def backup():
    folder = Path(tempfile.mkdtemp()) / "backup"
    folder.mkdir()
    lines = []
    code = stream(drivers.backup_command(folder), lines.append, timeout=900)
    exported = len(list(folder.iterdir()))
    assert code == 0 and exported, f"code {code}, {exported} packages\n" + "\n".join(lines[-5:])
    return f"{exported} driver packages exported"


check("installed drivers", installed)
check("problem devices", problems)
check("install script syntax", install_script_parses)
check("driver backup", backup)
check("windows update driver search", search)
print("FAILURES:", failures or "none")
sys.exit(1 if failures else 0)
