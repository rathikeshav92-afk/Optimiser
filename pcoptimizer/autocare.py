"""Auto Care: finds what's wrong with the PC, fixes the safe things by itself and explains the rest in
plain words.

Only low-risk, self-healing fixes run automatically: freeing memory, deleting temporary junk files and
setting monitors to their best refresh rate. Anything that needs a decision (drivers, restarts, BIOS,
hardware) becomes a ``Problem`` the user is told about, with one button that helps.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import health
from .cleaner import human
from .platform_utils import app_data_dir

Log = Callable[[str], None]

RAM_HIGH_PCT = 85            # free up memory above this
JUNK_MIN_BYTES = 500 * 1024 ** 2  # clean junk automatically once there's at least this much
# Junk that is always safe to delete without asking: temporary files, crash dumps and old logs.
SAFE_JUNK = ("user_temp", "win_temp", "crash_dumps", "logs")
HISTORY_KEEP = 30
RENOTIFY_SECONDS = 24 * 3600  # remind about the same problem at most once a day

BAD, WARN, TIP = "bad", "warn", "tip"


@dataclass
class Problem:
    id: str
    title: str
    detail: str
    severity: str  # BAD (something is wrong) | WARN (worth doing) | TIP (optional upgrade)
    action_label: str = ""
    action: str = ""  # same action codes as health.Finding.fix


@dataclass
class CareReport:
    fixed: list[str] = field(default_factory=list)      # what Auto Care did, in plain words
    problems: list[Problem] = field(default_factory=list)  # what needs the user
    failed: list[str] = field(default_factory=list)     # fixes that were tried and didn't work
    checked_at: float = field(default_factory=time.time)

    @property
    def attention(self) -> list[Problem]:
        return [p for p in self.problems if p.severity in (BAD, WARN)]

    @property
    def tips(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == TIP]

    @property
    def healthy(self) -> bool:
        return not self.attention and not self.failed


# --------------------------------------------------------------------------- plain-language problems


def explain(f: health.Finding) -> Problem | None:
    """Turn a health finding the user still needs to act on into plain words. None = nothing to tell."""
    if f.status in (health.OK, health.INFO):
        return None
    if f.id == "disk_space":
        return Problem(f.id, "Your main drive is almost full", f"{f.detail} Auto Care already removed the junk it "
                       "safely could. Uninstalling games or apps you no longer use frees the most space.",
                       BAD if f.status == health.BAD else WARN, "Free up space", "page:cleaner")
    if f.id == "gpu_driver":
        return Problem(f.id, "Your graphics driver is out of date", f.detail, WARN, "Update drivers", "page:drivers")
    if f.id == "temps":
        return Problem(f.id, "Your PC is running hot", f.detail + " Make sure the air vents aren't blocked.", BAD)
    if f.id == "uptime":
        return Problem(f.id, "Your PC needs a restart", f.detail.split(" - ")[0] + ". A restart clears out "
                       "slowdowns and finishes updates. Save your work first.", WARN, "Restart now", "restart")
    if f.id == "startup":
        return Problem(f.id, "Too many apps start with Windows", f.detail + " Turning some off makes your PC start "
                       "faster.", WARN, "Choose apps", "page:tweaks")
    if f.id.startswith("refresh:"):
        return Problem(f.id, "Your screen could look smoother", f.detail, WARN, f.fix_label, f.fix)
    if f.id == "xmp":
        speed = re.search(r"\d+ MHz", f.detail)
        return Problem(f.id, "Your memory might be able to run faster",
                       f"Your RAM runs at the standard speed{f' ({speed.group()})' if speed else ''}. Look at the label on the RAM sticks (or "
                       "their box): if it shows a higher speed, a BIOS setting called XMP or EXPO makes it run at "
                       "that speed. If it shows the same speed, your RAM is already at full speed and there's "
                       "nothing to do.", TIP, f.fix_label, f.fix)
    if f.id == "rebar":
        return Problem(f.id, "Games could run a little faster", "A BIOS setting called Resizable BAR is off. "
                       "Turning it on gives a few percent more speed in many games.", TIP, f.fix_label, f.fix)
    if f.id == "ssd":
        return Problem(f.id, "An SSD would make your PC much faster", "Windows is on an older hard disk. Moving to "
                       "an SSD is the biggest upgrade for start-up and loading times.", TIP)
    if f.id == "dual_channel":
        return Problem(f.id, "A second memory stick would help", f.detail, TIP)
    if f.id == "hvci":
        return None  # a security trade-off: never nag about it
    return Problem(f.id, f.title, f.detail, TIP if f.status == health.WARN else BAD, f.fix_label, f.fix)


# Motherboard parts made by the chipset vendor. When their driver package is missing, Windows lists many
# "PCI Device" / "SM Bus Controller" entries; one chipset driver install fixes them all.
CHIPSET_IDS = {"Intel": ("PCI\\VEN_8086", "ACPI\\VEN_INTC", "ACPI\\INTC"),
               "AMD": ("PCI\\VEN_1022", "ACPI\\VEN_AMDI", "ACPI\\AMDI")}
# Windows' own placeholder that waits on a chipset driver before it can start.
CHIPSET_DEPENDENTS = ("ACPI\\VEN_MSFT&DEV_8000", "ACPI\\MSFT8000")


def _search_url(query: str) -> str:
    from urllib.parse import quote_plus

    return "https://www.bing.com/search?q=" + quote_plus(query)


def device_problems(devices, specs=None) -> list[Problem]:
    """Group broken devices into as few plain-language problems as possible."""
    chipset, vendor, other = [], "", []
    for d in devices:
        dev_id = d.device_id.upper()
        maker = next((v for v, prefixes in CHIPSET_IDS.items() if dev_id.startswith(prefixes)), "")
        if maker:
            chipset.append(d)
            vendor = vendor or maker
        elif dev_id.startswith(CHIPSET_DEPENDENTS):
            chipset.append(d)
        else:
            other.append(d)
    out = []
    if vendor:
        board = (getattr(specs, "motherboard", "") or "").strip()
        out.append(Problem(
            "chipset", "Your PC is missing its chipset drivers",
            f"{len(chipset)} parts of your motherboard ({vendor} chipset) have no driver, so Windows can't use them "
            f"properly. Your PC still works, but installing the {vendor} chipset drivers fixes all of them at once. "
            + (f"Get them from the support page for your {board} motherboard." if board else
               "Driver Updates can look for them on Windows Update."),
            WARN, "Find the driver" if board else "Find drivers",
            f"url:{_search_url(f'{board} {vendor} chipset driver download')}" if board else "page:drivers"))
    else:
        other = chipset + other
    names = list(dict.fromkeys(d.name for d in other))
    if len(names) == 1:
        out.append(Problem(f"device:{names[0]}", f"A device isn't working: {names[0]}",
                           "Windows can't use this device, usually because its driver is missing or broken. "
                           "Driver Updates can look for a fix.", BAD, "Find a driver", "page:drivers"))
    elif names:
        out.append(Problem("devices", f"{len(names)} devices aren't working",
                           f"{', '.join(names[:4])}{' and more' if len(names) > 4 else ''}. Their drivers are "
                           "missing or broken. Driver Updates can look for fixes.", BAD, "Find drivers",
                           "page:drivers"))
    return out


# --------------------------------------------------------------------------- the check-up


def run(backend, specs, log: Log = lambda _m: None, auto_fix: bool = True) -> CareReport:
    """Check the PC, fix what's safe (when ``auto_fix``) and return what still needs the user."""
    rep = CareReport()

    def attempt(what: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # one failing check must never stop the others
            rep.failed.append(f"Couldn't {what}: {exc}")
            log(f"[FAILED] {what}: {exc}")

    def memory():
        _cpu, total, avail = backend.usage()
        if not total or 100 * (total - avail) / total < RAM_HIGH_PCT:
            return
        if not auto_fix:
            rep.problems.append(Problem("ram", "Your memory is almost full", "Apps are using most of your RAM, "
                                        "which slows everything down.", WARN, "Free up memory", "free_ram"))
            return
        backend.trim_ram()
        _cpu, total2, avail2 = backend.usage()
        rep.fixed.append(f"Freed up memory ({human(max(0, avail2 - avail))} released)")

    def junk():
        targets = [t for t in backend.clean_targets if t.id in SAFE_JUNK]
        sizes = {t.id: backend.scan(t)[0] for t in targets}
        total = sum(sizes.values())
        if total < JUNK_MIN_BYTES:
            return
        if not auto_fix:
            rep.problems.append(Problem("junk", "Junk files are taking up space", f"{human(total)} of temporary "
                                        "files can be deleted safely.", WARN, "Clean up", "clean_junk"))
            return
        freed = sum(backend.clean(t, log) for t in targets if sizes[t.id])
        if freed:
            rep.fixed.append(f"Deleted {human(freed)} of junk files")

    attempt("free up memory", memory)
    attempt("clean junk files", junk)

    findings: list[health.Finding] = []
    attempt("run the health check", lambda: findings.extend(health.evaluate(backend.health_facts(specs))))
    for f in findings:
        if auto_fix and f.id.startswith("refresh:") and f.status == health.BAD and f.fix.startswith("refresh:"):
            device, hz = f.fix[len("refresh:"):].rsplit(":", 1)
            ok, msg = backend.set_refresh_rate(device, int(hz))
            if ok:
                rep.fixed.append(f"Set your screen to {hz} Hz so it looks smoother")
                continue
            rep.failed.append(f"Couldn't set your screen to {hz} Hz: {msg or 'Windows refused'}")
        p = explain(f)
        if p:
            rep.problems.append(p)

    attempt("check your devices", lambda: rep.problems.extend(device_problems(backend.problem_devices(), specs)))
    order = {BAD: 0, WARN: 1, TIP: 2}
    rep.problems.sort(key=lambda p: order[p.severity])
    return rep


# --------------------------------------------------------------------------- history & notifications


class CareState:
    """What Auto Care did recently and which problems the user was already told about.

    Shared by the app and the background helper through one JSON file."""

    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / "autocare.json"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.history: list[dict] = data.get("history", [])  # [{"at": ts, "text": "..."}], newest first
        self.notified: dict[str, float] = data.get("notified", {})
        self.last: dict | None = data.get("last")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"history": self.history, "notified": self.notified, "last": self.last}),
                       encoding="utf-8")
        tmp.replace(self.path)

    def record(self, rep: CareReport) -> None:
        self.history = ([{"at": rep.checked_at, "text": t} for t in rep.fixed] + self.history)[:HISTORY_KEEP]
        self.last = {"checked_at": rep.checked_at, "fixed": rep.fixed, "failed": rep.failed,
                     "problems": [asdict(p) for p in rep.problems]}
        self.save()

    def last_report(self) -> CareReport | None:
        if not self.last:
            return None
        return CareReport(self.last["fixed"], [Problem(**p) for p in self.last["problems"]], self.last["failed"],
                          self.last["checked_at"])

    def to_notify(self, rep: CareReport, now: float | None = None) -> list[str]:
        """Messages worth a Windows notification: new or not-recently-mentioned problems and failed fixes."""
        now = now or time.time()
        out = []
        for key, text in [(p.id, p.title) for p in rep.attention] + [(f"failed:{t}", t) for t in rep.failed]:
            if key not in self.notified or now - self.notified[key] >= RENOTIFY_SECONDS:
                self.notified[key] = now
                out.append(text)
        self.save()
        return out


def summary(rep: CareReport) -> str:
    """One line for a notification or the status bar."""
    parts = []
    if rep.fixed:
        parts.append(f"fixed {len(rep.fixed)} thing{'s' if len(rep.fixed) != 1 else ''}")
    n = len(rep.attention) + len(rep.failed)
    if n:
        parts.append(f"{n} need{'s' if n == 1 else ''} your attention")
    return ("Auto Care " + " and ".join(parts) + ".") if parts else "Your PC is in good shape."
