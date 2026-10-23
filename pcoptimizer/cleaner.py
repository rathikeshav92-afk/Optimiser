"""Junk-file scanning/cleaning and RAM trimming."""

from __future__ import annotations

import ctypes
import glob
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .platform_utils import IS_WINDOWS, powershell, run


@dataclass
class CleanTarget:
    id: str
    title: str
    description: str
    patterns: list[str] = field(default_factory=list)  # glob patterns; env vars allowed
    min_age_hours: float = 0  # skip files newer than this (installers may still be using them)
    recommended: bool = True
    special: str = ""  # handled by custom code instead of file globbing


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def build_targets() -> list[CleanTarget]:
    local = _env("LOCALAPPDATA")
    windir = _env("WINDIR", r"C:\Windows")
    return [
        CleanTarget("user_temp", "User temporary files", "Leftovers from apps and installers in %TEMP%.",
                    [os.path.join(_env("TEMP", os.path.join(local, "Temp")), "*")], min_age_hours=2),
        CleanTarget("win_temp", "Windows temporary files", r"System temp folder (C:\Windows\Temp).",
                    [os.path.join(windir, "Temp", "*")], min_age_hours=2),
        CleanTarget("update_cache", "Windows Update download cache",
                    "Already-installed update packages. Windows re-downloads anything it still needs.",
                    [os.path.join(windir, "SoftwareDistribution", "Download", "*")], special="update_cache"),
        CleanTarget("delivery_opt", "Delivery Optimization cache",
                    "Update files Windows keeps to share with other PCs.", special="delivery_opt"),
        CleanTarget("thumbnails", "Thumbnail cache", "Explorer rebuilds thumbnails automatically.",
                    [os.path.join(local, "Microsoft", "Windows", "Explorer", "thumbcache_*.db"),
                     os.path.join(local, "Microsoft", "Windows", "Explorer", "iconcache_*.db")]),
        CleanTarget("crash_dumps", "Crash dumps & error reports", "Memory dumps and Windows Error Reporting files.",
                    [os.path.join(local, "CrashDumps", "*"), os.path.join(windir, "Minidump", "*"),
                     os.path.join(windir, "MEMORY.DMP"),
                     os.path.join(_env("PROGRAMDATA", r"C:\ProgramData"), "Microsoft", "Windows", "WER", "ReportArchive", "*"),
                     os.path.join(_env("PROGRAMDATA", r"C:\ProgramData"), "Microsoft", "Windows", "WER", "ReportQueue", "*")]),
        CleanTarget("browser_cache", "Browser caches (Chrome, Edge, Brave)",
                    "Cached web files - safe, but sites load slightly slower the first time. Close browsers first.",
                    [os.path.join(local, vendor, "User Data", "*", sub, "*")
                     for vendor in (r"Google\Chrome", r"Microsoft\Edge", r"BraveSoftware\Brave-Browser")
                     for sub in ("Cache", "Code Cache", "GPUCache")], recommended=False),
        CleanTarget("logs", "Old Windows log files", r"Setup and CBS logs under C:\Windows\Logs.",
                    [os.path.join(windir, "Logs", "CBS", "*.log"), os.path.join(windir, "Logs", "DISM", "*.log"),
                     os.path.join(windir, "Panther", "*.log")], min_age_hours=24),
        CleanTarget("recycle_bin", "Recycle Bin", "Permanently deletes everything in the Recycle Bin.",
                    recommended=False, special="recycle_bin"),
    ]


def _safe(patterns: list[str]) -> list[str]:
    # A missing env var would turn a pattern into a path relative to the current
    # directory - never delete anything we can't pin to an absolute location.
    return [p for p in patterns if os.path.isabs(p) and len(Path(p).parts) >= 3]


def _iter_files(paths: list[str], min_age_hours: float):
    cutoff = time.time() - min_age_hours * 3600
    for pattern in _safe(paths):
        for entry in glob.glob(pattern):
            p = Path(entry)
            try:
                if p.is_symlink():
                    continue
                if p.is_dir():
                    for root, dirs, files in os.walk(p):
                        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
                        for f in files:
                            fp = os.path.join(root, f)
                            try:
                                st = os.stat(fp)
                            except OSError:
                                continue
                            if st.st_mtime <= cutoff:
                                yield fp, st.st_size
                else:
                    st = p.stat()
                    if st.st_mtime <= cutoff:
                        yield str(p), st.st_size
            except OSError:
                continue


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]


