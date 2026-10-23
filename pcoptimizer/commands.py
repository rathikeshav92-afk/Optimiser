"""Curated maintenance commands for the "CMD Toolkit" page."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MaintenanceCommand:
    id: str
    title: str
    description: str
    command: str
    group: str
    duration: str = ""
    needs_restart: bool = False


COMMANDS: list[MaintenanceCommand] = [
    # Repair
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
    # Storage
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
    # Network
    MaintenanceCommand(
        "flushdns", "Flush DNS cache",
        "Clears cached website addresses. Fixes 'site can't be reached' and can lower ping to game servers.",
        "ipconfig /flushdns", "Network"),
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
    # System
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
        "power_report", "Battery / power efficiency report",
        "Analyses power settings for 60 s and writes an HTML report to your Desktop.",
        'powercfg /energy /output "%USERPROFILE%\\Desktop\\energy-report.html"', "System", "60 s"),
    MaintenanceCommand(
        "list_plans", "Show power plans",
        "Lists every power plan on this PC (the active one has a *).",
        "powercfg /list", "System"),
]
