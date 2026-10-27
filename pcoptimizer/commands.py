"""Curated maintenance commands for the "CMD Toolkit" page."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from .platform_utils import QUIET


@dataclass(frozen=True)
class MaintenanceCommand:
    id: str
    title: str
    description: str
    command: str  # what actually runs (through cmd.exe)
    group: str
    duration: str = ""
    needs_restart: bool = False
    warning: str = ""  # shown in a confirmation prompt before running
    display: str = ""  # what the toolkit shows; defaults to ``command``

    @property
    def shown(self) -> str:
        return self.display or self.command


def ps(id: str, title: str, description: str, script: str, group: str, **kw) -> MaintenanceCommand:
    """A PowerShell command. It runs -EncodedCommand so quotes and $ never clash with cmd.exe."""
    script = script.strip()
    encoded = base64.b64encode((QUIET + script).encode("utf-16-le")).decode()
    return MaintenanceCommand(id, title, description,
                              f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}", group,
                              display=script, **kw)


def _clear_folder(path: str, label: str) -> str:
    """PowerShell that empties a folder (skipping files in use) and reports the space freed."""
    return f"""
$p = {path}
$size = {{ (Get-ChildItem -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum }}
$before = & $size
Get-ChildItem -LiteralPath $p -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
$freed = [math]::Max(0, $before - (& $size))
'{label}: freed {{0:N1}} MB (files in use were skipped)' -f ($freed / 1MB)
"""


WIFI_PASSWORDS = r"""
$profiles = netsh wlan show profiles | Select-String ':\s(.+)$' | ForEach-Object { $_.Matches[0].Groups[1].Value.Trim() }
if (-not $profiles) { 'No saved Wi-Fi networks (or no Wi-Fi adapter).'; return }
foreach ($name in $profiles) {
    $key = netsh wlan show profile name="$name" key=clear | Select-String 'Key Content\s+:\s(.+)$'
    $pw = if ($key) { $key.Matches[0].Groups[1].Value } else { '(none / open network)' }
    '{0,-32} {1}' -f $name, $pw
}
"""

RESET_WINDOWS_UPDATE = r"""
$services = 'wuauserv', 'bits', 'cryptsvc', 'msiserver'
'Stopping Windows Update services...'
$services | ForEach-Object { Stop-Service $_ -Force -ErrorAction SilentlyContinue }
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
foreach ($dir in "$env:windir\SoftwareDistribution", "$env:windir\System32\catroot2") {
    if (Test-Path $dir) { Rename-Item $dir "$dir.old-$stamp" -ErrorAction SilentlyContinue; "Renamed $dir" }
}
'Starting services again...'
$services | ForEach-Object { Start-Service $_ -ErrorAction SilentlyContinue }
'Done. Windows Update will rebuild its cache on the next check (the .old folders can be deleted later).'
"""

THUMBNAIL_CACHE = r"""
'Restarting Explorer to release the cache files...'
Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$dir = "$env:LOCALAPPDATA\Microsoft\Windows\Explorer"
$files = Get-ChildItem $dir -Filter *.db -Force -ErrorAction SilentlyContinue | Where-Object Name -match '^(thumbcache|iconcache)_'
$mb = ($files | Measure-Object Length -Sum).Sum / 1MB
$files | Remove-Item -Force -ErrorAction SilentlyContinue
Start-Process explorer.exe
'Thumbnail and icon cache cleared ({0:N1} MB). Windows rebuilds them as you browse.' -f $mb
"""

DISK_HEALTH = r"""
Get-PhysicalDisk | ForEach-Object {
    $r = $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue
    [pscustomobject]@{
        Disk = $_.FriendlyName; Type = "$($_.MediaType) $($_.BusType)"; Health = $_.HealthStatus
        'Size (GB)' = [math]::Round($_.Size / 1GB); 'Temp (C)' = $r.Temperature; 'Wear (%)' = $r.Wear
        'Power-on hours' = $r.PowerOnHours; 'Read errors' = $r.ReadErrorsTotal
    }
} | Format-Table -AutoSize | Out-String -Width 200
"""

STARTUP_LIST = r"""
Get-CimInstance Win32_StartupCommand | Sort-Object Name |
  Format-Table Name, Location, Command -AutoSize -Wrap | Out-String -Width 200
"""


COMMANDS: list[MaintenanceCommand] = [
    # ------------------------------------------------------------------ Cleanup
    ps("clear_user_temp", "Clear your temp files",
       "Deletes everything in your %TEMP% folder that isn't in use right now.",
       _clear_folder("$env:TEMP", "Your temp files"), "Cleanup"),
    ps("clear_windows_temp", "Clear Windows temp files",
       r"Deletes everything in C:\Windows\Temp that isn't in use right now.",
       _clear_folder('"$env:windir\\Temp"', "Windows temp files"), "Cleanup"),
    ps("clear_prefetch", "Clear Prefetch",
       "Empties Windows' app launch cache. Useful when it's corrupted or after removing lots of programs.",
       _clear_folder('"$env:windir\\Prefetch"', "Prefetch"), "Cleanup",
       warning="Prefetch makes apps start faster. After clearing it, your next app launches and boot will be "
               "a bit slower until Windows rebuilds it. It's not a speed boost by itself."),
    ps("empty_recycle_bin", "Empty the Recycle Bin", "Permanently deletes everything in the Recycle Bin.",
       "Clear-RecycleBin -Force -ErrorAction SilentlyContinue; 'Recycle Bin emptied.'", "Cleanup",
       warning="Everything in the Recycle Bin will be deleted permanently."),
    ps("clear_thumbnails", "Clear thumbnail & icon cache",
       "Fixes wrong or blank thumbnails and icons. Your taskbar disappears for a moment.",
       THUMBNAIL_CACHE, "Cleanup"),
    ps("clear_delivery_opt", "Clear Delivery Optimization cache",
       "Removes update files Windows keeps to share with other PCs.",
       "Delete-DeliveryOptimizationCache -Force; 'Delivery Optimization cache cleared.'", "Cleanup"),
    MaintenanceCommand(
        "wsreset", "Clear Microsoft Store cache",
        "Fixes Store downloads that are stuck or won't start. The Store opens when it's done.",
        "wsreset.exe", "Cleanup"),
    # ------------------------------------------------------------------ Repair
    MaintenanceCommand(
        "sfc", "Repair system files (SFC)",
        "Scans every protected Windows file and replaces corrupted ones. Fixes random crashes and slowdowns.",
        "sfc /scannow", "Repair", "10-20 min"),
    MaintenanceCommand(
        "dism_restore", "Repair Windows image (DISM)",
        "Downloads clean copies of damaged Windows components. Run this before SFC if SFC can't fix everything.",
        "DISM /Online /Cleanup-Image /RestoreHealth", "Repair", "10-30 min"),
    MaintenanceCommand(
        "chkdsk", "Check disk for errors",
        "Online scan of the system drive's file system - no restart needed.",
        "chkdsk %SystemDrive% /scan", "Repair", "2-10 min"),
    ps("reset_windows_update", "Reset Windows Update",
       "Fixes updates that fail, get stuck downloading or show error codes, by rebuilding Windows Update's cache.",
       RESET_WINDOWS_UPDATE, "Repair", duration="1-2 min",
       warning="Your update history list will look empty afterwards (installed updates are not removed)."),
    # ------------------------------------------------------------------ Storage
    MaintenanceCommand(
        "dism_cleanup", "Clean up old Windows updates",
        "Removes superseded update components from WinSxS, often freeing several GB.",
        "DISM /Online /Cleanup-Image /StartComponentCleanup", "Storage", "5-15 min"),
    MaintenanceCommand(
        "optimize", "Optimize drives (TRIM / defrag)",
        "Runs TRIM on SSDs and defragments hard drives - Windows picks the right one automatically.",
        "defrag /C /O /U /V", "Storage", "1-30 min"),
    MaintenanceCommand(
        "cleanmgr", "Windows Disk Cleanup (low-disk mode)",
        "Runs Windows' built-in Disk Cleanup on every category automatically.",
        "cleanmgr /VERYLOWDISK", "Storage"),
    # ------------------------------------------------------------------ Network
    MaintenanceCommand(
        "flushdns", "Flush DNS cache",
        "Clears cached website addresses. Fixes 'site can't be reached' and can lower ping to game servers.",
        "ipconfig /flushdns", "Network"),
    MaintenanceCommand(
        "clear_arp", "Flush DNS and ARP cache",
        "Also forgets the hardware addresses of devices on your network. Fixes 'can't reach the router' issues.",
        "ipconfig /flushdns && arp -d *", "Network"),
    MaintenanceCommand(
        "renew_ip", "Renew IP address",
        "Gets a fresh IP from your router. Your internet will drop for a few seconds.",
        "ipconfig /release && ipconfig /renew", "Network", "~30 s"),
    MaintenanceCommand(
        "winsock", "Reset network stack",
        "Resets Winsock and TCP/IP to defaults. Fixes most internet problems caused by broken VPNs or software.",
        "netsh winsock reset && netsh int ip reset", "Network", needs_restart=True),
    MaintenanceCommand(
        "tcp_autotune", "Enable TCP auto-tuning",
        "Makes sure Windows can scale its network buffers for full download speed.",
        "netsh int tcp set global autotuninglevel=normal", "Network"),
    ps("wifi_passwords", "Show saved Wi-Fi passwords",
       "Lists every Wi-Fi network this PC remembers, with its password.", WIFI_PASSWORDS, "Network",
       warning="Passwords will be shown on screen. Make sure nobody is looking over your shoulder."),
    # ------------------------------------------------------------------ Diagnostics
    ps("disk_health", "Check disk health",
       "Health, temperature, wear and error counts for each drive (what the drive itself reports).",
       DISK_HEALTH, "Diagnostics"),
    MaintenanceCommand(
        "battery_report", "Battery health report (laptops)",
        "Saves an HTML report to your Desktop with battery capacity, wear and usage history.",
        'powercfg /batteryreport /output "%USERPROFILE%\\Desktop\\battery-report.html"', "Diagnostics"),
    MaintenanceCommand(
        "power_report", "Power efficiency report",
        "Analyses power settings for 60 s and writes an HTML report to your Desktop.",
        'powercfg /energy /output "%USERPROFILE%\\Desktop\\energy-report.html"', "Diagnostics", "60 s"),
    MaintenanceCommand(
        "sleep_blockers", "What's keeping the PC awake?",
        "Shows which apps or devices are stopping the screen or PC from sleeping.",
        "powercfg /requests", "Diagnostics"),
    MaintenanceCommand(
        "memory_test", "Test your RAM (Windows Memory Diagnostic)",
        "Opens Windows Memory Diagnostic. Choose 'Restart now' to test RAM for errors (about 15-30 min).",
        "start mdsched.exe", "Diagnostics", needs_restart=True),
    ps("startup_list", "List startup programs",
       "Every program set to start with Windows, and where it's registered.", STARTUP_LIST, "Diagnostics"),
    MaintenanceCommand(
        "system_info", "System summary",
        "Windows version, install date, uptime, RAM, network adapters and installed updates.",
        "systeminfo", "Diagnostics", "~20 s"),
    MaintenanceCommand(
        "activation", "Check Windows activation",
        "Shows whether Windows is activated and when the activation expires.",
        "cscript //nologo %windir%\\System32\\slmgr.vbs /xpr", "Diagnostics"),
    # ------------------------------------------------------------------ System
    MaintenanceCommand(
        "restore_point", "Create a restore point",
        "Saves a snapshot of system settings you can roll back to from Windows Recovery.",
        'powershell -NoProfile -Command "Enable-ComputerRestore -Drive ([IO.Path]::GetPathRoot($env:windir)); '
        "Checkpoint-Computer -Description 'PC Optimizer' -RestorePointType MODIFY_SETTINGS\"",
        "System", "~1 min"),
    MaintenanceCommand(
        "winget_upgrade", "Update all apps (winget)",
        "Upgrades every installed app that winget knows about to its latest version.",
        "winget upgrade --all --accept-source-agreements --accept-package-agreements --silent",
        "System", "5-20 min"),
    MaintenanceCommand(
        "restart_explorer", "Restart Explorer",
        "Restarts the taskbar and desktop. Fixes a frozen or laggy taskbar instantly.",
        "taskkill /f /im explorer.exe && start explorer.exe", "System"),
    MaintenanceCommand(
        "list_plans", "Show power plans",
        "Lists every power plan on this PC (the active one has a *).",
        "powercfg /list", "System"),
]
