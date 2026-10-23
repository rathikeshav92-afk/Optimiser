"""Reversible system tweaks.

Every tweak records the original state of whatever it touches in a JSON backup
the first time it is applied, so ``revert`` can put the PC back exactly as it was.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .platform_utils import app_data_dir, run
from .registry import Registry

# A command runner returns (exit_code, output). Injected so tests never touch the OS.
Runner = Callable[[str], tuple[int, str]]


def shell_runner(cmd: str) -> tuple[int, str]:
    res = run(cmd, shell=True)
    return res.returncode, (res.stdout or "") + (res.stderr or "")


@dataclass(frozen=True)
class RegValue:
    hive: str
    key: str
    name: str
    value: Any
    kind: str = "DWORD"


@dataclass
class Tweak:
    id: str
    title: str
    description: str
    category: str  # "gaming" | "speed" | "services" | "game"
    values: list[RegValue] = field(default_factory=list)
    needs_restart: bool = False
    recommended: bool = True
    warning: str = ""

    def is_available(self, ctx: "TweakManager") -> bool:
        return True

    def is_applied(self, ctx: "TweakManager") -> bool:
        return all(_same(ctx.registry.get(v.hive, v.key, v.name), v) for v in self.values)

    def snapshot(self, ctx: "TweakManager") -> dict:
        saved = []
        for v in self.values:
            cur = ctx.registry.get(v.hive, v.key, v.name)
            saved.append([v.hive, v.key, v.name, _encode(cur[0]) if cur else None, cur[1] if cur else None])
        return {"values": saved}

    def apply(self, ctx: "TweakManager") -> None:
        for v in self.values:
            ctx.registry.set(v.hive, v.key, v.name, v.value, v.kind)

    def revert(self, ctx: "TweakManager", backup: dict) -> None:
        for hive, key, name, value, kind in backup.get("values", []):
            if kind is None:
                ctx.registry.delete(hive, key, name)
            else:
                ctx.registry.set(hive, key, name, _decode(value, kind), kind)


@dataclass
class CommandTweak(Tweak):
    """A tweak applied with a command (powercfg, fsutil...) rather than raw registry writes."""

    apply_cmd: str = ""
    revert_cmd: str = ""
    check: Callable[["TweakManager"], bool] | None = None

    def is_applied(self, ctx):
        return bool(self.check(ctx)) if self.check else False

    def snapshot(self, ctx):
        return {"was_applied": self.is_applied(ctx)}

    def apply(self, ctx):
        code, out = ctx.runner(self.apply_cmd)
        if code != 0:
            raise RuntimeError(out.strip() or f"'{self.apply_cmd}' failed ({code})")

    def revert(self, ctx, backup):
        if not backup.get("was_applied"):
            ctx.runner(self.revert_cmd)


GUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)


@dataclass
class PowerPlanTweak(Tweak):
    """Switch the active power plan, remembering the previous one."""

    scheme_guid: str = ""
    scheme_name: str = ""

    def _active(self, ctx) -> str:
        _, out = ctx.runner("powercfg /getactivescheme")
        m = GUID_RE.search(out)
        return m.group(1).lower() if m else ""

    def _find_by_name(self, ctx) -> str:
        _, out = ctx.runner("powercfg /list")
        for line in out.splitlines():
            if self.scheme_name.lower() in line.lower() and (m := GUID_RE.search(line)):
                return m.group(1).lower()
        return ""

    def is_applied(self, ctx):
        active = self._active(ctx)
        return bool(active) and active in (self.scheme_guid, self._find_by_name(ctx))

    def snapshot(self, ctx):
        return {"scheme": self._active(ctx)}

    def apply(self, ctx):
        guid = self._find_by_name(ctx) or self.scheme_guid
        code, out = ctx.runner(f"powercfg /setactive {guid}")
        if code != 0:
            # Hidden plans such as Ultimate Performance have to be duplicated first.
            code, out = ctx.runner(f"powercfg -duplicatescheme {self.scheme_guid}")
            m = GUID_RE.search(out)
            if code != 0 or not m:
                raise RuntimeError(out.strip() or "Could not create power plan")
            code, out = ctx.runner(f"powercfg /setactive {m.group(1)}")
            if code != 0:
                raise RuntimeError(out.strip() or "Could not activate power plan")

    def revert(self, ctx, backup):
        if backup.get("scheme"):
            ctx.runner(f"powercfg /setactive {backup['scheme']}")


def _same(current: tuple[Any, str] | None, want: RegValue) -> bool:
    if current is None:
        return False
    value = current[0]
    if want.kind == "BINARY":
        return bytes(value) == bytes.fromhex(want.value)
    if isinstance(want.value, int) and isinstance(value, int):
        return (value & 0xFFFFFFFF) == (want.value & 0xFFFFFFFF)
    return str(value) == str(want.value)


def _encode(value):
    return {"hex": bytes(value).hex()} if isinstance(value, (bytes, bytearray)) else value


def _decode(value, kind):
    if isinstance(value, dict) and "hex" in value:
        return bytes.fromhex(value["hex"])
    return value


# --------------------------------------------------------------------------- catalogue

HKCU, HKLM = "HKCU", "HKLM"
MM = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"
CDM = r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"

ULTIMATE_GUID = "e9a42b02-d5df-448d-aa00-03f14749eb61"
HIGH_PERF_GUID = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
BALANCED_GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"


def _reg_check(hive, key, name, expected):
    def check(ctx):
        cur = ctx.registry.get(hive, key, name)
        return cur is not None and cur[0] == expected
    return check


def build_catalogue() -> list[Tweak]:
    return [
        # ---------------------------------------------------------- gaming
        PowerPlanTweak(
            "power_ultimate", "Ultimate Performance power plan",
            "Keeps the CPU at full clocks and stops cores parking so frame times stay consistent.",
            "gaming", scheme_guid=ULTIMATE_GUID, scheme_name="Ultimate Performance",
            warning="Uses more power - on a laptop keep it plugged in.",
        ),
        Tweak(
            "game_mode", "Windows Game Mode",
            "Prioritises the game you are playing and pauses Windows Update installs while you play.",
            "gaming",
            [RegValue(HKCU, r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1),
             RegValue(HKCU, r"Software\Microsoft\GameBar", "AllowAutoGameMode", 1)],
        ),
        Tweak(
            "game_dvr_off", "Disable background game recording",
            "Turns off Xbox Game Bar's always-on capture (Game DVR), which costs FPS in the background.",
            "gaming",
            [RegValue(HKCU, r"System\GameConfigStore", "GameDVR_Enabled", 0),
             RegValue(HKCU, r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0)],
        ),
        Tweak(
            "hags", "Hardware-accelerated GPU scheduling",
            "Lets the graphics card manage its own memory, lowering latency on modern GPUs (GTX 10-series / RX 5000 and newer).",
            "gaming",
            [RegValue(HKLM, r"SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode", 2)],
            needs_restart=True,
        ),
        Tweak(
            "mmcss_games", "Give games CPU & GPU priority",
            "Tells the Windows multimedia scheduler to favour games over background tasks and stop throttling network traffic.",
            "gaming",
            [RegValue(HKLM, MM, "SystemResponsiveness", 10),
             RegValue(HKLM, MM, "NetworkThrottlingIndex", 0xFFFFFFFF),
             RegValue(HKLM, MM + r"\Tasks\Games", "GPU Priority", 8),
             RegValue(HKLM, MM + r"\Tasks\Games", "Priority", 6),
             RegValue(HKLM, MM + r"\Tasks\Games", "Scheduling Category", "High", "SZ"),
             RegValue(HKLM, MM + r"\Tasks\Games", "SFIO Priority", "High", "SZ")],
            needs_restart=True,
        ),
        Tweak(
            "power_throttling_off", "Disable CPU power throttling",
            "Stops Windows from down-clocking apps it thinks are in the background (e.g. a game on a second monitor).",
            "gaming",
            [RegValue(HKLM, r"SYSTEM\CurrentControlSet\Control\Power\PowerThrottling", "PowerThrottlingOff", 1)],
            needs_restart=True,
            warning="Reduces battery life on laptops.",
        ),
        Tweak(
            "mouse_accel_off", "Raw mouse input (no acceleration)",
            "Disables 'Enhance pointer precision' so aim is 1:1 with your hand movement.",
            "gaming",
            [RegValue(HKCU, r"Control Panel\Mouse", "MouseSpeed", "0", "SZ"),
             RegValue(HKCU, r"Control Panel\Mouse", "MouseThreshold1", "0", "SZ"),
             RegValue(HKCU, r"Control Panel\Mouse", "MouseThreshold2", "0", "SZ")],
            needs_restart=True,
        ),
        # ---------------------------------------------------------- general speed
        Tweak(
            "visual_fx", "Visual effects: best performance",
            "Turns off animations, fades and shadows while keeping smooth fonts. Makes Windows feel snappier on any PC.",
            "speed",
            [RegValue(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting", 3),
             RegValue(HKCU, r"Control Panel\Desktop", "UserPreferencesMask", "9012038010000000", "BINARY"),
             RegValue(HKCU, r"Control Panel\Desktop\WindowMetrics", "MinAnimate", "0", "SZ"),
             RegValue(HKCU, r"Control Panel\Desktop", "FontSmoothing", "2", "SZ"),
             RegValue(HKCU, r"Software\Microsoft\Windows\DWM", "EnableAeroPeek", 0)],
            needs_restart=True,
        ),
        Tweak(
            "transparency_off", "Disable transparency effects",
            "Removes the blurred glass effect from the taskbar and Start menu, saving GPU work.",
            "speed",
            [RegValue(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency", 0)],
        ),
        Tweak(
            "startup_delay", "Remove startup app delay",
            "Windows waits ~10 seconds before launching startup apps. This removes that wait.",
            "speed",
            [RegValue(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize", "StartupDelayInMSec", 0),
             RegValue(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize", "WaitForIdleState", 0)],
        ),
        Tweak(
            "menu_delay", "Faster menus",
            "Opens menus after 100 ms instead of the default 400 ms.",
            "speed",
            [RegValue(HKCU, r"Control Panel\Desktop", "MenuShowDelay", "100", "SZ")],
            needs_restart=True,
        ),
        Tweak(
            "background_apps_off", "Stop Store apps running in the background",
            "Prevents Microsoft Store apps from using CPU, RAM and network when you're not using them.",
            "speed",
            [RegValue(HKCU, r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications", "GlobalUserDisabled", 1),
             RegValue(HKCU, r"Software\Microsoft\Windows\CurrentVersion\Search", "BackgroundAppGlobalToggle", 0)],
            warning="Some apps (e.g. Mail, Phone Link) won't send notifications while closed.",
        ),
        Tweak(
            "suggestions_off", "Disable ads, tips & auto-installed apps",
            "Stops Windows from showing suggestions and silently installing promoted apps.",
            "speed",
            [RegValue(HKCU, CDM, "SilentInstalledAppsEnabled", 0),
             RegValue(HKCU, CDM, "SystemPaneSuggestionsEnabled", 0),
             RegValue(HKCU, CDM, "SoftLandingEnabled", 0),
             RegValue(HKCU, CDM, "SubscribedContent-338388Enabled", 0),
             RegValue(HKCU, CDM, "SubscribedContent-338389Enabled", 0),
             RegValue(HKCU, CDM, "SubscribedContent-353694Enabled", 0)],
        ),
        Tweak(
            "web_search_off", "Disable web results in Start search",
            "Start menu search only looks at your PC, which makes it much faster.",
            "speed",
            [RegValue(HKCU, r"Software\Policies\Microsoft\Windows\Explorer", "DisableSearchBoxSuggestions", 1)],
            needs_restart=True,
        ),
        Tweak(
            "telemetry_low", "Reduce telemetry",
            "Sets diagnostic data to the minimum level, cutting background uploads.",
            "speed",
            [RegValue(HKLM, r"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", 0)],
        ),
        Tweak(
            "faster_shutdown", "Faster shutdown",
            "Waits 2 s instead of 5 s for frozen apps and services when shutting down.",
            "speed",
            [RegValue(HKLM, r"SYSTEM\CurrentControlSet\Control", "WaitToKillServiceTimeout", "2000", "SZ"),
             RegValue(HKCU, r"Control Panel\Desktop", "HungAppTimeout", "2000", "SZ")],
            needs_restart=True,
        ),
        Tweak(
            "ntfs_last_access", "Disable NTFS last-access timestamps",
            "Stops Windows writing a timestamp every time a file is read, reducing disk activity.",
            "speed",
            [RegValue(HKLM, r"SYSTEM\CurrentControlSet\Control\FileSystem", "NtfsDisableLastAccessUpdate", 0x80000001)],
            needs_restart=True,
        ),
        CommandTweak(
            "hibernation_off", "Disable hibernation",
            "Deletes hiberfil.sys, freeing disk space equal to a large part of your RAM.",
            "speed",
            recommended=False,
            warning="Also disables Fast Startup and the Hibernate option.",
            apply_cmd="powercfg /hibernate off",
            revert_cmd="powercfg /hibernate on",
            check=_reg_check(HKLM, r"SYSTEM\CurrentControlSet\Control\Power", "HibernateEnabled", 0),
        ),
    ] + _service_tweaks()


def _service_tweaks() -> list[Tweak]:
    from .services import build_service_tweaks  # services builds on the classes above

    return build_service_tweaks()


class TweakManager:
    def __init__(self, registry: Registry, runner: Runner = shell_runner, backup_path: Path | None = None):
        self.registry = registry
        self.runner = runner
        self.backup_path = backup_path or app_data_dir() / "tweak_backup.json"
        self.tweaks = build_catalogue()
        self.by_id = {t.id: t for t in self.tweaks}
        self._lock = threading.Lock()
        self._backup = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.backup_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        self.backup_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.backup_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._backup, indent=2), encoding="utf-8")
        tmp.replace(self.backup_path)

    def register(self, tweak: Tweak) -> None:
        """Add a tweak defined at runtime (e.g. a per-game profile)."""
        if tweak.id in self.by_id:
            self.tweaks.remove(self.by_id[tweak.id])
        self.tweaks.append(tweak)
        self.by_id[tweak.id] = tweak

    def unregister(self, tweak_id: str) -> None:
        tweak = self.by_id.pop(tweak_id, None)
        if tweak is not None:
            self.tweaks.remove(tweak)

    def in_category(self, category: str) -> list[Tweak]:
        return [t for t in self.tweaks if t.category == category]

    def is_applied(self, tweak_id: str) -> bool:
        try:
            return self.by_id[tweak_id].is_applied(self)
        except Exception:
            return False

    def has_backup(self, tweak_id: str) -> bool:
        return tweak_id in self._backup

    def apply(self, tweak_id: str) -> None:
        tweak = self.by_id[tweak_id]
        with self._lock:
            # Only the *first* snapshot is kept: it is the user's original configuration.
            if tweak_id not in self._backup:
                self._backup[tweak_id] = tweak.snapshot(self)
                self._save()
        tweak.apply(self)

    def revert(self, tweak_id: str) -> bool:
        """Restore the original state. Returns False when there is nothing to restore."""
        with self._lock:
            backup = self._backup.get(tweak_id)
        if backup is None:
            return False
        # A backup whose tweak is no longer defined (e.g. a deleted game profile) still
        # holds the original registry values, so restore those generically.
        tweak = self.by_id.get(tweak_id) or Tweak(tweak_id, tweak_id, "", "")
        tweak.revert(self, backup)
        with self._lock:
            self._backup.pop(tweak_id, None)
            self._save()
        return True

    def revert_all(self) -> list[str]:
        reverted = []
        for tweak_id in list(self._backup):
            if self.revert(tweak_id):
                reverted.append(tweak_id)
        return reverted
