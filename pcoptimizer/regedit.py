"""Registry editing for the Registry page: every change is reversible.

Each set/delete is recorded as a tweak in the TweakManager, so the original
value is backed up and can be restored from Backup & Restore. Before the first
change to a key, the whole key is also exported to a .reg file as a second copy.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .platform_utils import app_data_dir
from .registry import HIVES, KINDS
from .tweaks import RegValue, Tweak, TweakManager

FULL_HIVE = {"HKCU": "HKEY_CURRENT_USER", "HKLM": "HKEY_LOCAL_MACHINE", "HKCR": "HKEY_CLASSES_ROOT",
             "HKU": "HKEY_USERS"}
_ALIASES = {**{v: k for k, v in FULL_HIVE.items()}, **{k: k for k in HIVES}}

# Keys where a wrong edit can stop Windows from booting or signing in. Editing still works, with a stronger warning.
DANGEROUS = (r"system\currentcontrolset\control\session manager", r"system\currentcontrolset\services",
             r"software\microsoft\windows nt\currentversion\winlogon", r"system\setup", r"bcd00000000",
             r"system\currentcontrolset\control\lsa")

FAVORITES = [
    ("Startup apps (you)", "HKCU", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("Startup apps (all users)", "HKLM", r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("Visual effects", "HKCU", r"Control Panel\Desktop"),
    ("Mouse", "HKCU", r"Control Panel\Mouse"),
    ("Game DVR / Game Bar", "HKCU", r"System\GameConfigStore"),
    ("Explorer settings", "HKCU", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"),
    ("Game priority (MMCSS)", "HKLM", r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Multimedia\SystemProfile"),
    ("Services", "HKLM", r"SYSTEM\CurrentControlSet\Services"),
]


def parse_path(text: str) -> tuple[str, str]:
    """'Computer\\HKEY_LOCAL_MACHINE\\SOFTWARE\\X' or 'HKLM\\SOFTWARE\\X' -> ('HKLM', 'SOFTWARE\\X')."""
    t = text.strip().strip('"').replace("/", "\\")
    t = re.sub(r"^computer\\", "", t, flags=re.I)
    hive, _, key = t.partition("\\")
    hive = _ALIASES.get(hive.upper())
    if not hive:
        raise ValueError("Start the path with HKCU, HKLM, HKCR or HKU (or HKEY_...)")
    return hive, key.strip("\\")


def is_dangerous(key: str) -> bool:
    k = key.lower()
    return any(k.startswith(d) for d in DANGEROUS)


def parse_value(kind: str, text: str) -> Any:
    """Turn what the user typed into the Python value winreg expects."""
    if kind not in KINDS:
        raise ValueError(f"Unsupported type {kind}")
    if kind in ("DWORD", "QWORD"):
        t = text.strip().lower()
        n = int(t, 16) if t.startswith("0x") else int(t)
        limit = 0xFFFFFFFF if kind == "DWORD" else 0xFFFFFFFFFFFFFFFF
        if not 0 <= n <= limit:
            raise ValueError(f"{kind} must be between 0 and {limit}")
        return n
    if kind == "BINARY":
        hexstr = re.sub(r"[\s,]", "", text)
        if len(hexstr) % 2 or re.search(r"[^0-9a-fA-F]", hexstr):
            raise ValueError("Binary data must be hex bytes, e.g. 90 12 03 80")
        return hexstr.lower()
    if kind == "MULTI_SZ":
        return [line for line in text.splitlines()]
    return text


def format_value(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if kind in ("DWORD", "QWORD") and isinstance(value, int):
        return f"0x{value & (0xFFFFFFFF if kind == 'DWORD' else 0xFFFFFFFFFFFFFFFF):x} ({value})"
    if isinstance(value, (bytes, bytearray)):
        return " ".join(f"{b:02x}" for b in value[:64]) + (" ..." if len(value) > 64 else "")
    if isinstance(value, list):
        return " | ".join(map(str, value))
    return str(value)


def edit_text(value: Any, kind: str) -> str:
    """The value as the user should see it in the edit box."""
    if kind in ("DWORD", "QWORD") and isinstance(value, int):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return " ".join(f"{b:02x}" for b in value)
    if isinstance(value, list):
        return "\n".join(map(str, value))
    return "" if value is None else str(value)


@dataclass
class RegChange:
    id: str
    title: str
    hive: str
    key: str
    name: str
    kind: str
    value: Any
    action: str  # "set" | "delete"

    def to_tweak(self) -> Tweak:
        values = [RegValue(self.hive, self.key, self.name, self.value, self.kind)]
        cls = DeleteValueTweak if self.action == "delete" else Tweak
        return cls(self.id, self.title, f"{self.hive}\\{self.key}\\{self.name}", "registry", values)


@dataclass
class DeleteValueTweak(Tweak):
    """'Applying' deletes the value; reverting restores it from the snapshot like any tweak."""

    def is_applied(self, ctx) -> bool:
        return all(ctx.registry.get(v.hive, v.key, v.name) is None for v in self.values)

    def apply(self, ctx) -> None:
        for v in self.values:
            ctx.registry.delete(v.hive, v.key, v.name)


class RegistryEditor:
    def __init__(self, mgr: TweakManager, export: Callable[[str, str, Path], bool] | None = None,
                 path: Path | None = None):
        self.mgr = mgr
        self.reg = mgr.registry
        self.export = export
        self.path = path or app_data_dir() / "registry_changes.json"
        self.backup_dir = self.path.parent / "registry-backups"
        try:
            self.changes = [RegChange(**c) for c in json.loads(self.path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            self.changes = []
        for c in self.changes:
            mgr.register(c.to_tweak())
        self._exported: set[tuple[str, str]] = set()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(c) for c in self.changes], indent=2), encoding="utf-8")

    def export_key(self, hive: str, key: str) -> Path | None:
        """Save the whole key to a .reg file (once per session per key)."""
        if self.export is None or (hive, key.lower()) in self._exported:
            return None
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w.-]+", "_", f"{hive}_{key}")[-80:]
        target = self.backup_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S}_{safe}.reg"
        if self.export(hive, key, target):
            self._exported.add((hive, key.lower()))
            return target
        return None

    def _record(self, change: RegChange) -> str:
        # A second edit to the same value must not lose the first edit's backup: undo it first,
        # so the backup taken next is still the true original.
        if self.mgr.has_backup(change.id):
            self.mgr.revert(change.id)
        self.changes = [c for c in self.changes if c.id != change.id] + [change]
        self._save()
        self.mgr.register(change.to_tweak())
        self.mgr.apply(change.id)
        return change.id

    @staticmethod
    def _id(hive: str, key: str, name: str) -> str:
        return "reg:" + hashlib.sha1(f"{hive}\\{key}\\{name}".lower().encode()).hexdigest()[:12]

    def set_value(self, hive: str, key: str, name: str, kind: str, text: str) -> str:
        value = parse_value(kind, text)
        cur = self.reg.get(hive, key, name)
        if cur is not None and cur[1] not in KINDS:
            raise ValueError("This value has a special type that can only be changed in Windows' Registry Editor")
        self.export_key(hive, key)
        title = f"Registry: {name or '(Default)'} = {format_value(bytes.fromhex(value) if kind == 'BINARY' else value, kind)[:40]}"
        return self._record(RegChange(self._id(hive, key, name), title, hive, key, name, kind, value, "set"))

    def delete_value(self, hive: str, key: str, name: str) -> str:
        cur = self.reg.get(hive, key, name)
        if cur is None:
            raise ValueError("That value doesn't exist")
        if cur[1] not in KINDS:
            raise ValueError("This value has a special type that can only be changed in Windows' Registry Editor")
        self.export_key(hive, key)
        return self._record(RegChange(self._id(hive, key, name), f"Registry: deleted {name or '(Default)'}",
                                      hive, key, name, cur[1], None, "delete"))
