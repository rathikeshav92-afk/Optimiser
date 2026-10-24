"""Optional Windows services that can be disabled to free RAM and CPU."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .tweaks import RegValue, Skipped, Tweak

SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"
AUTOMATIC, MANUAL, DISABLED = 2, 3, 4


@dataclass
class ServiceTweak(Tweak):
    """Set a service's startup type to Disabled and stop it. Undo restores the original startup type."""

    service: str = ""

    @property
    def key(self) -> str:
        return f"{SERVICES_KEY}\\{self.service}"

    def is_available(self, ctx) -> bool:
        return ctx.registry.get("HKLM", self.key, "Start") is not None

    def is_applied(self, ctx) -> bool:
        cur = ctx.registry.get("HKLM", self.key, "Start")
        return cur is not None and cur[0] == DISABLED

    def _sc_config(self, ctx, start_value: int) -> None:
        """Fallback for services whose registry key Windows locks even for administrators."""
        mode = {AUTOMATIC: "auto", MANUAL: "demand", DISABLED: "disabled"}.get(start_value, "demand")
        code, out = ctx.runner(f'sc config "{self.service}" start= {mode}')
        if code != 0:
            if code == 5 or "access is denied" in out.lower():
                raise Skipped("Windows protects this service's settings")
            raise RuntimeError(out.strip() or f"sc config failed ({code})")

    def apply(self, ctx) -> None:
        # Never create a key for a service that isn't installed.
        if not self.is_available(ctx):
            raise RuntimeError("not installed on this PC")
        try:
            super().apply(ctx)
        except PermissionError:
            self._sc_config(ctx, DISABLED)
        ctx.runner(f'sc stop "{self.service}"')

    def revert(self, ctx, backup) -> None:
        original = next((v[3] for v in backup.get("values", []) if v[2] == "Start"), None)
        try:
            super().revert(ctx, backup)
        except PermissionError:
            if original is not None:
                self._sc_config(ctx, original)
        if original == AUTOMATIC:
            ctx.runner(f'sc start "{self.service}"')


def _svc(service, title, description, warning="", recommended=False) -> ServiceTweak:
    return ServiceTweak(
        f"svc_{service.lower()}", title, description, "services",
        [RegValue("HKLM", f"{SERVICES_KEY}\\{service}", "Start", DISABLED)],
        recommended=recommended, warning=warning, service=service,
    )


def build_service_tweaks() -> list[Tweak]:
    return [
        _svc("DiagTrack", "Connected User Experiences and Telemetry",
             "Collects and uploads usage data to Microsoft in the background.", recommended=True),
        _svc("dmwappushservice", "Device Management WAP Push",
             "Routes push messages used for telemetry. Not needed on home PCs.", recommended=True),
        _svc("MapsBroker", "Downloaded Maps Manager",
             "Keeps offline maps for the Maps app up to date.", recommended=True),
        _svc("RetailDemo", "Retail Demo Service",
             "Only used by display PCs in shops.", recommended=True),
        _svc("Fax", "Fax", "Sends and receives faxes.", recommended=True),
        _svc("WerSvc", "Windows Error Reporting",
             "Sends crash reports to Microsoft after an app crashes.", recommended=True),
        _svc("RemoteRegistry", "Remote Registry",
             "Lets other computers edit this PC's registry. Better off for security too.", recommended=True),
        _svc("TrkWks", "Distributed Link Tracking Client",
             "Tracks shortcuts to files moved between network PCs. Not needed at home.", recommended=True),
        _svc("WMPNetworkSvc", "Windows Media Player Network Sharing",
             "Shares your media library with other devices on the network.", recommended=True),
        _svc("AJRouter", "AllJoyn Router", "Smart-home device protocol that's almost never used.", recommended=True),
        _svc("wisvc", "Windows Insider Service", "Only needed if you're in the Windows Insider Program.",
             recommended=True),
        _svc("PhoneSvc", "Phone Service", "Manages telephony state for apps. Not needed on a desktop PC.",
             recommended=True),
        _svc("WpcMonSvc", "Parental Controls", "Enforces Microsoft Family Safety limits.",
             warning="Only disable if nobody uses Microsoft Family Safety on this PC."),
        _svc("SCardSvr", "Smart Card", "Reads smart cards (e.g. some work or ID cards).",
             warning="Only disable if you never use a smart card reader."),
        _svc("Spooler", "Print Spooler", "Needed to print.",
             warning="Only disable if you don't use a printer or 'Print to PDF'."),
        _svc("XblAuthManager", "Xbox Live Auth Manager", "Signs in to Xbox Live.",
             warning="Needed by the Xbox app, Game Pass PC games and Microsoft Store games."),
        _svc("XblGameSave", "Xbox Live Game Save", "Syncs Xbox cloud saves.",
             warning="Needed for cloud saves in Game Pass / Microsoft Store games."),
        _svc("XboxNetApiSvc", "Xbox Live Networking", "Xbox Live multiplayer networking.",
             warning="Needed for multiplayer in Game Pass / Microsoft Store games."),
        _svc("lfsvc", "Geolocation", "Tells apps where your PC is.",
             warning="Weather, Maps and 'Find my device' won't know your location."),
        _svc("WSearch", "Windows Search indexing",
             "Constantly indexes your files to make searching faster. Uses noticeable disk and CPU on slower PCs.",
             warning="Searching in Start and File Explorer gets slower."),
        _svc("bthserv", "Bluetooth Support", "Discovers and connects Bluetooth devices.",
             warning="Bluetooth headphones, controllers, mice and keyboards will stop working."),
        _svc("TermService", "Remote Desktop", "Lets other PCs connect to this one with Remote Desktop.",
             warning="You won't be able to Remote Desktop into this PC."),
        _svc("PcaSvc", "Program Compatibility Assistant", "Watches for old programs with known problems.",
             warning="Old programs won't get automatic compatibility fixes."),
        _svc("SysMain", "SysMain (Superfetch)",
             "Preloads frequently used apps into RAM. Can cause constant disk usage on hard drives.",
             warning="On an SSD it makes little difference. On an HDD with enough RAM it can help, so test both."),
    ]


