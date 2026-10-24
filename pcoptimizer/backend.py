"""Single facade the UI talks to. ``DemoBackend`` fakes the OS so the UI can be previewed anywhere."""

from __future__ import annotations

import random
import time
from types import SimpleNamespace
from typing import Callable

from . import benchmark, cleaner, games, processes, screentime, sensors, system_info
from .ai_assistant import AIChangeLog
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
        self.games = games.GameLibrary(self.tweaks)
        self.ai_changes = AIChangeLog(self.tweaks)
        self.bench_history = benchmark.BenchmarkHistory()
        self.sensors = sensors.SensorMonitor()

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
    def run_command(self, cmd: str | list[str], on_line: Log, timeout: float | None = None) -> int:
        return stream(cmd, on_line, timeout)

    # sensors / games / benchmark
    def temps(self) -> sensors.Reading:
        self.sensors.start()
        return self.sensors.latest

    def launch_game(self, path: str) -> None:
        games.launch(path)

    def run_benchmark(self, progress) -> benchmark.BenchResult:
        return benchmark.run(progress)

    # screen time
    def screentime_report(self, period: str, anchor=None) -> screentime.Report:
        return screentime.report(period, anchor)

    def screentime_enabled(self) -> bool:
        return screentime.autostart_enabled()

    def set_screentime(self, enabled: bool) -> tuple[bool, str]:
        return screentime.enable_autostart() if enabled else screentime.disable_autostart()

    def clear_screentime(self) -> None:
        screentime.clear()

    def shutdown(self) -> None:
        self.sensors.stop()


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

    def __init__(self, data_dir=None):
        import tempfile
        from pathlib import Path

        self.registry = FakeRegistry()
        data = Path(data_dir or tempfile.mkdtemp(prefix="pcoptimizer_demo_"))
        self.data = data
        self.tweaks = TweakManager(self.registry, _DemoRunner(), backup_path=data / "backup.json")
        self.cpu = system_info.CpuMeter()
        self.clean_targets = cleaner.build_targets()
        self.games = games.GameLibrary(self.tweaks, data / "games.json")
        self.ai_changes = AIChangeLog(self.tweaks, data / "ai_changes.json")
        self.bench_history = benchmark.BenchmarkHistory(data / "benchmarks.json")
        self.sensors = sensors.SensorMonitor()
        for svc in ("DiagTrack", "dmwappushservice", "MapsBroker", "RetailDemo", "WerSvc", "Spooler",
                    "XblAuthManager", "XblGameSave", "XboxNetApiSvc", "lfsvc", "WSearch", "SysMain"):
            self.registry.set("HKLM", rf"SYSTEM\CurrentControlSet\Services\{svc}", "Start", 2 if svc in ("DiagTrack", "Spooler", "WSearch", "SysMain") else 3, "DWORD")
        self.registry.set("HKLM", r"SYSTEM\CurrentControlSet\Services\RemoteRegistry", "Start", 4, "DWORD")
        self.games.add(r"C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs2.exe")
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

    def temps(self):
        return sensors.Reading(cpu_temp=random.uniform(44, 52), gpu_temp=random.uniform(40, 46),
                               gpu_load=random.uniform(2, 9), gpu_fan=30)

    def launch_game(self, path):
        pass

    def ai_client(self):
        return DemoAIClient()

    def _screentime_db(self):
        if not hasattr(self, "_st_db"):
            self._st_db = self.data / "screentime.db"
            _fill_demo_screentime(self._st_db)
        return self._st_db

    def screentime_report(self, period, anchor=None):
        return screentime.report(period, anchor, self._screentime_db())

    def screentime_enabled(self):
        return getattr(self, "_st_on", True)

    def set_screentime(self, enabled):
        self._st_on = enabled
        return True, ""

    def clear_screentime(self):
        screentime.clear(self._screentime_db())

    def run_benchmark(self, progress):
        base = {"cpu_single": 1650, "cpu_multi": 14800, "ram_copy": 11.8, "disk_write": 2400, "disk_iops": 2100,
                "bg_cpu": 9.5, "free_ram": 5.6}
        boost = 1.0 if not self.bench_history.results else 1.06
        for i, (key, label, *_rest) in enumerate(benchmark.METRICS):
            progress(label + "...", (i + 1) / len(benchmark.METRICS))
            time.sleep(0.25)
        vals = {k: (v * random.uniform(0.97, 1.03) * (boost if k != "bg_cpu" else (1 if boost == 1 else 0.45)))
                for k, v in base.items()}
        if boost > 1:
            vals["free_ram"] = base["free_ram"] + 2.3
        return benchmark.BenchResult(time.strftime("%Y-%m-%d %H:%M"), vals, benchmark.overall_score(vals))

    def run_command(self, cmd, on_line, timeout=None):
        on_line(f"(demo) would run: {cmd}")
        for pct in (0, 25, 50, 75, 100):
            time.sleep(0.2)
            on_line(f"Progress {pct}%")
        return 0


