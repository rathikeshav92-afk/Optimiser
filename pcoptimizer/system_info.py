"""Collect hardware specs and live usage numbers."""

from __future__ import annotations

import ctypes
import platform
import shutil
from dataclasses import asdict, dataclass, field

from .platform_utils import IS_WINDOWS, as_list, powershell_json

GPU_CLASS_KEY = r"HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"

# A single PowerShell round-trip is far faster than one call per WMI class.
SPECS_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cs  = Get-CimInstance Win32_ComputerSystem
$os  = Get-CimInstance Win32_OperatingSystem
$bb  = Get-CimInstance Win32_BaseBoard
$mem = Get-CimInstance Win32_PhysicalMemory
$gpuVram = @{}
Get-ChildItem '%GPU_KEY%' | ForEach-Object {
    $p = Get-ItemProperty $_.PSPath
    if ($p.DriverDesc -and $p.'HardwareInformation.qwMemorySize') {
        $gpuVram[$p.DriverDesc] = [int64]$p.'HardwareInformation.qwMemorySize'
    }
}
$gpus = Get-CimInstance Win32_VideoController | ForEach-Object {
    $vram = $gpuVram[$_.Name]; if (-not $vram) { $vram = [int64]$_.AdapterRAM }
    [pscustomobject]@{ Name=$_.Name; Driver=$_.DriverVersion; VRAM=$vram;
        Width=$_.CurrentHorizontalResolution; Height=$_.CurrentVerticalResolution;
        Refresh=$_.CurrentRefreshRate }
}
$sysDiskNum = (Get-Partition -DriveLetter $env:SystemDrive[0]).DiskNumber
$disks = Get-PhysicalDisk | ForEach-Object {
    [pscustomobject]@{ Name=$_.FriendlyName; Media=[string]$_.MediaType; Bus=[string]$_.BusType;
        Size=[int64]$_.Size; Health=[string]$_.HealthStatus; IsSystem=("$($_.DeviceId)" -eq "$sysDiskNum") }
}
$sys = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$env:SystemDrive'"
[pscustomobject]@{
    CpuName=$cpu.Name.Trim(); Cores=$cpu.NumberOfCores; Threads=$cpu.NumberOfLogicalProcessors;
    MaxClock=$cpu.MaxClockSpeed;
    Manufacturer=$cs.Manufacturer; Model=$cs.Model; TotalRam=[int64]$cs.TotalPhysicalMemory;
    RamSpeed=($mem | Measure-Object -Property ConfiguredClockSpeed -Maximum).Maximum;
    RamSticks=@($mem).Count;
    OsName=$os.Caption; OsVersion=$os.Version; OsBuild=$os.BuildNumber; LastBoot=$os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm');
    Board=(($bb.Manufacturer + ' ' + $bb.Product).Trim());
    Gpus=@($gpus); Disks=@($disks);
    SysDriveFree=[int64]$sys.FreeSpace; SysDriveSize=[int64]$sys.Size
}
""".replace("%GPU_KEY%", GPU_CLASS_KEY)


@dataclass
class Gpu:
    name: str
    driver: str = ""
    vram_bytes: int = 0
    resolution: str = ""
    refresh_hz: int = 0


@dataclass
class Disk:
    name: str
    media: str = "Unknown"  # SSD / HDD / Unspecified
    bus: str = ""  # NVMe / SATA / USB ...
    size_bytes: int = 0
    health: str = ""
    is_system: bool = False

    @property
    def kind(self) -> str:
        if self.bus.upper() == "NVME":
            return "NVMe SSD"
        if self.media.upper() == "SSD":
            return "SSD"
        if self.media.upper() == "HDD":
            return "HDD"
        return self.media or "Unknown"


@dataclass
class SystemSpecs:
    cpu_name: str = "Unknown CPU"
    cores: int = 0
    threads: int = 0
    max_clock_mhz: int = 0
    total_ram_bytes: int = 0
    ram_speed_mhz: int = 0
    ram_sticks: int = 0
    manufacturer: str = ""
    model: str = ""
    motherboard: str = ""
    os_name: str = platform.system()
    os_version: str = platform.version()
    os_build: str = ""
    last_boot: str = ""
    gpus: list[Gpu] = field(default_factory=list)
    disks: list[Disk] = field(default_factory=list)
    sys_drive_free: int = 0
    sys_drive_size: int = 0

    @property
    def primary_gpu(self) -> Gpu | None:
        """The discrete GPU if there is one, otherwise whatever we found."""
        if not self.gpus:
            return None
        return max(self.gpus, key=lambda g: (not _is_integrated(g.name), g.vram_bytes))

    @property
    def system_disk(self) -> Disk | None:
        return next((d for d in self.disks if d.is_system), self.disks[0] if self.disks else None)

    def to_dict(self) -> dict:
        return asdict(self)


def _is_integrated(name: str) -> bool:
    n = name.lower()
    if "microsoft basic" in n or "remote" in n or "virtual" in n:
        return True
    return ("intel" in n and "arc" not in n) or ("radeon" in n and "graphics" in n and "rx" not in n) or "vega" in n


def gb(n_bytes: int, digits: int = 1) -> str:
    return f"{n_bytes / 1024 ** 3:.{digits}f} GB"


def collect_specs() -> SystemSpecs:
    if not IS_WINDOWS:
        return _fallback_specs()
    data = powershell_json(SPECS_SCRIPT, timeout=60)
    if not isinstance(data, dict):
        return _fallback_specs()
    gpus = [
        Gpu(
            name=g.get("Name") or "Unknown GPU",
            driver=g.get("Driver") or "",
            vram_bytes=int(g.get("VRAM") or 0),
            resolution=f"{g['Width']}x{g['Height']}" if g.get("Width") else "",
            refresh_hz=int(g.get("Refresh") or 0),
        )
        for g in as_list(data.get("Gpus"))
    ]
    disks = [
        Disk(
            name=d.get("Name") or "Disk",
            media=d.get("Media") or "Unknown",
            bus=d.get("Bus") or "",
            size_bytes=int(d.get("Size") or 0),
            health=d.get("Health") or "",
            is_system=bool(d.get("IsSystem")),
        )
        for d in as_list(data.get("Disks"))
    ]
    return SystemSpecs(
        cpu_name=data.get("CpuName") or "Unknown CPU",
        cores=int(data.get("Cores") or 0),
        threads=int(data.get("Threads") or 0),
        max_clock_mhz=int(data.get("MaxClock") or 0),
        total_ram_bytes=int(data.get("TotalRam") or 0),
        ram_speed_mhz=int(data.get("RamSpeed") or 0),
        ram_sticks=int(data.get("RamSticks") or 0),
        manufacturer=data.get("Manufacturer") or "",
        model=data.get("Model") or "",
        motherboard=data.get("Board") or "",
        os_name=data.get("OsName") or "Windows",
        os_version=data.get("OsVersion") or "",
        os_build=str(data.get("OsBuild") or ""),
        last_boot=data.get("LastBoot") or "",
        gpus=gpus,
        disks=disks,
        sys_drive_free=int(data.get("SysDriveFree") or 0),
        sys_drive_size=int(data.get("SysDriveSize") or 0),
    )


def _fallback_specs() -> SystemSpecs:
    import os

    usage = shutil.disk_usage(os.path.abspath(os.sep))
    return SystemSpecs(
        cpu_name=platform.processor() or "Unknown CPU",
        cores=os.cpu_count() or 0,
        threads=os.cpu_count() or 0,
        total_ram_bytes=memory_status()[0],
        sys_drive_free=usage.free,
        sys_drive_size=usage.total,
    )


def demo_specs() -> SystemSpecs:
    """Realistic sample data used by --demo so the UI can be previewed off Windows."""
    return SystemSpecs(
        cpu_name="AMD Ryzen 7 5800X 8-Core Processor",
        cores=8,
        threads=16,
        max_clock_mhz=3801,
        total_ram_bytes=16 * 1024 ** 3,
        ram_speed_mhz=3200,
        ram_sticks=2,
        manufacturer="Custom",
        model="Desktop PC",
        motherboard="ASUSTeK TUF GAMING B550-PLUS",
        os_name="Microsoft Windows 11 Home",
        os_version="10.0.22631",
        os_build="22631",
        last_boot="2026-10-22 09:14",
        gpus=[Gpu("NVIDIA GeForce RTX 3060", "32.0.15.6094", 12 * 1024 ** 3, "1920x1080", 144)],
        disks=[
            Disk("Samsung SSD 980 1TB", "SSD", "NVMe", 1000 * 1000 ** 3, "Healthy", is_system=True),
            Disk("ST2000DM008", "HDD", "SATA", 2000 * 1000 ** 3, "Healthy"),
        ],
        sys_drive_free=212 * 1024 ** 3,
        sys_drive_size=930 * 1024 ** 3,
    )


# ---------------------------------------------------------------- live usage


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def memory_status() -> tuple[int, int]:
    """(total_bytes, available_bytes) of physical RAM."""
    if IS_WINDOWS:
        stat = _MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(stat)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys, stat.ullAvailPhys
    try:
        info = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, val = line.split(":", 1)
                info[key] = int(val.split()[0]) * 1024
        return info.get("MemTotal", 0), info.get("MemAvailable", 0)
    except OSError:
        return 0, 0


class CpuMeter:
    """Computes total CPU utilisation between successive calls to sample()."""

    def __init__(self):
        self._last = self._times()

    @staticmethod
    def _times() -> tuple[int, int]:
        """(idle, total) ticks."""
        if IS_WINDOWS:
            idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
            # Kernel time already includes idle time.
            return idle.value, kernel.value + user.value
        try:
            with open("/proc/stat") as fh:
                parts = [int(x) for x in fh.readline().split()[1:]]
            return parts[3] + parts[4], sum(parts)
        except (OSError, IndexError, ValueError):
            return 0, 0

    def sample(self) -> float:
        idle, total = self._times()
        d_idle, d_total = idle - self._last[0], total - self._last[1]
        self._last = (idle, total)
        if d_total <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total)))
