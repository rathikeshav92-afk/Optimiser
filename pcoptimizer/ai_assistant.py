"""AI assistant: Claude diagnoses problems and changes settings using the app's own tools.

Every tool that changes the PC goes through ``approve()`` first, so the user sees
exactly what will happen and can say no. Registry edits are recorded as normal
reversible tweaks, so they show up (and can be undone) in Backup & Restore.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from . import profiles
from .cleaner import human
from .platform_utils import app_data_dir
from .rating import rate
from .system_info import gb
from .tweaks import RegValue, Tweak

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_STEPS = 30  # tool-use round trips per user message
MAX_OUTPUT_CHARS = 8000
COMMAND_TIMEOUT = 15 * 60

Approve = Callable[[str, str, str], bool]  # (title, details, warning) -> allowed?
Notify = Callable[[str], None]

SYSTEM_PROMPT = """You are the AI assistant built into "PC Optimizer", a Windows app that speeds up the user's PC. \
The user is not technical. They come to you when their PC has a problem or when they want a setting changed \
and can't find it.

How to work:
- Investigate before changing anything. Start with get_pc_overview when the PC's state matters, and use the \
read-only tools and diagnostic commands to find the real cause.
- Prefer the app's built-in tools (apply_optimizations, activate_mode, clean_junk_files, set_startup_app, ...) \
over raw commands: they are tested and can be undone in Backup & Restore.
- Every action that changes the PC asks the user for permission. Give each one a short, plain-English \
`reason`. That text is what the user reads in the permission prompt. If they decline, accept it and offer \
another option.
- When the user only wants to see a setting, or would rather change it themselves, open the right Windows \
Settings page with open_windows_settings.
- Before a risky change (registry edits outside the built-in tweaks, network resets, driver or system \
repairs), create a restore point first.
- Never turn off Windows Defender, the firewall, SmartScreen, UAC or Windows Update. Never delete the \
user's personal files. Never run scripts downloaded from the internet. If asked to do one of these, explain \
the risk and suggest a safer alternative.
- Tell the user when a change needs a restart.

