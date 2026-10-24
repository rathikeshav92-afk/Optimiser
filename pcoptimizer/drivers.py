"""Driver updates: Windows Update driver search/install, installed driver inventory and problem devices.

Driver updates come from Windows Update (the Windows Update Agent COM API), so
every driver offered is signed and certified by Microsoft for this PC. Graphics
drivers are usually newer on the GPU maker's own site, so we link to it as well.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .platform_utils import as_list, powershell, powershell_json

GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
OLD_DRIVER_YEARS = 3
RESULT_PREFIX = "RESULT:"

# --------------------------------------------------------------------------- PowerShell scripts

SEARCH_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$session = New-Object -ComObject Microsoft.Update.Session
$session.ClientApplicationID = 'PC Optimizer'
$searcher = $session.CreateUpdateSearcher()
$searcher.Online = $true
$result = $searcher.Search("IsInstalled=0 and Type='Driver' and IsHidden=0")
$list = @($result.Updates | ForEach-Object {
    [pscustomobject]@{
        Id = $_.Identity.UpdateID; Title = $_.Title; Class = $_.DriverClass
        Manufacturer = $_.DriverManufacturer; Model = $_.DriverModel
        Date = if ($_.DriverVerDate) { $_.DriverVerDate.ToString('yyyy-MM-dd') } else { '' }
        Size = [int64]$_.MaxDownloadSize; Description = $_.Description
    }
})
# Wrap the list: an empty array piped to ConvertTo-Json prints nothing at all.
[pscustomobject]@{ updates = $list } | ConvertTo-Json -Depth 4 -Compress
"""

# Downloads and installs the updates whose IDs are listed in $ids. Prints progress lines,
# then one RESULT:<json> line. %IDS% is replaced with a validated, quoted GUID list.
INSTALL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ids = @(%IDS%)
$session = New-Object -ComObject Microsoft.Update.Session
$session.ClientApplicationID = 'PC Optimizer'
$searcher = $session.CreateUpdateSearcher()
$searcher.Online = $true
Write-Output 'Looking up the selected drivers...'
$found = $searcher.Search("IsInstalled=0 and Type='Driver' and IsHidden=0").Updates
$coll = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $found) {
    if ($ids -contains $u.Identity.UpdateID) {
        if (-not $u.EulaAccepted) { $u.AcceptEula() }
        [void]$coll.Add($u)
    }
}
if ($coll.Count -eq 0) { Write-Output 'RESULT:{"installed":[],"failed":[],"reboot":false,"missing":true}'; exit 0 }
Write-Output "Downloading $($coll.Count) driver(s)..."
$dl = $session.CreateUpdateDownloader(); $dl.Updates = $coll; [void]$dl.Download()
Write-Output 'Installing (this can take a few minutes, your screen may flicker)...'
$inst = $session.CreateUpdateInstaller(); $inst.Updates = $coll
$res = $inst.Install()
$ok = @(); $bad = @()
for ($i = 0; $i -lt $coll.Count; $i++) {
    $code = $res.GetUpdateResult($i).ResultCode
    if ($code -eq 2 -or $code -eq 3) { $ok += $coll.Item($i).Title } else { $bad += $coll.Item($i).Title }
}
$out = [pscustomobject]@{ installed = @($ok); failed = @($bad); reboot = [bool]$res.RebootRequired; missing = $false }
Write-Output ('RESULT:' + ($out | ConvertTo-Json -Compress))
"""

INSTALLED_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_PnPSignedDriver |
  Where-Object { $_.DeviceName -and $_.DriverProviderName -and $_.DriverProviderName -notmatch '^(Microsoft|\(Standard)' } |
  ForEach-Object {
    [pscustomobject]@{ Device = $_.DeviceName; Class = $_.DeviceClass; Provider = $_.DriverProviderName
        Version = $_.DriverVersion; Date = if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') } else { '' }
        Inf = $_.InfName }
  }
"""

PROBLEMS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -and $_.ConfigManagerErrorCode -ne 45 } |
  ForEach-Object { [pscustomobject]@{ Name = $_.Name; Class = $_.PNPClass; Code = [int]$_.ConfigManagerErrorCode
                                      Id = $_.PNPDeviceID } }