def _recycle_bin_size() -> int:
    if not IS_WINDOWS:
        return 0
    info = _SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(info)
    if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) == 0:
        return info.i64Size
    return 0


def _delivery_opt_size() -> int:
    if not IS_WINDOWS:
        return 0
    res = powershell("(Get-DeliveryOptimizationStatus | Measure-Object -Property FileSizeInCache -Sum).Sum", timeout=30)
    try:
        return int(float(res.stdout.strip() or 0))
    except ValueError:
        return 0


def scan(target: CleanTarget) -> tuple[int, int]:
    """(bytes, file_count) that cleaning would free."""
    if target.special == "recycle_bin":
        return _recycle_bin_size(), 0
    if target.special == "delivery_opt":
        return _delivery_opt_size(), 0
    total = count = 0
    for _, size in _iter_files(target.patterns, target.min_age_hours):
        total += size
        count += 1
    return total, count


def _delete_files(target: CleanTarget, log: Callable[[str], None]) -> tuple[int, int, int]:
    freed = removed = skipped = 0
    for path, size in _iter_files(target.patterns, target.min_age_hours):
        try:
            os.remove(path)
            freed += size
            removed += 1
        except OSError:
            skipped += 1  # file in use - perfectly normal for temp folders
    # Tidy up emptied sub-folders (never the root folders themselves).
    for pattern in _safe(target.patterns):
        for entry in glob.glob(pattern):
            if os.path.isdir(entry) and not os.path.islink(entry):
                for root, dirs, _ in os.walk(entry, topdown=False):
                    for d in dirs:
                        try:
                            os.rmdir(os.path.join(root, d))
                        except OSError:
                            pass
                try:
                    os.rmdir(entry)
                except OSError:
                    pass
    return freed, removed, skipped


def clean(target: CleanTarget, log: Callable[[str], None] = print) -> int:
    """Clean one target, returning bytes freed."""
    if target.special == "recycle_bin":
        size = _recycle_bin_size()
        if IS_WINDOWS:
            # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
            ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x7)
        log(f"Recycle Bin emptied ({human(size)})")
        return size
    if target.special == "delivery_opt":
        size = _delivery_opt_size()
        powershell("Delete-DeliveryOptimizationCache -Force", timeout=120)
        log(f"Delivery Optimization cache cleared ({human(size)})")
        return size
    if target.special == "update_cache":
        log("Stopping Windows Update service...")
        run("net stop wuauserv /y", shell=True, timeout=60)
        run("net stop bits /y", shell=True, timeout=60)
        try:
            freed, removed, skipped = _delete_files(target, log)
        finally:
            run("net start bits", shell=True, timeout=60)
            run("net start wuauserv", shell=True, timeout=60)
    else:
        freed, removed, skipped = _delete_files(target, log)
    note = f", {skipped} in use (skipped)" if skipped else ""
    log(f"{target.title}: removed {removed} files, freed {human(freed)}{note}")
    return freed


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# --------------------------------------------------------------------------- RAM

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_QUOTA = 0x0100


def trim_working_sets() -> int:
    """Ask every process we can open to release unused memory pages.

    Returns the number of processes trimmed. The freed pages go to the standby
    list, so Windows can hand them to the game/app that needs RAM right now.
    """
    if not IS_WINDOWS:
        return 0
    psapi, kernel32 = ctypes.windll.psapi, ctypes.windll.kernel32
    pids = (ctypes.c_ulong * 4096)()
    needed = ctypes.c_ulong()
    if not psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(needed)):
        return 0
    own = os.getpid()
    trimmed = 0
    for pid in pids[: needed.value // ctypes.sizeof(ctypes.c_ulong)]:
        if pid in (0, 4, own):
            continue
        handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid)
        if handle:
            if psapi.EmptyWorkingSet(handle):
                trimmed += 1
            kernel32.CloseHandle(handle)
    return trimmed


def flush_dns() -> bool:
    return run("ipconfig /flushdns", shell=True).returncode == 0


def free_disk_space(path: str | None = None) -> int:
    return shutil.disk_usage(path or os.path.abspath(os.sep)).free