class DemoAIClient:
    """Scripted stand-in for the Anthropic client so the AI page can be previewed without an API key."""

    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    @staticmethod
    def _msg(stop_reason, *blocks):
        return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))

    def create(self, messages, **_kwargs):
        time.sleep(0.6)
        last = messages[-1]["content"]
        if isinstance(last, str):  # a new user message: start by looking at the PC
            return self._msg("tool_use",
                             SimpleNamespace(type="text", text="Let me take a look at your PC first."),
                             SimpleNamespace(type="tool_use", id="t1", name="get_pc_overview", input={}))
        if last and last[0]["tool_use_id"] == "t1":
            return self._msg("tool_use", SimpleNamespace(type="tool_use", id="t2", name="apply_optimizations", input={
                "ids": ["game_dvr_off", "startup_delay"],
                "reason": "Background game recording and the startup delay are slowing your PC down."}))
        return self._msg("end_turn", SimpleNamespace(type="text", text=(
            "Your PC is in good shape overall (rating 85/100). I found two things slowing it down and fixed them:\n"
            "1. Turned off background game recording, which was costing FPS.\n"
            "2. Removed the 10-second startup delay.\n\nRestart your PC when convenient to finish applying them.")))


def _fill_demo_screentime(path) -> None:
    """Six weeks of plausible usage so the Screen Time page has something to show."""
    import datetime as dt
    from contextlib import closing

    rnd = random.Random(7)
    apps = {"chrome.exe": (2.2, range(9, 23)), "discord.exe": (1.1, range(16, 24)), "cs2.exe": (1.4, range(18, 24)),
            "code.exe": (1.6, range(10, 18)), "spotify.exe": (0.5, range(9, 22)), "explorer.exe": (0.3, range(9, 23)),
            "winword.exe": (0.6, range(10, 17)), "steam.exe": (0.25, range(17, 23)), "vlc.exe": (0.4, range(20, 24)),
            "whatsapp.exe": (0.3, range(8, 23))}
    names = {a: screentime.pretty_name(a) for a in apps}
    today, now_hour = dt.date.today(), max(15, dt.datetime.now().hour)
    buckets = {}
    for back in range(42):
        day = today - dt.timedelta(days=back)
        weekend = day.weekday() >= 5
        for app, (hours, window) in apps.items():
            h = hours * rnd.uniform(0.4, 1.4) * (1.5 if weekend and app in ("cs2.exe", "discord.exe") else 1)
            hrs = [x for x in window if back or x <= now_hour]
            if not hrs:
                continue
            for _ in range(max(1, int(h * 3))):
                hr = rnd.choice(hrs)
                key = (day.isoformat(), hr, app)
                buckets[key] = min(3600, buckets.get(key, 0) + h * 3600 / max(1, int(h * 3)))
    with closing(screentime.connect(path)) as conn:
        screentime.record(conn, buckets, names)