"""

PROBLEM_CODES = {
    1: "Not set up correctly", 3: "Driver may be corrupted or low on memory", 10: "Can't start",
    12: "Not enough free resources", 14: "Needs a restart", 18: "Driver needs reinstalling",
    19: "Registry entry is damaged", 21: "Being removed", 22: "Disabled", 24: "Not present or not working",
    28: "No driver installed", 29: "Disabled by firmware", 31: "Not working properly", 32: "Driver is disabled",
    37: "Driver failed to start", 38: "Old driver still loaded - restart", 39: "Driver is corrupted or missing",
    43: "Stopped after reporting a problem", 48: "Blocked: known compatibility problem", 52: "Driver isn't signed",
}

GPU_DOWNLOADS = {
    "nvidia": ("NVIDIA", "https://www.nvidia.com/en-us/drivers/"),
    "amd": ("AMD", "https://www.amd.com/en/support/download/drivers.html"),
    "intel": ("Intel", "https://www.intel.com/content/www/us/en/support/detect.html"),
}


# --------------------------------------------------------------------------- data


@dataclass
class DriverUpdate:
    id: str
    title: str
    device_class: str
    manufacturer: str
    model: str
    date: str
    size_bytes: int


@dataclass
class InstalledDriver:
    device: str
    device_class: str
    provider: str
    version: str
    date: str
    inf: str

    def age_years(self, today: dt.date | None = None) -> float | None:
        try:
            d = dt.date.fromisoformat(self.date)
        except ValueError:
            return None
        return ((today or dt.date.today()) - d).days / 365.25


@dataclass
class ProblemDevice:
    name: str
    device_class: str
    code: int
    device_id: str

    @property
    def problem(self) -> str:
        return PROBLEM_CODES.get(self.code, f"Problem code {self.code}")

    @property
    def severity(self) -> str:
        return "info" if self.code in (22, 29) else "problem"  # disabled on purpose isn't a fault


@dataclass
class InstallResult:
    installed: list[str]
    failed: list[str]
    reboot: bool
    missing: bool


# --------------------------------------------------------------------------- parsing (pure, unit-tested)


def parse_updates(data) -> list[DriverUpdate]:
    out = []
    for u in as_list(data):
        if not isinstance(u, dict) or not GUID_RE.match(str(u.get("Id", ""))):
            continue
        out.append(DriverUpdate(
            id=u["Id"], title=u.get("Title") or "Driver update", device_class=u.get("Class") or "",
            manufacturer=u.get("Manufacturer") or "", model=u.get("Model") or "", date=u.get("Date") or "",
            size_bytes=int(u.get("Size") or 0)))
    return out


def parse_installed(data) -> list[InstalledDriver]:
    drivers = [InstalledDriver(d.get("Device") or "", d.get("Class") or "", d.get("Provider") or "",
                               d.get("Version") or "", d.get("Date") or "", d.get("Inf") or "")
               for d in as_list(data) if isinstance(d, dict)]
    return sorted(drivers, key=lambda d: d.date or "0000")


def parse_problems(data) -> list[ProblemDevice]:
    return [ProblemDevice(p.get("Name") or "Unknown device", p.get("Class") or "", int(p.get("Code") or 0),
                          p.get("Id") or "")
            for p in as_list(data) if isinstance(p, dict) and p.get("Code")]


def parse_install_output(lines: list[str]) -> InstallResult:
    for line in reversed(lines):
        if line.startswith(RESULT_PREFIX):
            data = json.loads(line[len(RESULT_PREFIX):])
            return InstallResult(as_list(data.get("installed")), as_list(data.get("failed")),
                                 bool(data.get("reboot")), bool(data.get("missing")))
    raise RuntimeError("\n".join(lines[-5:]) or "Windows Update didn't report a result")


def install_script(ids: list[str]) -> str:
    bad = [i for i in ids if not GUID_RE.match(i)]
    if bad or not ids:
        raise ValueError(f"Invalid update ids: {bad or ids}")
    return INSTALL_SCRIPT.replace("%IDS%", ",".join(f"'{i}'" for i in ids))


def gpu_download(gpu_name: str) -> tuple[str, str] | None:
    n = gpu_name.lower()
    if any(k in n for k in ("nvidia", "geforce", "quadro", "rtx")):
        return GPU_DOWNLOADS["nvidia"]
    if any(k in n for k in ("amd", "radeon")):
        return GPU_DOWNLOADS["amd"]
    if "intel" in n:
        return GPU_DOWNLOADS["intel"]
    return None


def backup_folder() -> Path:
    return Path.home() / "Documents" / f"PC Optimizer Driver Backup {dt.date.today().isoformat()}"


# --------------------------------------------------------------------------- Windows calls


def parse_search_output(returncode: int, stdout: str, stderr: str) -> list[DriverUpdate]:
    try:
        data = json.loads(stdout.strip().splitlines()[-1]) if returncode == 0 and stdout.strip() else None
    except (ValueError, IndexError):
        data = None
    if not isinstance(data, dict) or "updates" not in data:
        detail = (stderr or stdout).strip().splitlines()
        hint = next((ln.strip() for ln in detail if "0x" in ln or "Exception" in ln), detail[0] if detail else "")
        raise RuntimeError("Windows Update couldn't be checked. Make sure you're online and the Windows Update "
                           f"service isn't disabled.{' Details: ' + hint if hint else ''}")
    return parse_updates(data["updates"])


def search_updates() -> list[DriverUpdate]:
    """Ask Windows Update for applicable driver updates. Usually takes 30 s - 3 min."""
    res = powershell(SEARCH_SCRIPT, timeout=15 * 60)
    return parse_search_output(res.returncode, res.stdout or "", res.stderr or "")


def install_updates(ids: list[str], run_command: Callable, log: Callable[[str], None]) -> InstallResult:
    lines: list[str] = []

    def on_line(line: str) -> None:
        lines.append(line)
        if not line.startswith(RESULT_PREFIX):
            log(line)

    run_command(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                 install_script(ids)], on_line, timeout=60 * 60)
    return parse_install_output(lines)


def installed_drivers() -> list[InstalledDriver]:
    return parse_installed(powershell_json(INSTALLED_SCRIPT, timeout=120))


def problem_devices() -> list[ProblemDevice]:
    return parse_problems(powershell_json(PROBLEMS_SCRIPT, timeout=120))


def backup_command(folder: Path) -> list[str]:
    return ["pnputil.exe", "/export-driver", "*", str(folder)]
