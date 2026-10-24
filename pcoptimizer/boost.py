"""One-Click Boost: a configurable sequence of safe, reversible optimisations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .cleaner import human
from .commands import COMMANDS
from .tweaks import Skipped

Log = Callable[[str], None]


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    description: str
    default: bool = True


STEPS = [
    Step("restore_point", "Create a restore point", "A Windows snapshot you can roll back to if anything goes wrong."),
    Step("speed", "Speed settings", "Visual effects, startup delay, faster menus, background apps, web search..."),
    Step("privacy", "Remove bloat & tracking (registry)",
         "Ads, advertising ID, activity history, Widgets, Edge background, feedback pop-ups, telemetry tasks..."),
    Step("services", "Turn off unneeded services",
         "Telemetry, Maps, Fax, Retail Demo, Error Reporting, Remote Registry and other services almost nobody "
         "uses. Printing, Bluetooth and Xbox are left alone."),
    Step("clean", "Clean junk files", "Temp files, update caches, thumbnails, crash dumps and old logs."),
    Step("ram", "Free up RAM", "Trims unused memory from every running app."),
    Step("dns", "Flush DNS", "Clears saved website addresses."),
]

# Recommended settings from these tweak categories are applied by the matching step.
CATEGORY_FOR_STEP = {"speed": "speed", "privacy": "privacy", "services": "services"}


@dataclass
class BoostResult:
    applied: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    freed_bytes: int = 0
    needs_restart: bool = False


def _apply_category(backend, category: str, log: Log, result: BoostResult) -> None:
    mgr = backend.tweaks
    for t in mgr.in_category(category):
        if not t.recommended:
            continue
        try:
            if not t.is_available(mgr):
                continue  # e.g. a service that isn't installed on this PC
            if mgr.is_applied(t.id):
                result.already.append(t.title)
                continue
            mgr.apply(t.id)
            result.applied.append(t.title)
            result.needs_restart |= t.needs_restart
            log(f"[OK] {t.title}")
        except Skipped as exc:
            result.skipped.append(t.title)
            log(f"[SKIPPED] {t.title}: {exc}")
        except Exception as exc:
            result.failed.append(t.title)
            log(f"[FAILED] {t.title}: {exc}")


def run(backend, steps: set[str], log: Log, cancelled: Callable[[], bool] = lambda: False) -> BoostResult:
    result = BoostResult()
    for step in STEPS:
        if step.id not in steps or cancelled():
            continue
        log(f"> {step.title}...")
        if step.id == "restore_point":
            cmd = next(c for c in COMMANDS if c.id == "restore_point").command
            ok = backend.run_command(cmd, lambda _l: None) == 0
            log("[OK] Restore point created" if ok else
                "[FAILED] Restore point (System Protection may be off, or one was made in the last 24 h) - continuing")
        elif step.id in CATEGORY_FOR_STEP:
            before = len(result.applied)
            _apply_category(backend, CATEGORY_FOR_STEP[step.id], log, result)
            if len(result.applied) == before:
                log("[OK] Already optimised - nothing to change")
        elif step.id == "clean":
            for target in backend.clean_targets:
                if target.recommended:
                    try:
                        result.freed_bytes += backend.clean(target, log)
                    except Exception as exc:
                        log(f"[FAILED] {target.title}: {exc}")
        elif step.id == "ram":
            log(f"[OK] Trimmed {backend.trim_ram()} processes")
        elif step.id == "dns":
            log("[OK] DNS cache flushed" if backend.flush_dns() else "[FAILED] DNS flush")
    return result


def summary(result: BoostResult) -> str:
    parts = [f"{len(result.applied)} settings changed"]
    if result.already:
        parts.append(f"{len(result.already)} already optimised")
    if result.freed_bytes:
        parts.append(f"{human(result.freed_bytes)} freed")
    if result.skipped:
        parts.append(f"{len(result.skipped)} skipped (protected by Windows)")
    if result.failed:
        parts.append(f"{len(result.failed)} failed")
    return ", ".join(parts)
