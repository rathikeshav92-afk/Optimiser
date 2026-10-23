"""Background process listing / ending and startup-app management."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .platform_utils import IS_WINDOWS, run
from .registry import Registry

# Never offer to end these - doing so would crash or log off Windows.
PROTECTED = {
    "system", "idle", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe", "explorer.exe", "memcompression",
    "sihost.exe", "ctfmon.exe", "audiodg.exe", "conhost.exe", "taskhostw.exe", "runtimebroker.exe",
    "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe", "textinputhost.exe",
    "msmpeng.exe", "nissrv.exe", "securityhealthservice.exe", "spoolsv.exe", "lsaiso.exe", "wudfhost.exe",
    "python.exe", "pythonw.exe", "pcoptimizer.exe", "dllhost.exe", "smartscreen.exe", "applicationframehost.exe",
}

# Common apps that are safe to close before a gaming session.
KNOWN_BACKGROUND = {
    "onedrive.exe": "OneDrive sync", "teams.exe": "Microsoft Teams", "ms-teams.exe": "Microsoft Teams",
    "skype.exe": "Skype", "dropbox.exe": "Dropbox", "googledrivefs.exe": "Google Drive",
    "adobeupdateservice.exe": "Adobe updater", "creative cloud.exe": "Adobe Creative Cloud",
    "spotify.exe": "Spotify", "epicgameslauncher.exe": "Epic Games Launcher (idle)",
    "widgets.exe": "Windows Widgets", "phoneexperiencehost.exe": "Phone Link", "yourphone.exe": "Phone Link",
    "msedge.exe": "Microsoft Edge", "chrome.exe": "Google Chrome", "cortana.exe": "Cortana",
    "copilot.exe": "Copilot", "msedgewebview2.exe": "Edge WebView",
}


@dataclass
class ProcInfo:
    name: str
    count: int
    memory_kb: int
    label: str = ""


def list_processes() -> list[ProcInfo]:
    """Running apps grouped by executable name, biggest memory users first."""
    if not IS_WINDOWS:
        return []
    res = run(["tasklist", "/fo", "csv", "/nh"], timeout=30)
    groups: dict[str, ProcInfo] = {}
    for row in csv.reader(io.StringIO(res.stdout)):
        if len(row) < 5:
            continue
        name, mem = row[0], row[4]
        if name.lower() in PROTECTED:
            continue
        kb = int("".join(ch for ch in mem if ch.isdigit()) or 0)
        info = groups.setdefault(name.lower(), ProcInfo(name, 0, 0, KNOWN_BACKGROUND.get(name.lower(), "")))
        info.count += 1
        info.memory_kb += kb
    return sorted(groups.values(), key=lambda p: p.memory_kb, reverse=True)


def end_process(name: str) -> tuple[bool, str]:
    if name.lower() in PROTECTED:
        return False, f"{name} is a protected system process"
    res = run(["taskkill", "/f", "/im", name], timeout=30)
    return res.returncode == 0, (res.stdout or res.stderr).strip()


# --------------------------------------------------------------------------- startup apps

RUN_KEYS = [
    ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
]
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
APPROVED32 = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32"

# Same flag bytes Task Manager writes in StartupApproved.
ENABLED_FLAG = "020000000000000000000000"
DISABLED_FLAG = "030000000000000000000000"


@dataclass
class StartupApp:
    name: str
    command: str
    hive: str
    run_key: str
    enabled: bool

    @property
    def approved_key(self) -> str:
        return APPROVED32 if "WOW6432Node" in self.run_key else APPROVED


def list_startup_apps(reg: Registry) -> list[StartupApp]:
    apps = []
    for hive, key in RUN_KEYS:
        approved = APPROVED32 if "WOW6432Node" in key else APPROVED
        for name, (command, _) in reg.values(hive, key).items():
            flag = reg.get(hive, approved, name)
            # Odd first byte = disabled (Task Manager convention).
            enabled = not (flag and isinstance(flag[0], (bytes, bytearray)) and len(flag[0]) and flag[0][0] & 1)
            apps.append(StartupApp(name, str(command), hive, key, enabled))
    return sorted(apps, key=lambda a: a.name.lower())


def set_startup_enabled(reg: Registry, app: StartupApp, enabled: bool) -> None:
    """Enable/disable exactly like Task Manager does - fully reversible, nothing is deleted."""
    reg.set(app.hive, app.approved_key, app.name, ENABLED_FLAG if enabled else DISABLED_FLAG, "BINARY")
    app.enabled = enabled
