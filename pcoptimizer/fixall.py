"""Fix everything: apply every safe recommendation in one go.

BIOS settings (XMP/EXPO, Resizable BAR) can't be changed from Windows, and the
security trade-offs (Memory Integrity, Defender exclusions) are left to the user,
so those are listed as manual steps instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import boost
from .network import PROVIDERS

Log = Callable[[str], None]

ACTIONS = [
    ("boost", "Run the full One-Click Boost",
     "Restore point, speed and privacy settings, unneeded services, junk cleanup, RAM and DNS flush."),
    ("refresh", "Monitors to their highest refresh rate", "Only where a monitor runs below its maximum."),
    ("dns", "Switch to the fastest DNS", "Tests each provider and switches only if one is clearly faster."),
    ("drivers", "Install driver updates from Windows Update", "Signed drivers only. Can take a few minutes."),
    ("auto_gaming", "Turn on Auto Gaming Mode", "Only if you've added games in Game Profiles."),
]

MANUAL_STEPS = {
    "xmp": "Turn on XMP / EXPO / DOCP for your RAM in the BIOS (Restart into BIOS on the Health Check page).",
    "rebar": "Enable 'Above 4G Decoding' and 'Re-Size BAR Support' in the BIOS.",
    "dual_channel": "Add a matching second RAM stick for dual-channel memory.",
    "ssd": "Move Windows to an SSD - the biggest upgrade for boot and loading times.",
    "temps": "Clean dust from fans and filters to stop the PC slowing down when hot.",
}

DNS_MIN_GAIN = 0.8  # switch only when the best provider is at least 20% faster than what's in use


@dataclass
class FixResult:
    done: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    needs_restart: bool = False


def _fastest_dns(backend, log: Log) -> str | None:
    adapters = backend.dns_adapters()
    if not adapters:
        log("[SKIPPED] Fast DNS: no connected network adapter")
        return None
    from .network import current_provider

    current = current_provider(adapters)
    times = {k: backend.measure_dns(servers) for k, (_, servers) in PROVIDERS.items()}
    valid = {k: v for k, v in times.items() if v is not None}
    log("  DNS lookup times: " + ", ".join(f"{PROVIDERS[k][0].split(' (')[0]} {v:.0f} ms" for k, v in valid.items()))
    if not valid:
        return None
    best = min(valid, key=valid.get)
    now_ms = valid.get(current if current in valid else "auto")
    if best == current or (now_ms is not None and valid[best] > now_ms * DNS_MIN_GAIN):
        log(f"[OK] DNS: keeping {PROVIDERS.get(current, ('your current servers',))[0]} - nothing clearly faster")
        return None
    for line in backend.set_dns(best, adapters):
        log(line)
    return PROVIDERS[best][0]


def run(backend, chosen: set[str], log: Log) -> FixResult:
    res = FixResult()

    def step(name: str, fn) -> None:
        log(f"> {name}...")
        try:
            outcome = fn()
            if outcome is False:
                res.skipped.append(name)
            else:
                res.done.append(name)
        except Exception as exc:
            res.failed.append(name)
            log(f"[FAILED] {name}: {exc}")

    if "boost" in chosen:
        def do_boost():
            r = boost.run(backend, {s.id for s in boost.STEPS}, log)
            res.needs_restart |= r.needs_restart
            log(f"[OK] Boost: {boost.summary(r)}")
        step("One-Click Boost", do_boost)

    if "refresh" in chosen:
        def do_refresh():
            changed = False
            for m in backend.monitors():
                if m.below_max:
                    ok, msg = backend.set_refresh_rate(m.device, m.max_hz)
                    log(f"[OK] {m.device}: {m.current_hz} Hz -> {m.max_hz} Hz" if ok else f"[FAILED] {m.device}: {msg}")
                    changed |= ok
            if not changed:
                log("[OK] Monitors already at their highest refresh rate")
            return changed
        step("Refresh rate", do_refresh)

    if "dns" in chosen:
        step("Fast DNS", lambda: _fastest_dns(backend, log) is not None)

    if "drivers" in chosen:
        def do_drivers():
            updates = backend.search_driver_updates()
            if not updates:
                log("[OK] Drivers are up to date")
                return False
            r = backend.install_driver_updates([u.id for u in updates], log)
            for t in r.installed:
                log(f"[OK] Installed {t}")
            for t in r.failed:
                log(f"[FAILED] {t}")
            res.needs_restart |= r.reboot
        step("Driver updates", do_drivers)

    if "auto_gaming" in chosen:
        def do_auto():
            if not backend.games.games:
                log("[SKIPPED] Auto Gaming Mode: add your games in Game Profiles first")
                return False
            ok, msg = backend.set_auto_gaming(True)
            if not ok:
                raise RuntimeError(msg)
            log("[OK] Auto Gaming Mode on")
        step("Auto Gaming Mode", do_auto)

    return res


def manual_steps(findings) -> list[str]:
    """Things only the user can do, for the findings that still need attention."""
    return [MANUAL_STEPS[f.id] for f in findings if f.id in MANUAL_STEPS and f.status in ("warn", "bad")]
