"""Screen time: which apps you actively use, and for how long.

Windows has no public API for its own app-usage history, so PC Optimizer records
it itself. A small background tracker (``--tracker``) starts when you sign in and
checks the foreground window every few seconds. Time is only counted while you are
actually using the PC - not while it is locked, asleep, or idle for 5+ minutes.

Only the app name and the seconds spent in it are stored, in a local SQLite file.
Window titles, URLs and keystrokes are never recorded.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .platform_utils import IS_WINDOWS, app_data_dir, powershell

SAMPLE_SECONDS = 5
FLUSH_SECONDS = 60
IDLE_LIMIT_SECONDS = 5 * 60
TASK_NAME = "PC Optimizer Screen Time"
MUTEX_NAME = "Local\\PCOptimizerScreenTimeTracker"

# Not "apps" the user chose to use: lock screen, task switcher, shell overlays.
IGNORED = {"lockapp.exe", "logonui.exe", "searchhost.exe", "searchapp.exe", "startmenuexperiencehost.exe",
           "shellexperiencehost.exe", "applicationframehost.exe", "textinputhost.exe", "screenclippinghost.exe"}

FRIENDLY = {
    "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "firefox.exe": "Firefox", "brave.exe": "Brave",
    "opera.exe": "Opera", "explorer.exe": "File Explorer", "code.exe": "Visual Studio Code",
    "winword.exe": "Microsoft Word", "excel.exe": "Microsoft Excel", "powerpnt.exe": "PowerPoint",
    "outlook.exe": "Outlook", "olk.exe": "Outlook", "ms-teams.exe": "Microsoft Teams", "teams.exe": "Microsoft Teams",
    "discord.exe": "Discord", "spotify.exe": "Spotify", "steam.exe": "Steam", "steamwebhelper.exe": "Steam",
    "epicgameslauncher.exe": "Epic Games Launcher", "vlc.exe": "VLC", "notepad.exe": "Notepad",
    "windowsterminal.exe": "Windows Terminal", "cmd.exe": "Command Prompt", "powershell.exe": "PowerShell",
    "whatsapp.exe": "WhatsApp", "telegram.exe": "Telegram", "zoom.exe": "Zoom", "obs64.exe": "OBS Studio",
    "pcoptimizer.exe": "PC Optimizer", "pythonw.exe": "PC Optimizer", "systemsettings.exe": "Settings",
    "taskmgr.exe": "Task Manager", "cs2.exe": "Counter-Strike 2", "valorant-win64-shipping.exe": "VALORANT",
    "fortniteclient-win64-shipping.exe": "Fortnite", "robloxplayerbeta.exe": "Roblox",
    "minecraft.windows.exe": "Minecraft", "javaw.exe": "Minecraft (Java)", "gta5.exe": "GTA V",
    "r5apex.exe": "Apex Legends", "leagueclientux.exe": "League of Legends", "league of legends.exe": "League of Legends",
}


# --------------------------------------------------------------------------- storage


def db_path() -> Path:
    return app_data_dir() / "screentime.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or db_path()), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")  # the UI reads while the tracker writes
    conn.execute("""CREATE TABLE IF NOT EXISTS usage (
        day TEXT NOT NULL, hour INTEGER NOT NULL, app TEXT NOT NULL, seconds INTEGER NOT NULL,
        PRIMARY KEY (day, hour, app))""")
    conn.execute("CREATE TABLE IF NOT EXISTS apps (app TEXT PRIMARY KEY, name TEXT NOT NULL)")
    return conn


def record(conn: sqlite3.Connection, buckets: dict[tuple[str, int, str], float], names: dict[str, str]) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO usage(day, hour, app, seconds) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(day, hour, app) DO UPDATE SET seconds = seconds + excluded.seconds",
            [(d, h, a, round(s)) for (d, h, a), s in buckets.items() if s >= 0.5],
        )
        conn.executemany("INSERT OR REPLACE INTO apps(app, name) VALUES (?, ?)", names.items())


def clear(path: Path | None = None) -> None:
    with closing(connect(path)) as conn, conn:
        conn.execute("DELETE FROM usage")


# --------------------------------------------------------------------------- reporting

PERIODS = ("day", "week", "month")


@dataclass
class AppUsage:
    app: str
    name: str
    seconds: int


@dataclass
class Report:
    period: str
    start: dt.date
    end: dt.date  # inclusive
    label: str
    total: int
    apps: list[AppUsage]
    # Chart: (label, seconds) per hour for a day, per day for a week/month.
    series: list[tuple[str, int]]
    previous_total: int
    days_with_data: int

    @property
    def daily_average(self) -> int:
        return self.total // max(1, self.days_with_data)


def period_range(period: str, anchor: dt.date) -> tuple[dt.date, dt.date, str]:
    if period == "day":
        today = dt.date.today()
        label = "Today" if anchor == today else "Yesterday" if anchor == today - dt.timedelta(1) else \
            anchor.strftime("%a %d %b %Y")
        return anchor, anchor, label
    if period == "week":
        start = anchor - dt.timedelta(days=anchor.weekday())
        end = start + dt.timedelta(days=6)
        return start, end, f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"
    if period == "month":
        start = anchor.replace(day=1)
        end = (start + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        return start, end, start.strftime("%B %Y")
    raise ValueError(period)


def shift(period: str, anchor: dt.date, steps: int) -> dt.date:
    if period == "day":
        return anchor + dt.timedelta(days=steps)
    if period == "week":
        return anchor + dt.timedelta(weeks=steps)
    month = anchor.year * 12 + anchor.month - 1 + steps
    return dt.date(month // 12, month % 12 + 1, 1)


def _total(conn, start: dt.date, end: dt.date) -> int:
    row = conn.execute("SELECT COALESCE(SUM(seconds), 0) FROM usage WHERE day BETWEEN ? AND ?",
                       (start.isoformat(), end.isoformat())).fetchone()
    return int(row[0])


def report(period: str, anchor: dt.date | None = None, path: Path | None = None) -> Report:
    anchor = anchor or dt.date.today()
    start, end, label = period_range(period, anchor)
    s, e = start.isoformat(), end.isoformat()
    with closing(connect(path)) as conn:
        apps = [AppUsage(a, n or pretty_name(a), int(sec)) for a, n, sec in conn.execute(
            "SELECT u.app, a.name, SUM(u.seconds) AS t FROM usage u LEFT JOIN apps a ON a.app = u.app "
            "WHERE u.day BETWEEN ? AND ? GROUP BY u.app ORDER BY t DESC", (s, e))]
        if period == "day":
            per_hour = dict(conn.execute("SELECT hour, SUM(seconds) FROM usage WHERE day = ? GROUP BY hour", (s,)))
            series = [(f"{h:02d}", int(per_hour.get(h, 0))) for h in range(24)]
        else:
            per_day = dict(conn.execute(
                "SELECT day, SUM(seconds) FROM usage WHERE day BETWEEN ? AND ? GROUP BY day", (s, e)))
            days = [start + dt.timedelta(n) for n in range((end - start).days + 1)]
            fmt = "%a" if period == "week" else "%d"
            series = [(d.strftime(fmt).lstrip("0") if period == "month" else d.strftime(fmt),
                       int(per_day.get(d.isoformat(), 0))) for d in days]
        prev_start, prev_end, _ = period_range(period, shift(period, start, -1))
        previous = _total(conn, prev_start, prev_end)
        days_with_data = conn.execute("SELECT COUNT(DISTINCT day) FROM usage WHERE day BETWEEN ? AND ?",
                                      (s, e)).fetchone()[0]
    return Report(period, start, end, label, sum(a.seconds for a in apps), apps, series, previous, days_with_data)


def fmt_duration(seconds: int) -> str:
    if not seconds:
        return "0m"
    if seconds < 60:
        return f"{seconds}s"
    h, m = divmod(round(seconds / 60), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def pretty_name(exe: str) -> str:
    return FRIENDLY.get(exe.lower()) or PureWindowsPath(exe).stem.replace("_", " ").replace("-", " ").title()


# --------------------------------------------------------------------------- Windows probes


def _file_description(path: str) -> str:
    """The 'File description' from an exe's version resource (what Task Manager shows)."""
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return ""
    ptr, length = ctypes.c_void_p(), ctypes.c_uint()
    langs = []
    if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) and length.value:
        pairs = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint16 * 2))
        langs = [f"{pairs[i][0]:04x}{pairs[i][1]:04x}" for i in range(length.value // 4)]
    for lang in langs + ["040904b0", "040904e4"]:
        if ver.VerQueryValueW(buf, f"\\StringFileInfo\\{lang}\\FileDescription", ctypes.byref(ptr),
                              ctypes.byref(length)) and length.value > 1:
            text = ctypes.wstring_at(ptr, length.value - 1).strip()
            if text:
                return text
    return ""


def _win32():
    """user32/kernel32 with 64-bit-safe handle types (cached)."""
    global _WIN32
    if _WIN32 is None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        _WIN32 = user32, kernel32
    return _WIN32


_WIN32 = None


def foreground_app() -> tuple[str, str] | None:
    """(exe file name, full path) of the app in front, or None."""
    from ctypes import wintypes

    user32, kernel32 = _win32()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return PureWindowsPath(buf.value).name, buf.value
    finally:
        kernel32.CloseHandle(handle)


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds() -> float:
    info = _LastInput(ctypes.sizeof(_LastInput))
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0


def _display_required() -> bool:
    """True while something (a video, a game) is keeping the screen awake, so watching counts as use."""
    try:
        from .platform_utils import run

        out = run(["powercfg", "/requests"], timeout=10).stdout
    except Exception:
        return False
    section = out.split("SYSTEM:")[0] if "SYSTEM:" in out else out
    return "DISPLAY:" in section and "None." not in section.split("DISPLAY:")[1].split("\n\n")[0]


# --------------------------------------------------------------------------- tracker


class Tracker:
    def __init__(self, conn: sqlite3.Connection, probe=None, idle=None, clock=time.time, watching=None):
        self.conn = conn
        self.probe = probe or foreground_app
        self.idle = idle or idle_seconds
        self.watching = watching or _display_required
        self.clock = clock
        self.buckets: dict[tuple[str, int, str], float] = {}
        self.names: dict[str, str] = {}
        self._known = {row[0] for row in conn.execute("SELECT app FROM apps")}
        self._last = clock()
        self._last_flush = self._last
        self._watch_cache = (0.0, False)

    def _is_watching(self, now: float) -> bool:
        # powercfg is slow-ish, so ask at most once a minute and only once the user is idle.
        if now - self._watch_cache[0] > 60:
            self._watch_cache = (now, bool(self.watching()))
        return self._watch_cache[1]

    def tick(self) -> None:
        now = self.clock()
        elapsed, self._last = now - self._last, now
        # A long gap means the PC slept or the tracker was paused - don't count it.
        if 0 < elapsed <= SAMPLE_SECONDS * 3:
            app = self.probe()
            active = self.idle() < IDLE_LIMIT_SECONDS or self._is_watching(now)
            if app and active and app[0].lower() not in IGNORED:
                exe = app[0].lower()
                stamp = dt.datetime.fromtimestamp(now)
                key = (stamp.date().isoformat(), stamp.hour, exe)
                self.buckets[key] = self.buckets.get(key, 0.0) + elapsed
                if exe not in self._known:
                    self._known.add(exe)
                    desc = ""
                    if IS_WINDOWS and exe not in FRIENDLY:
                        try:
                            desc = _file_description(app[1])
                        except OSError:
                            pass
                    self.names[exe] = FRIENDLY.get(exe) or desc or pretty_name(exe)
        if now - self._last_flush >= FLUSH_SECONDS:
            self.flush()

    def flush(self) -> None:
        if self.buckets or self.names:
            record(self.conn, self.buckets, self.names)
        self.buckets, self.names = {}, {}
        self._last_flush = self.clock()


def run_tracker() -> int:
    """Entry point for ``--tracker``: runs until sign-out. Only one copy runs at a time."""
    if not IS_WINDOWS:
        print("The screen time tracker only runs on Windows.")
        return 1
    _, kernel32 = _win32()
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: another tracker is running
        kernel32.CloseHandle(mutex)
        return 0
    tracker = Tracker(connect())
    try:
        while True:
            time.sleep(SAMPLE_SECONDS)
            try:
                tracker.tick()
            except sqlite3.Error:
                time.sleep(30)  # e.g. the database is briefly locked; try again later
    finally:
        tracker.flush()
        kernel32.CloseHandle(mutex)


# --------------------------------------------------------------------------- auto-start


def tracker_command() -> tuple[str, str]:
    """(program, arguments) that start the tracker without a console window."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--tracker"
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    launcher = Path(__file__).resolve().parent.parent / "launcher.py"
    return str(pythonw if pythonw.exists() else exe), f'"{launcher}" --tracker'


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def enable_autostart() -> tuple[bool, str]:
    """Register a sign-in task (no UAC prompt each time) and start the tracker now."""
    program, args = tracker_command()
    script = f"""
$ErrorActionPreference = 'Stop'
$a = New-ScheduledTaskAction -Execute {_ps_quote(program)} -Argument {_ps_quote(args)}
$t = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
     -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -Priority 7
Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
Start-ScheduledTask -TaskName {_ps_quote(TASK_NAME)}
"""
    res = powershell(script, timeout=60)
    return res.returncode == 0, (res.stderr or res.stdout).strip()


def disable_autostart() -> tuple[bool, str]:
    script = f"""
Stop-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Confirm:$false -ErrorAction Stop
"""
    res = powershell(script, timeout=60)
    return res.returncode == 0, (res.stderr or res.stdout).strip()


def autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    res = powershell(f"(Get-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -ErrorAction Stop).State", timeout=30)
    return res.returncode == 0 and bool(res.stdout.strip())
