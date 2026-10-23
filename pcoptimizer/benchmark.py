"""Quick built-in benchmark for comparing performance before and after optimising."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .platform_utils import app_data_dir
from .system_info import CpuMeter, memory_status

MB = 1024 ** 2

# (key, label, unit, higher_is_better, reference value used for the overall score)
METRICS = [
    ("cpu_single", "CPU single-core", "MB/s", True, 1000),
    ("cpu_multi", "CPU all cores", "MB/s", True, 8000),
    ("ram_copy", "RAM copy speed", "GB/s", True, 10),
    ("disk_write", "Disk write", "MB/s", True, 1000),
    ("disk_iops", "Disk 4K writes", "IOPS", True, 1000),
    ("bg_cpu", "Background CPU load", "%", False, 0),
    ("free_ram", "Free RAM", "GB", True, 0),
]

Progress = Callable[[str, float], None]


@dataclass
class BenchResult:
    timestamp: str
    values: dict[str, float] = field(default_factory=dict)
    score: int = 0


def _hash_for(seconds: float, block: bytes) -> int:
    """Bytes hashed in ``seconds``. hashlib releases the GIL, so threads run on separate cores."""
    h = hashlib.sha256()
    done, end = 0, time.perf_counter() + seconds
    while time.perf_counter() < end:
        h.update(block)
        done += len(block)
    return done


def cpu_test(threads: int, seconds: float = 3.0) -> float:
    block = os.urandom(4 * MB)
    totals = [0] * threads

    def worker(i):
        totals[i] = _hash_for(seconds, block)

    workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    start = time.perf_counter()
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    return sum(totals) / MB / (time.perf_counter() - start)


def ram_test(seconds: float = 2.0, size: int = 128 * MB) -> float:
    src, dst = bytearray(size), bytearray(size)
    copied, end = 0, time.perf_counter() + seconds
    start = time.perf_counter()
    while time.perf_counter() < end:
        dst[:] = src
        copied += size
    return copied / 1024 ** 3 / (time.perf_counter() - start)


def disk_test(folder: str | None = None, size: int = 256 * MB, seconds: float = 2.0) -> tuple[float, float]:
    """(sequential write MB/s, 4K synced random writes per second)."""
    path = Path(folder or tempfile.gettempdir()) / f"pcoptimizer_bench_{os.getpid()}.tmp"
    chunk = os.urandom(4 * MB)
    try:
        start = time.perf_counter()
        with open(path, "wb", buffering=0) as fh:
            for _ in range(size // len(chunk)):
                fh.write(chunk)
            os.fsync(fh.fileno())
        seq = size / MB / (time.perf_counter() - start)

        small, ops = os.urandom(4096), 0
        with open(path, "r+b", buffering=0) as fh:
            end = time.perf_counter() + seconds
            start = time.perf_counter()
            while time.perf_counter() < end:
                fh.seek(random.randrange(size // 4096) * 4096)
                fh.write(small)
                os.fsync(fh.fileno())
                ops += 1
        return seq, ops / (time.perf_counter() - start)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def overall_score(values: dict[str, float]) -> int:
    """Geometric mean of the speed tests against reference values, scaled to ~1000, minus background load."""
    ratios = [max(values.get(k, 0), 1e-6) / ref for k, _, _, _, ref in METRICS if ref]
    geo = math.exp(sum(math.log(r) for r in ratios) / len(ratios))
    return round(1000 * geo * (1 - min(values.get("bg_cpu", 0), 90) / 100))


def run(progress: Progress = lambda _m, _p: None) -> BenchResult:
    values: dict[str, float] = {}
    progress("Measuring background load...", 0.02)
    meter = CpuMeter()
    time.sleep(2)
    values["bg_cpu"] = meter.sample()
    values["free_ram"] = memory_status()[1] / 1024 ** 3
    progress("CPU single-core...", 0.15)
    values["cpu_single"] = cpu_test(1)
    progress("CPU all cores...", 0.35)
    values["cpu_multi"] = cpu_test(os.cpu_count() or 1)
    progress("RAM speed...", 0.55)
    values["ram_copy"] = ram_test()
    progress("Disk speed...", 0.7)
    values["disk_write"], values["disk_iops"] = disk_test()
    progress("Done", 1.0)
    return BenchResult(time.strftime("%Y-%m-%d %H:%M"), values, overall_score(values))


class BenchmarkHistory:
    def __init__(self, path: Path | None = None):
        self.path = path or app_data_dir() / "benchmarks.json"
        try:
            self.results = [BenchResult(**r) for r in json.loads(self.path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            self.results = []

    def add(self, result: BenchResult) -> None:
        self.results.append(result)
        self._save()

    def set_baseline_to_latest(self) -> None:
        self.results = self.results[-1:]
        self._save()

    def clear(self) -> None:
        self.results = []
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(r) for r in self.results], indent=2), encoding="utf-8")

    @property
    def baseline(self) -> BenchResult | None:
        return self.results[0] if self.results else None

    @property
    def latest(self) -> BenchResult | None:
        return self.results[-1] if len(self.results) > 1 else None


def change(key: str, before: float, after: float) -> float:
    """Percent improvement (positive = better) for a metric."""
    higher_better = next(m[3] for m in METRICS if m[0] == key)
    if key == "bg_cpu":
        return before - after  # percentage points
    if before <= 0:
        return 0.0
    pct = 100 * (after - before) / before
    return pct if higher_better else -pct
