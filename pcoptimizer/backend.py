"""Single facade the UI talks to. ``DemoBackend`` fakes the OS so the UI can be previewed anywhere."""

from __future__ import annotations

import random
import time
from typing import Callable

from . import cleaner, processes, system_info
from .platform_utils import is_admin, stream
from .registry import FakeRegistry, default_registry
from .tweaks import GUID_RE, TweakManager, shell_runner

Log = Callable[[str], None]


class Backend:
    demo = False

    def __init__(self):
        self.registry = default_registry()
        self.tweaks = TweakManager(self.registry, shell_runner)
        self.cpu = system_info.CpuMeter()
        self.clean_targets = cleaner.build_targets()

    # specs / usage
    def specs(self) -> system_info.SystemSpecs:
        return system_info.collect_specs()

    def usage(self) -> tuple[float, int, int]:
        """(cpu_percent, ram_total, ram_available)"""
        total, avail = system_info.memory_status()
        return self.cpu.sample(), total, avail

    def disk_free(self) -> int:
        return cleaner.free_disk_space()

    def is_admin(self) -> bool:
        return is_admin()

    # cleaner
    def scan(self, target) -> tuple[int, int]:
        return cleaner.scan(target)

    def clean(self, target, log: Log) -> int:
        return cleaner.clean(target, log)

    def trim_ram(self) -> int:
        return cleaner.trim_working_sets()

    def flush_dns(self) -> bool:
        return cleaner.flush_dns()

    # processes / startup
    def processes(self):
        return processes.list_processes()

    def end_process(self, name: str):
        return processes.end_process(name)

    def startup_apps(self):
        return processes.list_startup_apps(self.registry)

    def set_startup(self, app, enabled: bool):
        processes.set_startup_enabled(self.registry, app, enabled)

    # commands
    def run_command(self, cmd: str, on_line: Log) -> int:
        return stream(cmd, on_line)


class _DemoRunner:
    """Pretends to be powercfg so power-plan tweaks behave realistically in demo mode."""

    def __init__(self):
        self.active = "381b4222-f694-41f0-9685-ff5bb260df2e"
        self.plans = {"381b4222-f694-41f0-9685-ff5bb260df2e": "Balanced",
                      "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "High performance"}

    def __call__(self, cmd: str):
        time.sleep(0.05)
        guid = GUID_RE.search(cmd)
        if cmd.startswith("powercfg /getactivescheme"):
            return 0, f"Power Scheme GUID: {self.active}  ({self.plans[self.active]})"
        if cmd.startswith("powercfg /list"):
            return 0, "\n".join(f"Power Scheme GUID: {g}  ({n})" for g, n in self.plans.items())
        if cmd.startswith("powercfg -duplicatescheme") and guid:
            new = "d1e2f3a4-0000-4000-8000-00000000beef"
            self.plans[new] = "Ultimate Performance"
            return 0, f"Power Scheme GUID: {new}  (Ultimate Performance)"
        if cmd.startswith("powercfg /setactive") and guid:
            if guid.group(1) not in self.plans:
                return 1, "Invalid parameters"
            self.active = guid.group(1)
        return 0, ""


class DemoBackend(Backend):
    demo = True

    def __init__(self, backup_path=None):
        import tempfile
        from pathlib import Path

        self.registry = FakeRegistry()
        path = backup_path or Path(tempfile.gettempdir()) / "pcoptimizer_demo_backup.json"
        path.unlink(missing_ok=True)
        self.tweaks = TweakManager(self.registry, _DemoRunner(), backup_path=path)
        self.cpu = system_info.CpuMeter()
        self.clean_targets = cleaner.build_targets()
        self._junk = {t.id: random.randint(5, 900) * 1024 ** 2 for t in self.clean_targets}
        self._ram_used = 0.62
        rk = processes.RUN_KEYS[0]
        for name, cmd in [("Discord", r'"C:\Users\me\AppData\Local\Discord\Update.exe" --processStart Discord.exe'),
                          ("Spotify", r'"C:\Users\me\AppData\Roaming\Spotify\Spotify.exe" /minimized'),
                          ("OneDrive", r'"C:\Program Files\Microsoft OneDrive\OneDrive.exe" /background'),
                          ("Steam", r'"C:\Program Files (x86)\Steam\steam.exe" -silent')]:
            self.registry.set(rk[0], rk[1], name, cmd, "SZ")

    def specs(self):
        time.sleep(0.4)
        return system_info.demo_specs()

    def usage(self):
        total = 16 * 1024 ** 3
        self._ram_used = min(0.9, max(0.3, self._ram_used + random.uniform(-0.01, 0.012)))
        return random.uniform(4, 22), total, int(total * (1 - self._ram_used))

    def disk_free(self):
        return 212 * 1024 ** 3

    def is_admin(self):
        return True

    def scan(self, target):
        time.sleep(0.15)
        return self._junk.get(target.id, 0), random.randint(10, 3000)

    def clean(self, target, log):
        time.sleep(0.3)
        freed = self._junk.get(target.id, 0)
        self._junk[target.id] = 0
        log(f"{target.title}: freed {cleaner.human(freed)}")
        return freed

    def trim_ram(self):
        time.sleep(0.5)
        self._ram_used = max(0.3, self._ram_used - 0.12)
        return 143

    def flush_dns(self):
        return True

    def processes(self):
        P = processes.ProcInfo
        return [P("chrome.exe", 23, 1_850_000, "Google Chrome"), P("Discord.exe", 6, 610_000),
                P("Teams.exe", 4, 420_000, "Microsoft Teams"), P("OneDrive.exe", 1, 95_000, "OneDrive sync"),
                P("Spotify.exe", 5, 310_000, "Spotify"), P("steamwebhelper.exe", 7, 280_000)]

    def end_process(self, name):
        return True, f"SUCCESS: terminated {name}"

    def run_command(self, cmd, on_line):
        on_line(f"(demo) would run: {cmd}")
        for pct in (0, 25, 50, 75, 100):
            time.sleep(0.2)
            on_line(f"Progress {pct}%")
        return 0