# Stopping any of these would crash Windows, break networking/security or log you off.
PROTECTED = {s.lower() for s in (
    "RpcSs", "RpcEptMapper", "DcomLaunch", "LSM", "SamSs", "Winmgmt", "EventLog", "PlugPlay", "Power", "ProfSvc",
    "Schedule", "gpsvc", "CryptSvc", "Dhcp", "Dnscache", "nsi", "NlaSvc", "netprofm", "BFE", "mpssvc", "WinDefend",
    "WdNisSvc", "SecurityHealthService", "wscsvc", "SENS", "UserManager", "CoreMessagingRegistrar", "BrokerInfrastructure",
    "SystemEventsBroker", "TimeBrokerSvc", "StateRepository", "AudioSrv", "AudioEndpointBuilder", "Themes",
    "ShellHWDetection", "FontCache", "EventSystem", "LanmanWorkstation", "KeyIso", "VaultSvc", "Appinfo",
    "camsvc", "TokenBroker", "WlanSvc", "Wcmsvc", "WinHttpAutoProxySvc", "DispBrokerDesktopSvc", "TrustedInstaller",
    "msiserver", "sppsvc", "DeviceInstall", "DsmSvc", "iphlpsvc", "LanmanServer", "WpnService", "CDPSvc",
    "TextInputManagementService", "UdkUserSvc", "cbdhsvc", "webthreatdefsvc", "mpsdrv", "Sense",
)}

LIST_RUNNING = r"""
Get-Service -ErrorAction SilentlyContinue | Where-Object Status -eq 'Running' | Sort-Object DisplayName | ForEach-Object {
    [pscustomobject]@{ Name = $_.Name; DisplayName = $_.DisplayName; StartType = [string]$_.StartType
                       CanStop = [bool]$_.CanStop }
}
"""


@dataclass
class RunningService:
    name: str
    display_name: str
    start_type: str
    can_stop: bool

    @property
    def protected(self) -> bool:
        base = self.name.split("_")[0].lower()  # per-user services look like 'cbdhsvc_1a2b3'
        return not self.can_stop or self.name.lower() in PROTECTED or base in PROTECTED


def parse_running(data) -> list[RunningService]:
    from .platform_utils import as_list

    return [RunningService(d.get("Name") or "", d.get("DisplayName") or "", d.get("StartType") or "",
                           bool(d.get("CanStop"))) for d in as_list(data) if isinstance(d, dict) and d.get("Name")]


def running_services() -> list[RunningService]:
    from .platform_utils import powershell_json

    return parse_running(powershell_json(LIST_RUNNING, timeout=60))


_NAME_RE = re.compile(r"^[\w.\-]{1,256}$")


def set_running(name: str, run: bool) -> tuple[bool, str]:
    """Stop (or start) a service right now. Its startup type is not changed."""
    from .platform_utils import powershell

    if not _NAME_RE.match(name):
        return False, "Invalid service name"
    if not run and (name.lower() in PROTECTED or name.split("_")[0].lower() in PROTECTED):
        return False, f"{name} is needed by Windows and can't be stopped here"
    verb = "Start-Service" if run else "Stop-Service -Force"
    res = powershell(f"{verb} -Name '{name}' -ErrorAction Stop", timeout=90)
    return res.returncode == 0, (res.stderr or res.stdout).strip()
