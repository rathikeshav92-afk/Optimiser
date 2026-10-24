"""All-services manager: list every Windows service, start/stop/restart it, change its startup type.

Startup-type changes are recorded as reversible tweaks (undo in Backup & Restore).
Services Windows can't run without are protected and can't be stopped or changed here.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .platform_utils import app_data_dir, as_list, powershell, powershell_json
from .services import PROTECTED
from .tweaks import Skipped, Tweak, TweakManager

MODES = {"auto": "Automatic", "delayed-auto": "Automatic (Delayed)", "demand": "Manual", "disabled": "Disabled"}
_NAME_RE = re.compile(r"^[\w.\-]{1,256}$")

LIST_ALL = r"""
Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | ForEach-Object {
    [pscustomobject]@{ Name = $_.Name; DisplayName = $_.DisplayName; State = $_.State; StartMode = $_.StartMode
        Delayed = [bool]$_.DelayedAutoStart; Description = $_.Description; Path = $_.PathName
        CanStop = [bool]$_.AcceptStop }
}
"""


@dataclass
class ServiceInfo:
    name: str
    display_name: str
    state: str          # Running / Stopped / ...
    mode: str           # auto / delayed-auto / demand / disabled / boot / system
    description: str = ""
    path: str = ""
    can_stop: bool = True

    @property
    def start_label(self) -> str:
        return MODES.get(self.mode, self.mode.title())

    @property
    def per_user(self) -> bool:
        """Per-user instances like 'cbdhsvc_4f2a1' follow their template service and can't be configured."""
        return bool(re.search(r"_[0-9a-f]{4,8}$", self.name, re.I))

    @property
    def protected(self) -> bool:
        base = re.sub(r"_[0-9a-f]{4,8}$", "", self.name, flags=re.I).lower()
        return self.name.lower() in PROTECTED or base in PROTECTED or self.mode in ("boot", "system")


def _mode(start_mode: str, delayed: bool) -> str:
    m = (start_mode or "").lower()
    if m in ("auto", "automatic"):
        return "delayed-auto" if delayed else "auto"
    return {"manual": "demand", "disabled": "disabled", "boot": "boot", "system": "system"}.get(m, m or "demand")


def parse_all(data) -> list[ServiceInfo]:
    return sorted((ServiceInfo(d.get("Name") or "", d.get("DisplayName") or d.get("Name") or "", d.get("State") or "",
                               _mode(d.get("StartMode") or "", bool(d.get("Delayed"))), d.get("Description") or "",
                               d.get("Path") or "", bool(d.get("CanStop", True)))
                   for d in as_list(data) if isinstance(d, dict) and d.get("Name")),
                  key=lambda s: s.display_name.lower())


def list_all() -> list[ServiceInfo]:
    return parse_all(powershell_json(LIST_ALL, timeout=90))


def parse_sc_qc(text: str) -> str | None:
    """Startup mode from `sc qc` output, e.g. 'START_TYPE : 2   AUTO_START  (DELAYED)' -> 'delayed-auto'."""
    m = re.search(r"START_TYPE\s*:\s*(\d)\s+(\w+)(\s+\(DELAYED\))?", text)
    if not m:
        return None
    return {"0": "boot", "1": "system", "2": "delayed-auto" if m.group(3) else "auto", "3": "demand",
            "4": "disabled"}.get(m.group(1))


@dataclass
class ServiceStartTweak(Tweak):
    service: str = ""
    target: str = "demand"

    def _current(self, ctx) -> str | None:
        code, out = ctx.runner(f'sc qc "{self.service}"')
        return parse_sc_qc(out) if code == 0 else None

    def is_applied(self, ctx) -> bool:
        return self._current(ctx) == self.target

    def snapshot(self, ctx) -> dict:
        return {"mode": self._current(ctx)}

    def _set(self, ctx, mode: str) -> None:
        code, out = ctx.runner(f'sc config "{self.service}" start= {mode}')
        if code != 0:
            if code == 5 or "access is denied" in out.lower():
                raise Skipped("Windows protects this service's settings")
            raise RuntimeError(out.strip() or f"sc config failed ({code})")

    def apply(self, ctx) -> None:
        self._set(ctx, self.target)

    def revert(self, ctx, backup) -> None:
        if backup.get("mode") in MODES:
            self._set(ctx, backup["mode"])


class ServiceManager:
    def __init__(self, mgr: TweakManager, path: Path | None = None):
        self.mgr = mgr
        self.path = path or app_data_dir() / "service_changes.json"
        try:
            self.changes: dict[str, str] = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.changes = {}
        for name, mode in self.changes.items():
            mgr.register(self._tweak(name, mode))

    @staticmethod
    def tweak_id(name: str) -> str:
        return f"svcstart:{name.lower()}"

    def _tweak(self, name: str, mode: str) -> ServiceStartTweak:
        return ServiceStartTweak(self.tweak_id(name), f"Service {name}: {MODES[mode]}", "", "servicemgr",
                                 service=name, target=mode)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.changes, indent=2), encoding="utf-8")

    def changed_names(self) -> set[str]:
        """Services whose startup type we changed and haven't undone (from here or Backup & Restore)."""
        return {n for n in self.changes if self.mgr.has_backup(self.tweak_id(n))}

    def undo(self, name: str) -> bool:
        ok = self.mgr.revert(self.tweak_id(name))
        self.changes.pop(name, None)
        self._save()
        return ok

    def set_start(self, svc: ServiceInfo, mode: str) -> str:
        if mode not in MODES:
            raise ValueError(f"Unknown startup type {mode}")
        if not _NAME_RE.match(svc.name):
            raise ValueError("Invalid service name")
        if svc.protected:
            raise ValueError(f"{svc.display_name} is needed by Windows and can't be changed here")
        if svc.per_user:
            raise ValueError("This is a per-user copy of a service; change the main service instead")
        tid = self.tweak_id(svc.name)
        if self.mgr.has_backup(tid):  # keep the true original: undo the earlier change first
            self.mgr.revert(tid)
        self.changes[svc.name] = mode
        self._save()
        self.mgr.register(self._tweak(svc.name, mode))
        try:
            self.mgr.apply(tid)
        except Exception:
            self.changes.pop(svc.name, None)
            self._save()
            raise
        return tid


def control(svc: ServiceInfo, action: str) -> tuple[bool, str]:
    """start / stop / restart right now (startup type unchanged)."""
    if action not in ("start", "stop", "restart") or not _NAME_RE.match(svc.name):
        return False, "Invalid request"
    if action != "start" and svc.protected:
        return False, f"{svc.display_name} is needed by Windows and can't be stopped here"
    verb = {"start": "Start-Service", "stop": "Stop-Service -Force", "restart": "Restart-Service -Force"}[action]
    res = powershell(f"{verb} -Name '{svc.name}' -ErrorAction Stop", timeout=120)
    return res.returncode == 0, (res.stderr or res.stdout).strip().splitlines()[0] if res.returncode else ""


def to_dict(svc: ServiceInfo) -> dict:
    return asdict(svc) | {"start": svc.start_label, "protected": svc.protected}
