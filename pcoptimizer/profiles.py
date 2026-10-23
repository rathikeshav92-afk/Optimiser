"""Gaming and Balanced performance profiles built on top of the tweak catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .tweaks import BALANCED_GUID, PowerPlanTweak, TweakManager

# Gaming tweaks that cost battery/thermals and are reverted by Balanced mode.
AGGRESSIVE = ["power_ultimate", "mmcss_games", "power_throttling_off", "mouse_accel_off", "hags"]

BALANCED_PLAN = PowerPlanTweak(
    "power_balanced", "Balanced power plan",
    "Lets the CPU clock down when idle - quieter fans, cooler PC, better battery life.",
    "balanced", scheme_guid=BALANCED_GUID, scheme_name="Balanced",
)


@dataclass
class StepResult:
    tweak_id: str
    title: str
    ok: bool
    message: str = ""


Log = Callable[[str], None]


def _ensure_registered(mgr: TweakManager) -> None:
    if BALANCED_PLAN.id not in mgr.by_id:
        mgr.tweaks.append(BALANCED_PLAN)
        mgr.by_id[BALANCED_PLAN.id] = BALANCED_PLAN


def _apply(mgr: TweakManager, tweak_id: str, log: Log) -> StepResult:
    tweak = mgr.by_id[tweak_id]
    try:
        mgr.apply(tweak_id)
        log(f"[OK] {tweak.title}")
        return StepResult(tweak_id, tweak.title, True)
    except Exception as exc:  # report and keep going with the rest of the profile
        log(f"[FAILED] {tweak.title}: {exc}")
        return StepResult(tweak_id, tweak.title, False, str(exc))


def gaming_tweak_ids(mgr: TweakManager) -> list[str]:
    return [t.id for t in mgr.in_category("gaming")]


def apply_gaming(mgr: TweakManager, tweak_ids: list[str] | None = None, log: Log = print) -> list[StepResult]:
    _ensure_registered(mgr)
    ids = tweak_ids if tweak_ids is not None else gaming_tweak_ids(mgr)
    if "power_ultimate" in ids and mgr.has_backup(BALANCED_PLAN.id):
        # Drop the Balanced snapshot so switching back later goes through Ultimate's own backup.
        mgr.revert(BALANCED_PLAN.id)
    log("Applying Gaming mode...")
    return [_apply(mgr, i, log) for i in ids]


def apply_balanced(mgr: TweakManager, log: Log = print) -> list[StepResult]:
    """Undo the power-hungry gaming tweaks and switch to the Balanced power plan."""
    _ensure_registered(mgr)
    log("Applying Balanced mode...")
    results = []
    for tweak_id in AGGRESSIVE:
        if mgr.has_backup(tweak_id):
            title = mgr.by_id[tweak_id].title
            try:
                mgr.revert(tweak_id)
                log(f"[OK] Restored original: {title}")
                results.append(StepResult(tweak_id, title, True))
            except Exception as exc:
                log(f"[FAILED] {title}: {exc}")
                results.append(StepResult(tweak_id, title, False, str(exc)))
    results.append(_apply(mgr, BALANCED_PLAN.id, log))
    # Game Mode is harmless outside games, so keep it on.
    results.append(_apply(mgr, "game_mode", log))
    return results


def current_profile(mgr: TweakManager) -> str:
    _ensure_registered(mgr)
    if mgr.is_applied("power_ultimate"):
        return "Gaming"
    if mgr.is_applied(BALANCED_PLAN.id):
        return "Balanced"
    return "Custom"
