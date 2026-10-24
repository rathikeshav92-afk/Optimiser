"""Monitor refresh rate: read the current and highest supported rate, and switch to it."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from .platform_utils import IS_WINDOWS

ENUM_CURRENT_SETTINGS = -1
DM_DISPLAYFREQUENCY = 0x00400000
DM_PELSWIDTH, DM_PELSHEIGHT = 0x00080000, 0x00100000
CDS_UPDATEREGISTRY, CDS_TEST = 0x01, 0x02
DISP_CHANGE_SUCCESSFUL = 0
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32), ("dmSpecVersion", ctypes.c_ushort), ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort), ("dmDriverExtra", ctypes.c_ushort), ("dmFields", ctypes.c_ulong),
        ("dmPositionX", ctypes.c_long), ("dmPositionY", ctypes.c_long), ("dmDisplayOrientation", ctypes.c_ulong),
        ("dmDisplayFixedOutput", ctypes.c_ulong), ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32), ("dmLogPixels", ctypes.c_ushort), ("dmBitsPerPel", ctypes.c_ulong),
        ("dmPelsWidth", ctypes.c_ulong), ("dmPelsHeight", ctypes.c_ulong), ("dmDisplayFlags", ctypes.c_ulong),
        ("dmDisplayFrequency", ctypes.c_ulong), ("dmICMMethod", ctypes.c_ulong), ("dmICMIntent", ctypes.c_ulong),
        ("dmMediaType", ctypes.c_ulong), ("dmDitherType", ctypes.c_ulong), ("dmReserved1", ctypes.c_ulong),
        ("dmReserved2", ctypes.c_ulong), ("dmPanningWidth", ctypes.c_ulong), ("dmPanningHeight", ctypes.c_ulong),
    ]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("DeviceName", ctypes.c_wchar * 32), ("DeviceString", ctypes.c_wchar * 128),
                ("StateFlags", ctypes.c_ulong), ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128)]


@dataclass
class Monitor:
    device: str       # e.g. \\.\DISPLAY1
    name: str         # graphics adapter description
    width: int
    height: int
    current_hz: int
    max_hz: int       # highest rate offered at the current resolution

    @property
    def below_max(self) -> bool:
        return self.max_hz > self.current_hz + 1  # 143.9 vs 144 style rounding isn't a problem


def best_rate(modes: list[tuple[int, int, int, int]], width: int, height: int, bpp: int) -> int:
    """Highest refresh rate among (w, h, bpp, hz) modes matching the current resolution and colour depth."""
    return max((hz for w, h, b, hz in modes if (w, h, b) == (width, height, bpp)), default=0)


def _devmode() -> DEVMODEW:
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    return dm


def monitors() -> list[Monitor]:
    if not IS_WINDOWS:
        return []
    user32 = ctypes.windll.user32
    out, i = [], 0
    while True:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if not dd.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            continue
        cur = _devmode()
        if not user32.EnumDisplaySettingsW(dd.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(cur)):
            continue
        modes, n = [], 0
        while True:
            dm = _devmode()
            if not user32.EnumDisplaySettingsW(dd.DeviceName, n, ctypes.byref(dm)):
                break
            modes.append((dm.dmPelsWidth, dm.dmPelsHeight, dm.dmBitsPerPel, dm.dmDisplayFrequency))
            n += 1
        out.append(Monitor(dd.DeviceName, dd.DeviceString, cur.dmPelsWidth, cur.dmPelsHeight, cur.dmDisplayFrequency,
                           best_rate(modes, cur.dmPelsWidth, cur.dmPelsHeight, cur.dmBitsPerPel)))
    return out


def set_refresh_rate(device: str, hz: int) -> tuple[bool, str]:
    """Switch a monitor to ``hz`` at its current resolution. Windows tests the mode first."""
    if not IS_WINDOWS:
        return False, "Only works on Windows"
    user32 = ctypes.windll.user32
    dm = _devmode()
    if not user32.EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return False, "Couldn't read the current display mode"
    dm.dmDisplayFrequency = hz
    dm.dmFields = DM_DISPLAYFREQUENCY | DM_PELSWIDTH | DM_PELSHEIGHT
    if user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, CDS_TEST, None) != DISP_CHANGE_SUCCESSFUL:
        return False, f"{hz} Hz isn't accepted by this monitor/cable at the current resolution"
    rc = user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None)
    return rc == DISP_CHANGE_SUCCESSFUL, "" if rc == DISP_CHANGE_SUCCESSFUL else f"Windows refused the change ({rc})"