How to reply: keep it short and friendly, in plain language with no jargon. Use short numbered steps when \
the user has to do something themselves. Summarise what you changed at the end."""


# --------------------------------------------------------------------------- tool schemas


def _tool(name: str, description: str, properties: dict | None = None) -> dict:
    properties = properties or {}
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


REASON = {"type": "string", "description": "One plain-English sentence for the user explaining why."}
IDS = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    _tool("get_pc_overview", "Hardware specs, the 0-100 performance rating, live CPU/RAM/disk usage, "
          "temperatures, current mode (Gaming/Balanced) and whether the app runs as administrator."),
    _tool("list_optimizations", "Every built-in optimization (gaming tweaks, speed tweaks, optional services, "
          "game profiles) with its id, description, whether it is applied, and any warning."),
    _tool("apply_optimizations", "Apply built-in optimizations by id (from list_optimizations). Reversible.",
          {"ids": IDS, "reason": REASON}),
    _tool("undo_optimizations", "Restore the original settings for these optimization ids.",
          {"ids": IDS, "reason": REASON}),
    _tool("activate_mode", "Switch the whole PC to Gaming Mode (max performance) or Balanced Mode (everyday use).",
          {"mode": {"type": "string", "enum": ["gaming", "balanced"]}, "reason": REASON}),
    _tool("scan_junk_files", "How much space each junk-file category would free (temp files, caches, "
          "Recycle Bin...), with target ids."),
    _tool("clean_junk_files", "Delete the junk-file categories with these target ids (from scan_junk_files).",
          {"target_ids": IDS, "reason": REASON}),
    _tool("free_ram", "Trim unused memory from all running apps. Safe and needs no permission."),
    _tool("list_startup_apps", "Apps that start with Windows and whether each is enabled."),
    _tool("set_startup_app", "Enable or disable a startup app by its exact name from list_startup_apps.",
          {"name": {"type": "string"}, "enabled": {"type": "boolean"}, "reason": REASON}),
    _tool("list_background_apps", "Running apps grouped by name with their memory use (system processes hidden)."),
    _tool("end_app", "Force-close a running app by process name, e.g. 'chrome.exe'.",
          {"process_name": {"type": "string"}, "reason": REASON}),
    _tool("run_command",
          "Run a Windows command and return its output. Use shell 'cmd' or 'powershell'. Set changes_system "
          "to true unless the command only reads information. Read-only diagnostics (ipconfig /all, "
          "systeminfo, Get-* cmdlets...) run immediately; anything else asks the user first. Commands run "
          "as administrator, with no interactive input, and time out after 15 minutes.",
          {"command": {"type": "string"}, "shell": {"type": "string", "enum": ["cmd", "powershell"]},
           "changes_system": {"type": "boolean"}, "reason": REASON}),
    _tool("read_registry_value", "Read one registry value.",
          {"hive": {"type": "string", "enum": ["HKCU", "HKLM"]}, "key": {"type": "string"},
           "name": {"type": "string"}}),
    _tool("set_registry_value",
          "Set one registry value. The original is backed up and can be undone in Backup & Restore. "
          "For DWORD/QWORD pass the number as a decimal string.",
          {"hive": {"type": "string", "enum": ["HKCU", "HKLM"]}, "key": {"type": "string"},
           "name": {"type": "string"}, "value_type": {"type": "string", "enum": ["DWORD", "QWORD", "SZ", "EXPAND_SZ"]},
           "value": {"type": "string"}, "reason": REASON}),
    _tool("delete_registry_value", "Delete one registry value. Backed up first; undoable in Backup & Restore.",
          {"hive": {"type": "string", "enum": ["HKCU", "HKLM"]}, "key": {"type": "string"},
           "name": {"type": "string"}, "reason": REASON}),
    _tool("list_running_services", "Every running Windows service with its start type, and whether it is "
          "protected (needed by Windows, can't be stopped)."),
    _tool("set_service_running", "Stop or start a Windows service right now (until the next restart). To keep a "
          "service off permanently, use the svc_* ids with apply_optimizations instead.",
          {"name": {"type": "string"}, "running": {"type": "boolean"}, "reason": REASON}),
    _tool("open_windows_settings", "Open a Windows Settings page for the user, e.g. 'ms-settings:display', "
          "'ms-settings:network-status', 'ms-settings:windowsupdate', 'ms-settings:sound'.",
          {"uri": {"type": "string"}}),
    _tool("create_restore_point", "Create a Windows System Restore point. Takes about a minute."),
    _tool("get_screen_time", "The user's screen time per app for today, this week (Mon-Sun) or this month, "
          "with the previous period's total for comparison. Only counts time since tracking was turned on.",
          {"period": {"type": "string", "enum": ["day", "week", "month"]}}),
    _tool("get_driver_status", "Devices with driver problems (missing, failed, disabled) and the installed "
          "third-party drivers with their dates, oldest first."),
    _tool("check_driver_updates", "Search Windows Update for driver updates for this PC. Takes 30 s to 3 minutes. "
          "Returns update ids for install_driver_updates."),
    _tool("install_driver_updates", "Download and install Windows Update driver updates by id (from "
          "check_driver_updates). Create a restore point first. May need a restart.",
          {"ids": IDS, "reason": REASON}),
]


# --------------------------------------------------------------------------- read-only command detection

_CMD_READ_ONLY = re.compile(
    r"^(ipconfig(\s+/all|\s+/displaydns)?|systeminfo|tasklist(\s+/\w+)*|driverquery(\s+/\w+)*|ver|whoami|hostname"
    r"|powercfg\s+/(list|l|getactivescheme|a|availablesleepstates|query(\s+[\w-]+)*)"
    r"|ping\s+[\w.:-]+(\s+-n\s+\d+)?|tracert\s+[\w.:-]+|nslookup\s+[\w.:-]+|pathping\s+[\w.:-]+"
    r"|netsh\s+(wlan|interface|int|advfirewall)\s+show(\s+[\w=\"-]+)*|sc\s+(query|qc)(\s+[\w\"-]+)*"
    r"|schtasks\s+/query(\s+/\w+)*|wmic(\s+[\w]+)+\s+get(\s+[\w,]+)*|getmac|route\s+print|arp\s+-a"
    r"|w32tm\s+/query\s+/status|bcdedit\s+/enum|vol(\s+\w:)?|dir(\s+[^&|<>^]+)?)$",
    re.I,
)
_PS_SAFE_CMDLETS = {"select-object", "sort-object", "where-object", "format-table", "format-list",
                    "measure-object", "select-string", "convertto-json", "out-string", "group-object"}


def is_read_only(command: str, shell: str) -> bool:
    """Conservative check: True only for commands we know just read information."""
    cmd = command.strip()
    if not cmd or "\n" in cmd:
        return False
    if shell == "cmd":
        return bool(_CMD_READ_ONLY.match(cmd)) and not re.search(r"[&|<>^%]", cmd)
    if re.search(r"[;&>`]|\$\(|::|\[|@\(", cmd):
        return False
    words = re.findall(r"(?<![\w-])([A-Za-z]+-[A-Za-z]+)", cmd)
    if not words or not words[0].lower().startswith("get-"):
        return False
    return all(w.lower().startswith("get-") or w.lower() in _PS_SAFE_CMDLETS for w in words)


# --------------------------------------------------------------------------- AI registry changes


@dataclass
class AIChange:
    id: str
    title: str
    hive: str
    key: str
    name: str
    kind: str
    value: Any

    def to_tweak(self) -> Tweak:
        return Tweak(self.id, self.title, f"{self.hive}\\{self.key}\\{self.name}", "ai",
                     [RegValue(self.hive, self.key, self.name, self.value, self.kind)])


class AIChangeLog:
    """Registry edits made by the assistant, persisted so they stay undoable after a restart."""

    def __init__(self, mgr, path: Path | None = None):
        self.mgr = mgr
        self.path = path or app_data_dir() / "ai_changes.json"
        try:
            self.changes = [AIChange(**c) for c in json.loads(self.path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            self.changes = []
        for c in self.changes:
            mgr.register(c.to_tweak())

    def record(self, change: AIChange) -> None:
        self.changes = [c for c in self.changes if c.id != change.id] + [change]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(c) for c in self.changes], indent=2), encoding="utf-8")
        self.mgr.register(change.to_tweak())


# --------------------------------------------------------------------------- the assistant


class Cancelled(Exception):
    pass


class Assistant:
    def __init__(self, backend, client=None, approve: Approve = lambda *_: False, notify: Notify = lambda _: None,
                 change_log: AIChangeLog | None = None, on_text: Notify | None = None):
        self.backend = backend
        self.approve = approve
        self.notify = notify
        self.on_text = on_text  # receives text Claude writes between tool calls, as it arrives
        self.changes = change_log or getattr(backend, "ai_changes", None) or AIChangeLog(backend.tweaks)
        self._client = client
        self.messages: list[dict] = []
        self.cancel_event = threading.Event()
        self._specs = None

    # ------------------------------------------------------------------ client
    @property
    def client(self):
        if self._client is None:
            import anthropic

            from .ai_settings import load_api_key

            key = load_api_key()
            self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        return self._client

    def reset(self) -> None:
        self.messages = []

    def _system(self) -> str:
        if self._specs is None:
            self._specs = self.backend.specs()
        s = self._specs
        gpu = s.primary_gpu.name if s.primary_gpu else "unknown GPU"
        return (SYSTEM_PROMPT + f"\n\nThis PC: {s.cpu_name}, {gpu}, {gb(s.total_ram_bytes, 0)} RAM, "
                f"{s.os_name}. It is a desktop PC.")

    # ------------------------------------------------------------------ conversation loop
    def send(self, text: str) -> str:
        """Send a user message, run the tool loop to completion and return Claude's final reply."""
        self.cancel_event.clear()
        self.messages.append({"role": "user", "content": text})
        reply_parts: list[str] = []
        for _ in range(MAX_STEPS):
            if self.cancel_event.is_set():
                raise Cancelled()
            response = self.client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                system=self._system(),
                tools=TOOLS,
                messages=self.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            # Keep the full content (thinking, fallback and tool_use blocks); the history must stay append-only.
            self.messages.append({"role": "assistant", "content": response.content})
            text_now = "\n".join(b.text for b in response.content if b.type == "text" and b.text.strip())
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if text_now:
                if tool_uses and self.on_text:
                    self.on_text(text_now)
                else:
                    reply_parts.append(text_now)

            if response.stop_reason == "refusal":
                return "\n\n".join(reply_parts + ["Sorry, I can't help with that request."])
            if response.stop_reason == "max_tokens":
                return "\n\n".join(reply_parts + ["(My answer was cut off - ask me to continue.)"])
            if response.stop_reason != "tool_use" or not tool_uses:
                return "\n\n".join(reply_parts)

            results = []
            for block in tool_uses:
                if self.cancel_event.is_set():
                    output, is_error = "The user stopped the assistant.", True
                else:
                    output, is_error = self.run_tool(block.name, block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output[-MAX_OUTPUT_CHARS:], "is_error": is_error})
            self.messages.append({"role": "user", "content": results})
        return "\n\n".join(reply_parts + ["(I stopped after many steps - tell me if you want me to keep going.)"])

    # ------------------------------------------------------------------ tools
    def run_tool(self, name: str, args: dict) -> tuple[str, bool]:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            return f"Unknown tool {name}", True
        try:
            return handler(**args), False
        except PermissionError as exc:
            return str(exc), True
        except Exception as exc:  # report the failure to Claude so it can try something else
            return f"Error: {exc}", True

    def _ask(self, title: str, details: str, reason: str, warning: str = "") -> None:
        self.notify(f"Asking permission: {title}")
        if not self.approve(title, f"{reason}\n\n{details}".strip(), warning):
            self.notify("Declined by you")
            raise PermissionError("The user declined this action. Don't retry it; suggest an alternative.")

    def _t_get_pc_overview(self) -> str:
        self.notify("Checking your PC...")
        b = self.backend
        if self._specs is None:
            self._specs = b.specs()
        s = self._specs
        r = rate(s)
        cpu, total, avail = b.usage()
        t = b.temps()
        return json.dumps({
            "cpu": f"{s.cpu_name} ({s.cores}C/{s.threads}T)",
            "gpus": [{"name": g.name, "vram": gb(g.vram_bytes, 0), "driver": g.driver,
                      "display": f"{g.resolution} @ {g.refresh_hz} Hz"} for g in s.gpus],
            "ram": f"{gb(s.total_ram_bytes, 0)} @ {s.ram_speed_mhz} MHz, {s.ram_sticks} stick(s)",
            "disks": [{"name": d.name, "type": d.kind, "size_gb": round(d.size_bytes / 1e9), "health": d.health,
                       "system": d.is_system} for d in s.disks],
            "system_drive_free": f"{gb(s.sys_drive_free)} of {gb(s.sys_drive_size)}",
            "windows": f"{s.os_name} build {s.os_build}", "motherboard": s.motherboard,
            "last_restart": s.last_boot,
            "rating": {"total": r.total, "grade": r.grade, "weakest": r.bottleneck,
                       "parts": {c.name: f"{c.score:.0f}/{c.max_score:.0f}" for c in r.components}},
            "usage_now": {"cpu_percent": round(cpu), "ram_used": f"{gb(total - avail)} of {gb(total)}"},
            "temps_c": {"cpu": t.cpu_temp, "gpu": t.gpu_temp, "gpu_load_percent": t.gpu_load},
            "mode": profiles.current_profile(b.tweaks),
            "running_as_admin": b.is_admin(),
        })

    def _t_list_optimizations(self) -> str:
        self.notify("Looking at available optimizations...")
        mgr = self.backend.tweaks
        out = []
        for t in mgr.tweaks:
            if t.category == "balanced":
                continue
            item = {"id": t.id, "title": t.title, "category": t.category, "description": t.description,
                    "applied": mgr.is_applied(t.id), "needs_restart": t.needs_restart}
            if t.warning:
                item["warning"] = t.warning
            if not t.is_available(mgr):
                item["available"] = False
            out.append(item)
        return json.dumps(out)

    def _tweak_titles(self, ids: list[str]) -> list[str]:
        mgr = self.backend.tweaks
        unknown = [i for i in ids if i not in mgr.by_id]
        if unknown:
            raise ValueError(f"Unknown optimization ids: {unknown}. Call list_optimizations first.")
        return [mgr.by_id[i].title for i in ids]

    def _t_apply_optimizations(self, ids: list[str], reason: str) -> str:
        titles = self._tweak_titles(ids)
        self._ask("Apply optimizations", "\n".join(f"• {t}" for t in titles), reason)
        mgr, lines = self.backend.tweaks, []
        for i, title in zip(ids, titles):
            try:
                mgr.apply(i)
                lines.append(f"OK: {title}")
                self.notify(f"Applied: {title}")
            except Exception as exc:
                lines.append(f"FAILED: {title}: {exc}")
        restart = any(mgr.by_id[i].needs_restart for i in ids)
        return "\n".join(lines) + ("\nSome changes need a restart." if restart else "")

    def _t_undo_optimizations(self, ids: list[str], reason: str) -> str:
        titles = self._tweak_titles(ids)
        self._ask("Undo optimizations", "\n".join(f"• {t}" for t in titles), reason)
        mgr = self.backend.tweaks
        return "\n".join(f"{'Restored' if mgr.revert(i) else 'Nothing to undo'}: {t}" for i, t in zip(ids, titles))

    def _t_activate_mode(self, mode: str, reason: str) -> str:
        label = "Gaming Mode" if mode == "gaming" else "Balanced Mode"
        self._ask(f"Switch to {label}", "", reason)
        lines: list[str] = []
        fn = profiles.apply_gaming if mode == "gaming" else profiles.apply_balanced
        results = fn(self.backend.tweaks, log=lines.append)
        self.notify(f"{label} activated")
        return "\n".join(lines) + f"\n{sum(r.ok for r in results)}/{len(results)} steps succeeded."

    def _t_scan_junk_files(self) -> str:
        self.notify("Scanning junk files...")
        out = []
        for t in self.backend.clean_targets:
            size, _ = self.backend.scan(t)
            out.append({"id": t.id, "title": t.title, "size": human(size), "bytes": size,
                        "recommended": t.recommended, "note": t.description})
        return json.dumps(out)

    def _t_clean_junk_files(self, target_ids: list[str], reason: str) -> str:
        targets = [t for t in self.backend.clean_targets if t.id in target_ids]
        if not targets:
            raise ValueError("No matching target ids. Call scan_junk_files first.")
        warning = "Items in the Recycle Bin will be deleted permanently." if any(
            t.id == "recycle_bin" for t in targets) else ""
        self._ask("Clean junk files", "\n".join(f"• {t.title}" for t in targets), reason, warning)
        lines, freed = [], 0
        for t in targets:
            freed += self.backend.clean(t, lines.append)
        self.notify(f"Freed {human(freed)}")
        return "\n".join(lines) + f"\nTotal freed: {human(freed)}"

    def _t_free_ram(self) -> str:
        before = self.backend.usage()[2]
        n = self.backend.trim_ram()
        after = self.backend.usage()[2]
        self.notify("Freed up RAM")
        return f"Trimmed {n} processes. Available RAM went from {gb(before)} to {gb(after)}."

    def _t_list_startup_apps(self) -> str:
        return json.dumps([{"name": a.name, "enabled": a.enabled, "command": a.command}
                           for a in self.backend.startup_apps()])

    def _t_set_startup_app(self, name: str, enabled: bool, reason: str) -> str:
        app = next((a for a in self.backend.startup_apps() if a.name.lower() == name.lower()), None)
        if app is None:
            raise ValueError(f"No startup app named {name!r}")
        self._ask(f"{'Enable' if enabled else 'Disable'} startup app: {app.name}", app.command, reason)
        self.backend.set_startup(app, enabled)
        return f"{app.name} is now {'enabled' if enabled else 'disabled'} at startup (takes effect next sign-in)."

    def _t_list_background_apps(self) -> str:
        return json.dumps([{"process": p.name, "what": p.label, "instances": p.count,
                            "memory_mb": round(p.memory_kb / 1024)} for p in self.backend.processes()[:30]])

    def _t_end_app(self, process_name: str, reason: str) -> str:
        self._ask(f"Close {process_name}", "Unsaved work in this app will be lost.", reason)
        ok, msg = self.backend.end_process(process_name)
        if not ok:
            raise RuntimeError(msg)
        return msg

    def _t_run_command(self, command: str, shell: str, changes_system: bool, reason: str) -> str:
        if changes_system or not is_read_only(command, shell):
            self._ask("Run a command", f"{shell}> {command}", reason,
                      "This command can change your system and can't be undone automatically.")
        self.notify(f"Running: {command}")
        lines: list[str] = []
        if shell == "powershell":
            code = self.backend.run_command(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
                lines.append, timeout=COMMAND_TIMEOUT)
        else:
            code = self.backend.run_command(command, lines.append, timeout=COMMAND_TIMEOUT)
        return f"Exit code {code}\n" + "\n".join(lines)

    def _t_read_registry_value(self, hive: str, key: str, name: str) -> str:
        cur = self.backend.registry.get(hive, key, name)
        if cur is None:
            return "Value does not exist."
        value, kind = cur
        if isinstance(value, (bytes, bytearray)):
            value = value.hex()
        return json.dumps({"value": value, "type": kind})

    def _t_set_registry_value(self, hive: str, key: str, name: str, value_type: str, value: str, reason: str) -> str:
        parsed: Any = int(value, 0) if value_type in ("DWORD", "QWORD") else value
        if value_type == "DWORD" and not 0 <= parsed <= 0xFFFFFFFF:
            raise ValueError("DWORD must be between 0 and 4294967295")
        cur = self.backend.registry.get(hive, key, name)
        self._ask("Change a registry setting",
                  f"{hive}\\{key}\n{name} = {value} ({value_type})\nCurrently: {cur[0] if cur else 'not set'}",
                  reason, "The old value is backed up - you can undo this in Backup & Restore.")
        digest = hashlib.sha1(f"{hive}\\{key}\\{name}".lower().encode()).hexdigest()[:10]
        change = AIChange(f"ai:{digest}", f"AI change: {reason[:70]}", hive, key, name, value_type, parsed)
        self.changes.record(change)
        self.backend.tweaks.apply(change.id)
        self.notify(f"Changed {name}")
        return f"Set {hive}\\{key}\\{name} = {value}. Backed up; undo with undo_optimizations(['{change.id}'])."

    def _t_open_windows_settings(self, uri: str) -> str:
        if not re.fullmatch(r"ms-settings:[\w\-]*", uri):
            raise ValueError("Only ms-settings: pages can be opened")
        self.backend.run_command(f"start {uri}", lambda _l: None)
        self.notify(f"Opened {uri}")
        return f"Opened {uri} on the user's screen."

    def _t_create_restore_point(self) -> str:
        from .commands import COMMANDS

        self.notify("Creating a restore point...")
        cmd = next(c for c in COMMANDS if c.id == "restore_point").command
        lines: list[str] = []
        code = self.backend.run_command(cmd, lines.append)
        if code != 0:
            raise RuntimeError("Could not create a restore point (System Protection may be off): " + "\n".join(lines))
        return "Restore point created."

    def _t_get_screen_time(self, period: str) -> str:
        from .screentime import fmt_duration

        self.notify("Checking your screen time...")
        rep = self.backend.screentime_report(period)
        return json.dumps({
            "period": rep.label, "total": fmt_duration(rep.total),
            "previous_period_total": fmt_duration(rep.previous_total),
            "daily_average": fmt_duration(rep.daily_average) if period != "day" else None,
            "apps": [{"app": a.name, "time": fmt_duration(a.seconds)} for a in rep.apps[:15]],
            "tracking_on": self.backend.screentime_enabled(),
        })

    def _t_get_driver_status(self) -> str:
        self.notify("Checking your drivers...")
        problems = self.backend.problem_devices()
        installed = self.backend.installed_drivers()
        return json.dumps({
            "problem_devices": [{"name": p.name, "class": p.device_class, "problem": p.problem, "code": p.code}
                                for p in problems],
            "drivers": [{"device": d.device, "maker": d.provider, "version": d.version, "date": d.date}
                        for d in installed[:40]],
        })

    def _t_check_driver_updates(self) -> str:
        self.notify("Checking Windows Update for drivers (can take a few minutes)...")
        updates = self.backend.search_driver_updates()
        return json.dumps([{"id": u.id, "title": u.title, "device": u.model, "class": u.device_class,
                            "date": u.date, "size": human(u.size_bytes)} for u in updates]) if updates else \
            "No driver updates available from Windows Update."

    def _t_install_driver_updates(self, ids: list[str], reason: str) -> str:
        from .drivers import GUID_RE

        if not ids or not all(GUID_RE.match(i) for i in ids):
            raise ValueError("Pass update ids from check_driver_updates.")
        known = {u.id: u.title for u in self.backend.search_driver_updates()}
        missing = [i for i in ids if i not in known]
        if missing:
            raise ValueError(f"These ids are not offered any more: {missing}. Call check_driver_updates again.")
        self._ask("Install driver updates", "\n".join(f"• {known[i]}" for i in ids), reason,
                  "Your screen or network may blink while drivers install. A restart may be needed.")
        lines: list[str] = []
        res = self.backend.install_driver_updates(ids, lines.append)
        self.notify(f"Installed {len(res.installed)} driver(s)")
        return json.dumps({"installed": res.installed, "failed": res.failed, "restart_needed": res.reboot,
                           "progress": lines[-5:]})

    def _t_delete_registry_value(self, hive: str, key: str, name: str, reason: str) -> str:
        cur = self.backend.registry.get(hive, key, name)
        if cur is None:
            return "That value doesn't exist - nothing to delete."
        self._ask("Delete a registry value", f"{hive}\\{key}\n{name} (currently {cur[0]!r})", reason,
                  "The value is backed up - you can undo this in Backup & Restore.")
        change_id = self.backend.regedit.delete_value(hive, key, name)
        self.notify(f"Deleted {name}")
        return f"Deleted. Undo with undo_optimizations(['{change_id}'])."

    def _t_list_running_services(self) -> str:
        self.notify("Listing running services...")
        return json.dumps([{"name": s.name, "display": s.display_name, "starts": s.start_type,
                            "protected": s.protected} for s in self.backend.running_services()])

    def _t_set_service_running(self, name: str, running: bool, reason: str) -> str:
        self._ask(f"{'Start' if running else 'Stop'} service: {name}", "Until the next restart.", reason)
        ok, msg = self.backend.set_service_running(name, running)
        if not ok:
            raise RuntimeError(msg or "failed")
        self.notify(f"{'Started' if running else 'Stopped'} {name}")
        return f"{name} {'started' if running else 'stopped'}."
