"""Fast DNS: switch the active network adapters to a public DNS provider, measure, and undo."""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from .platform_utils import app_data_dir, as_list, powershell, powershell_json

PROVIDERS = {
    "auto": ("Automatic (your internet provider)", []),
    "cloudflare": ("Cloudflare", ["1.1.1.1", "1.0.0.1"]),
    "google": ("Google", ["8.8.8.8", "8.8.4.4"]),
    "quad9": ("Quad9 (blocks malicious sites)", ["9.9.9.9", "149.112.112.112"]),
}
TEST_DOMAINS = ["www.google.com", "www.youtube.com", "store.steampowered.com", "www.microsoft.com"]
_IP_RE = re.compile(r"^[0-9a-fA-F:.]{2,45}$")

ADAPTERS_SCRIPT = r"""
# Only adapters with an IPv4 default gateway are really connected. Virtual or unplugged adapters
# that merely report "Up" have no DNS client entry and can't be changed.
$connected = @(Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object { $_.IPv4DefaultGateway } |
               ForEach-Object { $_.InterfaceIndex })
Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' -and $connected -contains $_.ifIndex } |
  ForEach-Object {
    $client = Get-DnsClientServerAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
    if (-not $client) { return }
    $dns = $client.ServerAddresses
    $static = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces\$($_.InterfaceGuid)" -ErrorAction SilentlyContinue).NameServer
    [pscustomobject]@{ Index = $_.ifIndex; Name = $_.Name; Description = $_.InterfaceDescription; Dns = @($dns)
                       Static = [string]$static }
}
"""


@dataclass
class Adapter:
    index: int
    name: str
    description: str
    dns: list[str]
    static: list[str]  # servers typed in by hand; empty = automatic from the router


def parse_adapters(data) -> list[Adapter]:
    return [Adapter(int(a.get("Index") or 0), a.get("Name") or "", a.get("Description") or "",
                    [d for d in as_list(a.get("Dns")) if d],
                    [x for x in re.split(r"[ ,]+", a.get("Static") or "") if _IP_RE.match(x)])
            for a in as_list(data) if isinstance(a, dict) and a.get("Index")]


def current_provider(adapters: list[Adapter]) -> str:
    """Which provider the adapters use: a PROVIDERS key, 'custom' or 'auto' (from DHCP/router)."""
    if not adapters or not adapters[0].static:
        return "auto"
    for key, (_, servers) in PROVIDERS.items():
        if servers and adapters[0].static[:1] == servers[:1]:
            return key
    return "custom"


def list_adapters() -> list[Adapter]:
    return parse_adapters(powershell_json(ADAPTERS_SCRIPT, timeout=60))


def _backup_path() -> Path:
    return app_data_dir() / "dns_backup.json"


def load_backup() -> dict[str, list[str]]:
    try:
        return json.loads(_backup_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_provider(provider: str, adapters: list[Adapter]) -> list[str]:
    """Apply a provider to every active adapter. Returns log lines."""
    if provider not in PROVIDERS:
        raise ValueError(provider)
    servers = PROVIDERS[provider][1]
    backup = load_backup()
    log = []
    for a in adapters:
        key = str(a.index)
        if provider == "auto":
            original = backup.pop(key, [])
            if original:  # the user had their own servers before - put those back
                cmd = f"Set-DnsClientServerAddress -InterfaceIndex {a.index} -ServerAddresses " + \
                      ",".join(f"'{s}'" for s in original if _IP_RE.match(s))
            else:
                cmd = f"Set-DnsClientServerAddress -InterfaceIndex {a.index} -ResetServerAddresses"
        else:
            if key not in backup:
                backup[key] = a.static
            cmd = f"Set-DnsClientServerAddress -InterfaceIndex {a.index} -ServerAddresses " + \
                  ",".join(f"'{s}'" for s in servers)
        res = powershell(cmd + " -ErrorAction Stop; Clear-DnsClientCache", timeout=60)
        log.append(f"{'[OK]' if res.returncode == 0 else '[FAILED]'} {a.name}: "
                   f"{PROVIDERS[provider][0]}{'' if res.returncode == 0 else ' - ' + res.stderr.strip()[:200]}")
    path = _backup_path()
    path.write_text(json.dumps(backup), encoding="utf-8")
    return log


def measure(servers: list[str], domains: list[str] = TEST_DOMAINS) -> float | None:
    """Median lookup time in ms against these servers (or the current ones when empty)."""
    target = f"-Server '{servers[0]}'" if servers else ""
    script = ("$t = foreach ($d in @(" + ",".join(f"'{d}'" for d in domains) + ")) { "
              f"(Measure-Command {{ Resolve-DnsName $d -Type A -DnsOnly -NoHostsFile {target} -QuickTimeout "
              "-ErrorAction Stop | Out-Null }).TotalMilliseconds }; $t -join ','")
    res = powershell("Clear-DnsClientCache; " + script, timeout=60)
    try:
        times = [float(x) for x in res.stdout.strip().split(",") if x]
    except ValueError:
        return None
    return round(statistics.median(times), 1) if res.returncode == 0 and times else None
