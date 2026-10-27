"""The background helper (``--tracker``): screen time recording, Auto Gaming Mode and Auto Care.

It starts at sign-in through one Task Scheduler task, which exists while at least
one of the features is on. Each feature checks the shared settings file, so
turning one off in the app takes effect within a minute without restarting it.
"""

from __future__ import annotations

import ctypes
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from . import settings
from .platform_utils import IS_WINDOWS, app_data_dir

GAME_CHECK_SECONDS = 10
GAME_GONE_CHECKS = 3  # the game must be gone this many checks in a row (30 s) before switching back
SETTINGS_RELOAD_SECONDS = 60
CARE_FIRST_SECONDS = 5 * 60   # first check-up a few minutes after sign-in, once the PC has settled
CARE_EVERY_SECONDS = 4 * 3600


def helper_needed(values: dict) -> bool:
    return bool(values.get("screentime") or values.get("auto_gaming") or values.get("auto_care"))


def sync_task(values: dict) -> tuple[bool, str]:
    """Register or remove the sign-in task to match the settings."""
    from .screentime import autostart_enabled, disable_autostart, enable_autostart

    if helper_needed(values):
        return enable_autostart()
    if autostart_enabled():
        return disable_autostart()
    return True, ""


# --------------------------------------------------------------------------- Auto Gaming Mode


def game_exes(path: Path | None = None) -> set[str]:
    try:
        games = json.loads((path or app_data_dir() / "games.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    from pathlib import PureWindowsPath

    return {PureWindowsPath(g["path"]).name.lower() for g in games if isinstance(g, dict) and g.get("path")}


class AutoGaming:
    """Switches to Gaming Mode while a profiled game runs, then undoes exactly what it changed."""

    def __init__(self, make_manager: Callable, running: Callable[[], set[str]], games: Callable[[], set[str]],
                 state_path: Path | None = None, log: Callable[[str], None] = lambda _m: None):
        self.make_manager = make_manager  # fresh TweakManager each time: the app may have changed the backup
        self.running = running
        self.games = games
        self.state_path = state_path or app_data_dir() / "auto_gaming_state.json"
        self.log = log
        self.gone = 0
        self.state = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"active": False, "changed": []}

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def tick(self) -> None:
        from .profiles import apply_gaming, gaming_tweak_ids

        playing = sorted(self.games() & self.running())
        if playing and not self.state["active"]:
            mgr = self.make_manager()
            # Only remember settings that weren't already on; those are the ones to undo later.
            changed = [i for i in gaming_tweak_ids(mgr) if not mgr.has_backup(i) and not mgr.is_applied(i)]
            apply_gaming(mgr, changed, log=self.log)
            self.state = {"active": True, "changed": changed, "game": playing[0]}
            self._save()
            self.log(f"Gaming Mode on for {playing[0]} ({len(changed)} settings changed)")
            self.gone = 0
        elif self.state["active"]:
            self.gone = 0 if playing else self.gone + 1
            if self.gone >= GAME_GONE_CHECKS:
                mgr = self.make_manager()
                for tweak_id in self.state.get("changed", []):
                    mgr.revert(tweak_id)
                self.log(f"Game closed - restored {len(self.state.get('changed', []))} settings")
                self.state = {"active": False, "changed": []}
                self._save()
                self.gone = 0


def running_exe_names() -> set[str]:
    """Lower-case exe names of every running process we can query."""
    if not IS_WINDOWS:
        return set()
    from ctypes import wintypes

    psapi = ctypes.WinDLL("psapi")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    pids = (wintypes.DWORD * 4096)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids), ctypes.byref(needed)):
        return set()
    names = set()
    buf = ctypes.create_unicode_buffer(1024)
    for pid in pids[: needed.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            names.add(buf.value.rsplit("\\", 1)[-1].lower())
        kernel32.CloseHandle(handle)
    return names


# --------------------------------------------------------------------------- Auto Care


def care_checkup(make_backend: Callable, auto_fix: bool, log: Callable[[str], None],
                 notify: Callable[[list[str]], object] | None = None):
    """One background check-up: fix what's safe, record it, and notify about anything that needs the user."""
    from . import autocare

    backend = make_backend()
    rep = autocare.run(backend, backend.specs(), log=log, auto_fix=auto_fix)
    state = backend.care_state()
    state.record(rep)
    log(autocare.summary(rep))
    if notify is None:
        from .notify import notify_problems as notify
    notify(state.to_notify(rep))
    return rep


# --------------------------------------------------------------------------- main loop


def run() -> int:
    """Entry point for ``--tracker``: runs until sign-out. Only one copy runs at a time."""
    from .registry import default_registry
    from .screentime import MUTEX_NAME, SAMPLE_SECONDS, Tracker, _win32, connect
    from .tweaks import TweakManager, shell_runner

    if not IS_WINDOWS:
        print("The background helper only runs on Windows.")
        return 1
    _, kernel32 = _win32()
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: another helper is running
        kernel32.CloseHandle(mutex)
        return 0
    log_path = app_data_dir() / "background.log"

    def log(msg: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
        except OSError:
            pass

    registry = default_registry()
    tracker = Tracker(connect())
    auto = AutoGaming(lambda: TweakManager(registry, shell_runner), running_exe_names, game_exes, log=log)
    values, last_reload, last_game_check = settings.load(), 0.0, 0.0
    next_care = time.time() + CARE_FIRST_SECONDS
    care_thread: threading.Thread | None = None

    def care():
        from .backend import Backend

        try:
            care_checkup(Backend, bool(values.get("auto_fix", True)), log)
        except Exception as exc:
            log(f"Auto Care error: {exc}")
    try:
        while True:
            time.sleep(SAMPLE_SECONDS)
            now = time.time()
            if now - last_reload >= SETTINGS_RELOAD_SECONDS:
                values, last_reload = settings.load(), now
            if values.get("screentime"):
                try:
                    tracker.tick()
                except sqlite3.Error:
                    time.sleep(30)  # e.g. the database is briefly locked; try again later
            else:
                tracker._last = now  # don't count the paused time when tracking is turned back on
            if values.get("auto_care") and now >= next_care and not (care_thread and care_thread.is_alive()):
                # Its own thread: a check-up takes a while and must not pause screen time recording.
                next_care = now + CARE_EVERY_SECONDS
                care_thread = threading.Thread(target=care, daemon=True)
                care_thread.start()
            if (values.get("auto_gaming") or auto.state["active"]) and now - last_game_check >= GAME_CHECK_SECONDS:
                last_game_check = now
                try:
                    if values.get("auto_gaming"):
                        auto.tick()
                    else:  # turned off mid-game: put things back now
                        auto.games = lambda: set()
                        auto.gone = GAME_GONE_CHECKS
                        auto.tick()
                        auto.games = game_exes
                except Exception as exc:  # never let one failure stop screen time recording
                    log(f"Auto Gaming Mode error: {exc}")
    finally:
        tracker.flush()
        kernel32.CloseHandle(mutex)
