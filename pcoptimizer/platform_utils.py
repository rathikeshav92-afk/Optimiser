"""Thin wrappers around Windows-specific process and privilege APIs.

Everything here degrades gracefully on non-Windows systems so the rest of the
code base (and the test-suite) can be imported anywhere.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Iterable

IS_WINDOWS = sys.platform == "win32"

# Hide the console window that would otherwise flash for every child process.
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def app_data_dir() -> Path:
    """Folder where backups and logs are stored."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "PCOptimizer"
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_admin() -> bool:
    if not IS_WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin() -> bool:
    """Re-start this program with a UAC prompt. Returns True if a new process was launched."""
    if not IS_WINDOWS:
        return False
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, subprocess.list2cmdline(sys.argv[1:])
    else:
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        if not Path(exe).exists():
            exe = sys.executable
        params = subprocess.list2cmdline(["-m", "pcoptimizer", *sys.argv[1:]])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, os.getcwd(), 1)
    return rc > 32


def run(cmd: str | list[str], timeout: float | None = 120, shell: bool = False) -> subprocess.CompletedProcess:
    """Run a command and capture its output (never raises on non-zero exit)."""
    try:
        return subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr=str(exc))


def powershell(script: str, timeout: float | None = 120) -> subprocess.CompletedProcess:
    return run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        timeout=timeout,
    )


def powershell_json(script: str, timeout: float | None = 120):
    """Run a PowerShell snippet whose output is piped to ConvertTo-Json and parse it."""
    result = powershell(f"{script} | ConvertTo-Json -Depth 4 -Compress", timeout=timeout)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def stream(cmd: str | list[str], on_line: Callable[[str], None], timeout: float | None = None) -> int:
    """Run a command (a string goes through the shell), delivering output line by line. Returns the exit code."""
    try:
        proc = subprocess.Popen(
            cmd,
            shell=isinstance(cmd, str),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="oem" if IS_WINDOWS else "utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, ValueError) as exc:
        on_line(f"Failed to start: {exc}")
        return -1
    assert proc.stdout is not None
    killer = threading.Timer(timeout, proc.kill) if timeout else None
    if killer:
        killer.daemon = True
        killer.start()
    buf = ""
    # Read char-by-char so tools that redraw progress with \r (sfc, DISM) still update.
    while True:
        ch = proc.stdout.read(1)
        if not ch:
            break
        if ch in "\r\n":
            _emit(buf, on_line)
            buf = ""
        elif ch != "\x00":  # sfc/DISM write UTF-16 when piped; dropping NULs recovers the text
            buf += ch
    _emit(buf, on_line)
    code = proc.wait()
    if killer:
        killer.cancel()
    return code


def _emit(line: str, on_line: Callable[[str], None]) -> None:
    line = line.replace("\ufeff", "").rstrip()
    if line.strip():
        on_line(line)


def as_list(value) -> list:
    """PowerShell's ConvertTo-Json returns a bare object for one result and a list for many."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def first(items: Iterable, default=None):
    return next(iter(items), default)
