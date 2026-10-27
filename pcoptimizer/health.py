"""PC Health Check: finds the settings and conditions that cost the most performance.

``evaluate`` is pure (facts in, findings out) so it can be tested anywhere;
``gather`` collects the facts on a real Windows PC.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .display import Monitor
from .platform_utils import IS_WINDOWS, powershell, run

OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"


@dataclass
class Finding:
    id: str
    title: str
    status: str
    detail: str
    fix_label: str = ""
    fix: str = ""  # action id understood by the UI, e.g. "bios", "page:cleaner", "refresh:\\.\DISPLAY1:144"


@dataclass
class Facts:
    ram_speed: int = 0
    ram_rated: int = 0
    ram_type: str = ""
    ram_sticks: int = 0
    ram_gb: float = 0
    monitors: list[Monitor] = field(default_factory=list)
    disk_free: int = 0
    disk_size: int = 0
    disk_kind: str = ""
    gpu_name: str = ""
    gpu_driver_date: str = ""
    cpu_temp: float | None = None
    gpu_temp: float | None = None
    startup_enabled: int | None = None
    last_boot: str = ""
    hvci_running: bool | None = None
    rebar: bool | None = None
    today: dt.date = field(default_factory=dt.date.today)


# Highest standard (non-XMP) speed per generation: at or below it, XMP/EXPO is very likely off.
JEDEC_BASE = {"DDR4": 2666, "DDR5": 4800}


def _ram(f: Facts) -> Finding:
    base = JEDEC_BASE.get(f.ram_type)
    if not f.ram_speed:
        return Finding("xmp", "Memory speed (XMP/EXPO)", INFO, "Couldn't read your RAM speed.")
    if base and f.ram_speed <= base:
        return Finding(
            "xmp", "Memory speed (XMP/EXPO)", WARN,
            f"Your {f.ram_type} RAM runs at {f.ram_speed} MHz, the standard speed without XMP/EXPO. Most gaming RAM "
            "is rated faster (e.g. 3200-3600 MHz for DDR4, 6000 MHz for DDR5). Turning on XMP (Intel) or EXPO "
            "(AMD) in the BIOS is often worth 5-15% more FPS. Check your RAM's box or model for its rated speed.",
            "Restart into BIOS", "bios")
    return Finding("xmp", "Memory speed (XMP/EXPO)", OK, f"{f.ram_type or 'RAM'} running at {f.ram_speed} MHz.")


def _channels(f: Facts) -> Finding | None:
    if f.ram_sticks == 1:
        return Finding("dual_channel", "Dual-channel memory", WARN,
                       "You have one RAM stick, so it runs in single-channel mode. A matching second stick doubles "
                       "memory bandwidth - a big boost for integrated graphics and CPU-heavy games.")
    if f.ram_gb and f.ram_gb < 15:
        return Finding("ram_size", "Amount of RAM", INFO,
                       f"{f.ram_gb:.0f} GB of RAM. 16 GB is the comfortable minimum for modern games with a browser open.")
    return None


def _monitors(f: Facts) -> list[Finding]:
    out = []
    for m in f.monitors:
        label = f"Refresh rate ({m.width}x{m.height})"
        if m.below_max:
            out.append(Finding(f"refresh:{m.device}", label, BAD,
                               f"Your monitor runs at {m.current_hz} Hz but supports {m.max_hz} Hz. Games and even "
                               "the mouse pointer will look much smoother at the higher rate.",
                               f"Switch to {m.max_hz} Hz", f"refresh:{m.device}:{m.max_hz}"))
        else:
            out.append(Finding(f"refresh:{m.device}", label, OK, f"Running at its maximum, {m.current_hz} Hz."))
    return out


def _disk(f: Facts) -> list[Finding]:
    out = []
    if f.disk_size:
        pct = f.disk_free / f.disk_size
        free_gb = f.disk_free / 1024 ** 3
        if pct < 0.08:
            out.append(Finding("disk_space", "Free space on system drive", BAD,
                               f"Only {free_gb:.0f} GB ({pct:.0%}) free. Windows and games slow down and updates "
                               "fail when the system drive is nearly full.", "Open Cleaner", "page:cleaner"))
        elif pct < 0.15:
            out.append(Finding("disk_space", "Free space on system drive", WARN,
                               f"{free_gb:.0f} GB ({pct:.0%}) free. Try to keep at least 15% free.",
                               "Open Cleaner", "page:cleaner"))
        else:
            out.append(Finding("disk_space", "Free space on system drive", OK, f"{free_gb:.0f} GB ({pct:.0%}) free."))
    if f.disk_kind == "HDD":
        out.append(Finding("ssd", "System drive type", WARN,
                           "Windows is on a hard disk drive. Moving to an SSD is the single biggest upgrade for "
                           "boot and loading times (often 5-10x faster)."))
    return out


def _gpu_driver(f: Facts) -> Finding | None:
    if not f.gpu_name:
        return None
    try:
        months = (f.today - dt.date.fromisoformat(f.gpu_driver_date)).days // 30
    except ValueError:
        return Finding("gpu_driver", "Graphics driver", INFO, "Couldn't read the graphics driver's date.",
                       "Open Driver Updates", "page:drivers")
    if months >= 6:
        return Finding("gpu_driver", "Graphics driver", WARN,
                       f"Your {f.gpu_name} driver is about {months} months old. New drivers regularly add FPS and "
                       "fix crashes in recent games.", "Open Driver Updates", "page:drivers")
    return Finding("gpu_driver", "Graphics driver", OK, f"Released {f.gpu_driver_date} ({months} months ago).")


def _temps(f: Facts) -> Finding:
    hot = []
    if f.cpu_temp is not None and f.cpu_temp >= 85:
        hot.append(f"CPU {f.cpu_temp:.0f}°C")
    if f.gpu_temp is not None and f.gpu_temp >= 83:
        hot.append(f"GPU {f.gpu_temp:.0f}°C")
    if hot:
        return Finding("temps", "Temperatures", BAD,
                       f"Running hot ({', '.join(hot)}) - the PC will slow itself down to cool off. Clean dust from "
                       "fans and filters and check that case fans are spinning.")
    if f.cpu_temp is None and f.gpu_temp is None:
        return Finding("temps", "Temperatures", INFO, "Your PC doesn't report temperatures to Windows.")
    shown = ", ".join(x for x in (f"CPU {f.cpu_temp:.0f}°C" if f.cpu_temp is not None else "",
                                  f"GPU {f.gpu_temp:.0f}°C" if f.gpu_temp is not None else "") if x)
    return Finding("temps", "Temperatures", OK, f"{shown} right now.")


def _startup(f: Facts) -> Finding | None:
    if f.startup_enabled is None:
        return None
    if f.startup_enabled > 8:
        return Finding("startup", "Startup apps", WARN,
                       f"{f.startup_enabled} apps start with Windows, slowing boot and using RAM in the background.",
                       "Review startup apps", "page:tweaks")
    return Finding("startup", "Startup apps", OK, f"{f.startup_enabled} apps start with Windows.")


def _uptime(f: Facts) -> Finding | None:
    try:
        boot = dt.datetime.strptime(f.last_boot, "%Y-%m-%d %H:%M").date()
    except ValueError:
        return None
    days = (f.today - boot).days
    if days >= 7:
        return Finding("uptime", "Last restart", WARN,
                       f"Your PC hasn't restarted in {days} days. 'Shut down' with Fast Startup doesn't clear memory "
                       "leaks or finish updates - restart about once a week.", "Restart now", "restart")
    return Finding("uptime", "Last restart", OK, f"{days} day{'s' if days != 1 else ''} ago.")


def rebar_capable(gpu_name: str) -> bool:
    """Resizable BAR needs an NVIDIA RTX 30-series or newer, AMD RX 6000 or newer, or Intel Arc card."""
    name = gpu_name.lower()
    nvidia = re.search(r"rtx\s*(\d{2})\d{2}", name)
    if nvidia:
        return int(nvidia.group(1)) >= 30
    amd = re.search(r"rx\s*(\d)\d{3}", name)
    if amd:
        return int(amd.group(1)) >= 6
    return bool(re.search(r"\barc\b", name))


def _rebar(f: Facts) -> Finding | None:
    if f.gpu_name and not rebar_capable(f.gpu_name):
        return None  # e.g. GTX 16-series: the card can't use it, so there's nothing to turn on
    if f.rebar is None:
        if not f.gpu_name or "nvidia" not in f.gpu_name.lower() and "radeon" not in f.gpu_name.lower():
            return None
        return Finding("rebar", "Resizable BAR / Smart Access Memory", INFO,
                       "Couldn't check automatically. If your motherboard and graphics card support it (most from "
                       "2020 on), enable 'Above 4G Decoding' and 'Re-Size BAR' in the BIOS for a few % more FPS.",
                       "Restart into BIOS", "bios")
    if f.rebar:
        return Finding("rebar", "Resizable BAR / Smart Access Memory", OK, "Enabled.")
    return Finding("rebar", "Resizable BAR / Smart Access Memory", WARN,
                   "Off. If your motherboard supports it, enable 'Above 4G Decoding' and 'Re-Size BAR Support' "
                   "in the BIOS - many games gain a few % FPS.", "Restart into BIOS", "bios")


def _hvci(f: Facts) -> Finding | None:
    if f.hvci_running is None:
        return None
    if f.hvci_running:
        return Finding("hvci", "Memory Integrity (Core Isolation)", INFO,
                       "On. It protects against advanced malware but can cost around 5-10% FPS in some games. "
                       "You can choose to turn it off on the Speed Up PC page (optional, lowers protection).",
                       "Open Speed Up PC", "page:tweaks")
    return Finding("hvci", "Memory Integrity (Core Isolation)", OK, "Off - no gaming overhead.")


SEVERITY = {BAD: 0, WARN: 1, INFO: 2, OK: 3}


def evaluate(f: Facts) -> list[Finding]:
    items = [_ram(f), _channels(f), *_monitors(f), *_disk(f), _gpu_driver(f), _temps(f), _startup(f), _uptime(f),
             _rebar(f), _hvci(f)]
    return sorted((i for i in items if i), key=lambda i: SEVERITY[i.status])


def score(findings: list[Finding]) -> tuple[int, int]:
    """(problems, warnings)"""
    return sum(i.status == BAD for i in findings), sum(i.status == WARN for i in findings)


# --------------------------------------------------------------------------- Windows probes


def hvci_running() -> bool | None:
    if not IS_WINDOWS:
        return None
    res = powershell("(Get-CimInstance -Namespace root\\Microsoft\\Windows\\DeviceGuard -ClassName Win32_DeviceGuard "
                     "-ErrorAction Stop).SecurityServicesRunning -contains 2", timeout=60)
    text = res.stdout.strip().lower()
    return {"true": True, "false": False}.get(text)


def parse_rebar(nvsmi_memory: str) -> bool | None:
    """From `nvidia-smi -q -d MEMORY`: BAR1 spanning (almost) all VRAM means Resizable BAR is on."""
    fb = re.search(r"FB Memory Usage\s+Total\s*:\s*(\d+)\s*MiB", nvsmi_memory)
    bar = re.search(r"BAR1 Memory Usage\s+Total\s*:\s*(\d+)\s*MiB", nvsmi_memory)
    if not fb or not bar:
        return None
    return int(bar.group(1)) >= 0.9 * int(fb.group(1))


def rebar_enabled(gpu_name: str) -> bool | None:
    if not IS_WINDOWS or "nvidia" not in gpu_name.lower() and "geforce" not in gpu_name.lower():
        return None
    res = run(["nvidia-smi", "-q", "-d", "MEMORY"], timeout=30)
    return parse_rebar(res.stdout) if res.returncode == 0 else None
