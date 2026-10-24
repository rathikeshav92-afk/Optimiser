"""Built-in (Microsoft Store / AppX) app removal, with a way back.

Apps are removed for the current user. Windows keeps a copy of most built-in
apps for new accounts, so "Reinstall" can usually re-register that copy
instantly; otherwise it opens the app's Microsoft Store page.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .platform_utils import app_data_dir, as_list, powershell, powershell_json

# package-name prefix -> (friendly name, group). Groups: "bloat" (pre-ticked), "optional", "protected".
KNOWN: dict[str, tuple[str, str]] = {
    "Microsoft.BingNews": ("News", "bloat"),
    "Microsoft.BingWeather": ("Weather", "optional"),
    "Microsoft.BingSearch": ("Bing Search", "bloat"),
    "Microsoft.GetHelp": ("Get Help", "bloat"),
    "Microsoft.Getstarted": ("Tips", "bloat"),
    "Microsoft.MicrosoftSolitaireCollection": ("Solitaire Collection", "bloat"),
    "Microsoft.MicrosoftOfficeHub": ("Microsoft 365 (Office) app", "bloat"),
    "Microsoft.People": ("People", "bloat"),
    "Microsoft.SkypeApp": ("Skype", "bloat"),
    "Microsoft.WindowsFeedbackHub": ("Feedback Hub", "bloat"),
    "Microsoft.WindowsMaps": ("Maps", "bloat"),
    "Clipchamp.Clipchamp": ("Clipchamp video editor", "bloat"),
    "MicrosoftTeams": ("Teams (personal)", "bloat"),
    "MSTeams": ("Teams (personal)", "bloat"),
    "Microsoft.Todos": ("Microsoft To Do", "bloat"),
    "Microsoft.PowerAutomateDesktop": ("Power Automate", "bloat"),
    "Microsoft.549981C3F5F10": ("Cortana", "bloat"),
    "Microsoft.MixedReality.Portal": ("Mixed Reality Portal", "bloat"),
    "Microsoft.Microsoft3DViewer": ("3D Viewer", "bloat"),
    "Microsoft.MSPaint": ("Paint 3D", "bloat"),
    "Microsoft.Wallet": ("Wallet", "bloat"),
    "Microsoft.Messaging": ("Messaging", "bloat"),
    "Microsoft.OneConnect": ("Mobile Plans", "bloat"),
    "Microsoft.Office.OneNote": ("OneNote (Store version)", "optional"),
    "Microsoft.ZuneMusic": ("Media Player", "optional"),
    "Microsoft.ZuneVideo": ("Movies & TV", "optional"),
    "Microsoft.YourPhone": ("Phone Link", "optional"),
    "Microsoft.WindowsAlarms": ("Clock & Alarms", "optional"),
    "Microsoft.WindowsSoundRecorder": ("Sound Recorder", "optional"),
    "Microsoft.WindowsCamera": ("Camera", "optional"),
    "Microsoft.MicrosoftStickyNotes": ("Sticky Notes", "optional"),
    "microsoft.windowscommunicationsapps": ("Mail and Calendar", "optional"),
    "Microsoft.OutlookForWindows": ("Outlook (new)", "optional"),
    "Microsoft.Copilot": ("Copilot", "optional"),
    "Microsoft.Windows.DevHome": ("Dev Home", "optional"),
    "MicrosoftCorporationII.QuickAssist": ("Quick Assist", "optional"),
    "MicrosoftWindows.Client.WebExperience": ("Widgets", "optional"),
    "Microsoft.GamingApp": ("Xbox app", "optional"),
    "Microsoft.XboxApp": ("Xbox Console Companion", "optional"),
    "Microsoft.XboxGamingOverlay": ("Xbox Game Bar", "optional"),
    "Microsoft.XboxGameOverlay": ("Xbox Game Bar plug-in", "optional"),
    "Microsoft.XboxSpeechToTextOverlay": ("Xbox speech-to-text", "optional"),
    "Microsoft.Xbox.TCUI": ("Xbox TCUI", "optional"),
    "Microsoft.XboxIdentityProvider": ("Xbox Identity Provider", "optional"),
    # Never removed here: Windows or other apps depend on them.
    "Microsoft.WindowsStore": ("Microsoft Store", "protected"),
    "Microsoft.StorePurchaseApp": ("Store purchases", "protected"),
    "Microsoft.DesktopAppInstaller": ("App Installer (winget)", "protected"),
    "Microsoft.WindowsCalculator": ("Calculator", "protected"),
    "Microsoft.Windows.Photos": ("Photos", "protected"),
    "Microsoft.WindowsNotepad": ("Notepad", "protected"),
    "Microsoft.WindowsTerminal": ("Terminal", "protected"),
    "Microsoft.Paint": ("Paint", "protected"),
    "Microsoft.ScreenSketch": ("Snipping Tool", "protected"),
    "Microsoft.SecHealthUI": ("Windows Security", "protected"),
    "Microsoft.HEIFImageExtension": ("HEIF image support", "protected"),
    "Microsoft.HEVCVideoExtension": ("HEVC video support", "protected"),
    "Microsoft.VP9VideoExtensions": ("VP9 video support", "protected"),
    "Microsoft.WebMediaExtensions": ("Web media support", "protected"),
    "Microsoft.WebpImageExtension": ("WebP image support", "protected"),
    "Microsoft.RawImageExtension": ("RAW image support", "protected"),
    "Microsoft.AV1VideoExtension": ("AV1 video support", "protected"),
    "Microsoft.MPEG2VideoExtension": ("MPEG-2 video support", "protected"),
    "Microsoft.MicrosoftEdge": ("Microsoft Edge", "protected"),
    "Microsoft.MicrosoftEdge.Stable": ("Microsoft Edge", "protected"),
    "Microsoft.MicrosoftEdgeDevToolsClient": ("Edge DevTools", "protected"),
    "Microsoft.WindowsAppRuntime": ("Windows App Runtime", "protected"),
    "Microsoft.Winget.Source": ("winget package source", "protected"),
    "Microsoft.LanguageExperiencePack": ("Windows display language", "protected"),
    "MicrosoftWindows.CrossDevice": ("Cross-device experience", "protected"),
    "MicrosoftCorporationII.WindowsSubsystemForLinux": ("Windows Subsystem for Linux", "optional"),
    "Microsoft.Windows.Ai.Copilot.Provider": ("Copilot provider", "protected"),
    "NVIDIACorp.NVIDIAControlPanel": ("NVIDIA Control Panel", "protected"),
    "AdvancedMicroDevicesInc-2.AMDRadeonSoftware": ("AMD Radeon Software", "protected"),
    "AppUp.IntelGraphicsExperience": ("Intel Graphics Command Center", "protected"),
    "RealtekSemiconductorCorp.RealtekAudioControl": ("Realtek Audio Console", "protected"),
}

# Third-party apps Windows installs as promotions - always safe to remove.
PROMOTED = re.compile(r"(king\.com|CandyCrush|Disney|Spotify|TikTok|Facebook|Instagram|Netflix|Twitter|LinkedIn|"
                      r"AmazonPrimeVideo|PrimeVideo|BytedancePte|Duolingo|Picsart|Flipboard|HuluLLC|"
                      r"AdobeSystemsIncorporated\.AdobePhotoshopExpress|McAfee|Booking)", re.I)

WARNINGS = {
    "Microsoft.GamingApp": "Needed for PC Game Pass and Xbox app games.",
    "Microsoft.XboxGamingOverlay": "Game Bar: needed for Win+G screenshots/recording and some game overlays.",
    "Microsoft.XboxIdentityProvider": "Needed to sign in to Xbox in Game Pass / Microsoft Store games.",
    "Microsoft.Xbox.TCUI": "Used by Xbox sign-in and friend lists in games.",
    "Microsoft.ZuneMusic": "This is Windows 11's Media Player for music and video files.",
    "Microsoft.YourPhone": "Connects your Android/iPhone to the PC.",
    "MicrosoftWindows.Client.WebExperience": "Also used by some lock-screen and taskbar features.",
}

LIST_SCRIPT = r"""
Get-AppxPackage -ErrorAction SilentlyContinue | Where-Object { -not $_.IsFramework -and -not $_.NonRemovable -and
    $_.SignatureKind -ne 'System' } | ForEach-Object {
    [pscustomobject]@{ Name = $_.Name; Full = $_.PackageFullName; Family = $_.PackageFamilyName
                       Publisher = $_.Publisher }
}
"""


@dataclass
class App:
    name: str        # package name, e.g. Microsoft.BingNews
    full_name: str   # PackageFullName, used for removal
    family: str      # PackageFamilyName, used for the Store link
    friendly: str
    group: str       # bloat / optional / protected / other
    warning: str = ""


def classify(name: str) -> tuple[str, str]:
    if name.lower().startswith("microsoft.languageexperiencepack"):  # e.g. ...LanguageExperiencePacken-GB
        return "Windows display language", "protected"
    for prefix, (friendly, group) in KNOWN.items():
        if name.lower() == prefix.lower() or name.lower().startswith(prefix.lower() + "."):
            return friendly, group
    if PROMOTED.search(name):
        return _pretty(name), "optional"
    return _pretty(name), "other"


def _pretty(name: str) -> str:
    base = name.rsplit(".", 1)[-1]
    base = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base).replace("_", " ")
    return base.strip() or name


def parse_apps(data) -> list[App]:
    apps = []
    for d in as_list(data):
        if not isinstance(d, dict) or not d.get("Name"):
            continue
        friendly, group = classify(d["Name"])
        warning = next((w for p, w in WARNINGS.items() if d["Name"].lower() == p.lower()), "")
        if not warning and PROMOTED.search(d["Name"]):
            warning = "Installed by Windows as a promotion - remove it unless you use it."
        apps.append(App(d["Name"], d.get("Full") or "", d.get("Family") or "", friendly, group, warning))
    order = {"bloat": 0, "optional": 1, "other": 2, "protected": 3}
    return sorted(apps, key=lambda a: (order[a.group], a.friendly.lower()))


def list_apps() -> list[App]:
    return parse_apps(powershell_json(LIST_SCRIPT, timeout=120))


def _log_path() -> Path:
    return app_data_dir() / "removed_apps.json"


def removed(path: Path | None = None) -> dict[str, dict]:
    try:
        return json.loads((path or _log_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _record(name: str, info: dict | None, path: Path | None = None) -> None:
    data = removed(path)
    if info is None:
        data.pop(name, None)
    else:
        data[name] = info
    (path or _log_path()).write_text(json.dumps(data, indent=2), encoding="utf-8")


_SAFE = re.compile(r"^[\w.\-~]+$")


def remove(app: App) -> tuple[bool, str]:
    if app.group == "protected":
        return False, f"{app.friendly} is needed by Windows or other apps"
    if not _SAFE.match(app.full_name):
        return False, "Invalid package name"
    res = powershell(f"Remove-AppxPackage -Package '{app.full_name}' -ErrorAction Stop", timeout=300)
    if res.returncode != 0:
        return False, (res.stderr or res.stdout).strip().splitlines()[0] if (res.stderr or res.stdout) else "failed"
    _record(app.name, {"friendly": app.friendly, "family": app.family})
    return True, ""


def reinstall(name: str) -> tuple[bool, str]:
    """Re-register Windows' stored copy; falls back to opening the Store page."""
    info = removed().get(name, {})
    if not _SAFE.match(name):
        return False, "Invalid package name"
    script = (f"$p = Get-AppxPackage -AllUsers -Name '{name}' -ErrorAction SilentlyContinue | Select-Object -First 1; "
              "if (-not $p) { exit 2 }; "
              "Add-AppxPackage -DisableDevelopmentMode -Register \"$($p.InstallLocation)\\AppxManifest.xml\" "
              "-ErrorAction Stop")
    res = powershell(script, timeout=300)
    if res.returncode == 0:
        _record(name, None)
        return True, "reinstalled"
    family = info.get("family", "")
    if family and _SAFE.match(family):
        powershell(f"Start-Process 'ms-windows-store://pdp/?PFN={family}'", timeout=30)
        return False, "opened its Microsoft Store page - click Get/Install there"
    return False, "not found - search for it in the Microsoft Store"
