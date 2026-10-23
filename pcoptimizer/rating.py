"""Turn a SystemSpecs into a 0-100 performance rating with a per-component breakdown."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .system_info import SystemSpecs, _is_integrated

GIB = 1024 ** 3


@dataclass
class ComponentScore:
    name: str
    score: float
    max_score: float
    detail: str

    @property
    def percent(self) -> float:
        return 100 * self.score / self.max_score if self.max_score else 0


@dataclass
class Rating:
    total: int
    grade: str
    components: list[ComponentScore]
    bottleneck: str


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _interp(x: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation over sorted (x, y) points."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


_TIER = {"3": 0.6, "5": 0.8, "7": 0.9, "9": 1.0}


def cpu_generation_factor(name: str) -> float:
    """0..1 estimate of how modern / high-end a CPU model is, from its marketing name."""
    n = name.lower()
    if any(k in n for k in ("celeron", "pentium", "atom", "athlon")):
        return 0.3
    if m := re.search(r"core\(tm\)\s*ultra\s*(\d)|core ultra\s*(\d)", n):
        return _TIER.get(m.group(1) or m.group(2), 0.8)
    if m := re.search(r"i([3579])-(\d{4,5})", n):
        tier, digits = m.groups()
        gen = int(digits[:2]) if len(digits) == 5 else int(digits[0])
        gen_f = _interp(gen, [(4, 0.4), (6, 0.55), (8, 0.7), (10, 0.85), (12, 1.0)])
        return _TIER[tier] * gen_f
    if m := re.search(r"ryzen\s*(?:ai\s*)?([3579])\s*(?:pro\s*)?(\d)\d{3}", n):
        tier, series = m.group(1), int(m.group(2))
        series_f = _interp(series, [(1, 0.6), (3, 0.75), (5, 0.9), (7, 1.0)])
        return _TIER[tier] * series_f
    if "ryzen" in n and "threadripper" in n:
        return 1.0
    if "apple" in n or "snapdragon" in n:
        return 0.8
    return 0.6


def gpu_factor(name: str) -> float:
    """0..1 estimate of a GPU's gaming tier from its name."""
    n = name.lower()
    if m := re.search(r"rtx\s*(?:a)?(\d{2})(\d{2})", n):
        series, model = int(m.group(1)), int(m.group(2))
        series_f = _interp(series, [(20, 0.65), (30, 0.8), (40, 0.92), (50, 1.0)])
        model_f = _interp(model, [(50, 0.55), (60, 0.68), (70, 0.8), (80, 0.9), (90, 1.0)])
        return series_f * model_f
    if m := re.search(r"gtx\s*(\d{2})(\d{2})", n):
        series, model = int(m.group(1)), int(m.group(2))
        base = 0.45 if series >= 16 else 0.35 if series >= 10 else 0.2
        return base * _interp(model, [(50, 0.8), (60, 0.95), (80, 1.1)])
    if m := re.search(r"rx\s*(\d)(\d)\d{2}", n):
        series, model = int(m.group(1)), int(m.group(2))
        series_f = _interp(series, [(5, 0.55), (6, 0.78), (7, 0.9), (9, 1.0)])
        model_f = _interp(model, [(5, 0.55), (6, 0.68), (7, 0.8), (8, 0.9), (9, 1.0)])
        return series_f * model_f
    if "arc" in n and "intel" in n:
        return 0.5
    if _is_integrated(name):
        return 0.15
    return 0.4


def rate(specs: SystemSpecs) -> Rating:
    comps: list[ComponentScore] = []

    # CPU - 30 points
    thread_f = _clamp(specs.threads / 16)
    clock_f = _clamp((specs.max_clock_mhz - 1500) / 2500) if specs.max_clock_mhz else 0.5
    gen_f = cpu_generation_factor(specs.cpu_name)
    cpu = 30 * (0.45 * thread_f + 0.20 * clock_f + 0.35 * gen_f)
    comps.append(ComponentScore("Processor", cpu, 30, f"{specs.cores} cores / {specs.threads} threads"))

    # GPU - 25 points
    gpu = specs.primary_gpu
    if gpu:
        vram_f = _clamp(gpu.vram_bytes / (12 * GIB))
        gpu_pts = 25 * (0.8 * gpu_factor(gpu.name) + 0.2 * vram_f)
        detail = f"{gpu.name} ({gpu.vram_bytes / GIB:.0f} GB)" if gpu.vram_bytes else gpu.name
    else:
        gpu_pts, detail = 3.0, "No GPU detected"
    comps.append(ComponentScore("Graphics", gpu_pts, 25, detail))

    # RAM - 20 points
    ram_gb = specs.total_ram_bytes / GIB
    size_pts = _interp(ram_gb, [(2, 1), (4, 4), (8, 9), (16, 14), (32, 16), (64, 16)])
    speed = specs.ram_speed_mhz
    speed_pts = _interp(speed, [(1600, 1), (2666, 2), (3200, 3), (6000, 4)]) if speed else 2
    comps.append(ComponentScore("Memory", size_pts + speed_pts, 20, f"{ram_gb:.0f} GB" + (f" @ {speed} MHz" if speed else "")))

    # Storage - 15 points
    disk = specs.system_disk
    kind = disk.kind if disk else "Unknown"
    storage = {"NVMe SSD": 15, "SSD": 12, "HDD": 4}.get(kind, 8)
    comps.append(ComponentScore("Storage", storage, 15, f"System drive: {kind}"))

    # System health - 10 points
    os_pts = 5 if "11" in specs.os_name else 4 if "10" in specs.os_name else 2
    free_ratio = specs.sys_drive_free / specs.sys_drive_size if specs.sys_drive_size else 0.3
    free_pts = _interp(free_ratio, [(0.05, 0), (0.10, 2), (0.20, 4), (0.30, 5)])
    comps.append(ComponentScore("System health", os_pts + free_pts, 10, f"{free_ratio:.0%} free on system drive"))

    total = round(sum(c.score for c in comps))
    grade = (
        "Elite" if total >= 90 else
        "Excellent" if total >= 75 else
        "Good" if total >= 60 else
        "Average" if total >= 45 else
        "Entry level"
    )
    bottleneck = min(comps, key=lambda c: c.percent).name
    return Rating(total, grade, comps, bottleneck)
