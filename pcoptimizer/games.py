"""Per-game performance profiles."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PureWindowsPath

from .platform_utils import IS_WINDOWS, app_data_dir
from .tweaks import RegValue, Tweak, TweakManager

IFEO = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options"
GPU_PREFS = r"Software\Microsoft\DirectX\UserGpuPreferences"
COMPAT_LAYERS = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
HIGH_PRIORITY = 3  # PerfOptions CpuPriorityClass: 3 = High


@dataclass
class GameProfile:
    path: str
    high_priority: bool = True
    high_perf_gpu: bool = True
    disable_fso: bool = False
    defender_exclusion: bool = False
    defender_preexisting: bool = False  # the folder was already excluded before we touched it

    @property
    def folder(self) -> str:
        return str(PureWindowsPath(self.path).parent)

    @property
    def exe(self) -> str:
        return PureWindowsPath(self.path).name

    @property
    def name(self) -> str:
        return PureWindowsPath(self.path).stem

    @property
    def tweak_id(self) -> str:
        return "game:" + self.path.lower()

    def to_tweak(self) -> Tweak:
        values = []
        if self.high_priority:
            values.append(RegValue("HKLM", f"{IFEO}\\{self.exe}\\PerfOptions", "CpuPriorityClass", HIGH_PRIORITY))
        if self.high_perf_gpu:
            values.append(RegValue("HKCU", GPU_PREFS, self.path, "GpuPreference=2;", "SZ"))
        if self.disable_fso:
            values.append(RegValue("HKCU", COMPAT_LAYERS, self.path, "~ DISABLEDXMAXIMIZEDWINDOWEDMODE", "SZ"))
        return Tweak(self.tweak_id, f"Game profile: {self.name}", self.path, "game", values)


class GameLibrary:
    def __init__(self, mgr: TweakManager, path: Path | None = None):
        self.mgr = mgr
        self.path = path or app_data_dir() / "games.json"
        self.games: list[GameProfile] = []
        try:
            for item in json.loads(self.path.read_text(encoding="utf-8")):
                self.games.append(GameProfile(**item))
        except (OSError, ValueError, TypeError):
            pass
        for g in self.games:
            mgr.register(g.to_tweak())

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(g) for g in self.games], indent=2), encoding="utf-8")

    def find(self, path: str) -> GameProfile | None:
        return next((g for g in self.games if g.path.lower() == path.lower()), None)

    def add(self, path: str) -> GameProfile:
        existing = self.find(path)
        if existing:
            return existing
        game = GameProfile(str(PureWindowsPath(path)))
        self.games.append(game)
        self._save()
        self.mgr.register(game.to_tweak())
        self.mgr.apply(game.tweak_id)
        return game

    # Defender exclusions are a PowerShell setting, not a registry value, so they're handled here.
    def _ps(self, script: str) -> tuple[int, str]:
        return self.mgr.runner(f'powershell -NoProfile -NonInteractive -Command "{script}"')

    def _set_exclusion(self, game: GameProfile, on: bool) -> None:
        folder = game.folder.replace("'", "''")
        if on:
            code, out = self._ps(f"(Get-MpPreference).ExclusionPath -contains '{folder}'")
            game.defender_preexisting = out.strip().lower() == "true"
            if not game.defender_preexisting:
                code, out = self._ps(f"Add-MpPreference -ExclusionPath '{folder}' -ErrorAction Stop")
                if code != 0:
                    raise RuntimeError(out.strip() or "Couldn't add the Defender exclusion")
        elif not game.defender_preexisting:
            self._ps(f"Remove-MpPreference -ExclusionPath '{folder}' -ErrorAction SilentlyContinue")

    def update(self, game: GameProfile, **options) -> None:
        """Change a game's options. The old values are restored first so the backup stays the true original."""
        if "defender_exclusion" in options and options["defender_exclusion"] != game.defender_exclusion:
            self._set_exclusion(game, options["defender_exclusion"])
        self.mgr.revert(game.tweak_id)
        for k, v in options.items():
            setattr(game, k, v)
        self._save()
        self.mgr.register(game.to_tweak())
        self.mgr.apply(game.tweak_id)

    def remove(self, game: GameProfile) -> None:
        if game.defender_exclusion:
            self._set_exclusion(game, False)
        self.mgr.revert(game.tweak_id)
        self.mgr.unregister(game.tweak_id)
        self.games.remove(game)
        self._save()


def launch(path: str) -> None:
    """Start a game as the normal (non-admin) user, like double-clicking it in Explorer."""
    if IS_WINDOWS:
        subprocess.Popen(["explorer.exe", path])
