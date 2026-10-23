"""Optional Windows services that can be disabled to free RAM and CPU."""

from __future__ import annotations

from dataclasses import dataclass

from .tweaks import RegValue, Tweak

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

    def apply(self, ctx) -> None:
        # Never create a key for a service that isn't installed.
        if not self.is_available(ctx):
            raise RuntimeError("not installed on this PC")
        super().apply(ctx)
        ctx.runner(f'sc stop "{self.service}"')

    def revert(self, ctx, backup) -> None:
        super().revert(ctx, backup)
        original = next((v[3] for v in backup.get("values", []) if v[2] == "Start"), None)
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
        _svc("SysMain", "SysMain (Superfetch)",
             "Preloads frequently used apps into RAM. Can cause constant disk usage on hard drives.",
             warning="On an SSD it makes little difference. On an HDD with enough RAM it can help, so test both."),
    ]
