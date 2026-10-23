"""CPU/GPU temperature and GPU load readings.

A single long-running PowerShell process prints one JSON line every few seconds,
which costs far less CPU than starting a new process for every reading.
"""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass

from .platform_utils import CREATE_NO_WINDOW, IS_WINDOWS

LOOP_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$smi = (Get-Command nvidia-smi -ErrorAction SilentlyContinue).Source
while ($true) {
    $t = @(Get-CimInstance Win32_PerfFormattedData_Counters_ThermalZoneInformation |
           ForEach-Object { $_.HighPrecisionTemperature / 10 - 273.15 })
    if (-not $t) {
        $t = @(Get-CimInstance -Namespace root/wmi MSAcpi_ThermalZoneTemperature |
               ForEach-Object { $_.CurrentTemperature / 10 - 273.15 })
    }
    $o = @{ cpu_temp = ($t | Measure-Object -Maximum).Maximum }
    if ($smi) {
        $l = (& $smi --query-gpu=temperature.gpu,utilization.gpu,fan.speed --format=csv,noheader,nounits |
              Select-Object -First 1) -split ','
        $o.gpu_temp = $l[0].Trim(); $o.gpu_load = $l[1].Trim(); $o.gpu_fan = $l[2].Trim()
    } else {
        $s = (Get-Counter '\GPU Engine(*engtype_3D)\Utilization Percentage').CounterSamples
        if ($s) { $o.gpu_load = [math]::Min(100, ($s | Measure-Object CookedValue -Sum).Sum) }
    }
    [Console]::Out.WriteLine(($o | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
    Start-Sleep -Seconds 3
}
"""


@dataclass
class Reading:
    cpu_temp: float | None = None
    gpu_temp: float | None = None
    gpu_load: float | None = None
    gpu_fan: float | None = None


def _num(value, lo: float, hi: float) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None  # e.g. "[N/A]" from nvidia-smi
    return x if lo <= x <= hi else None


def parse(line: str) -> Reading | None:
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return Reading(
        # Many desktop boards expose a fake ACPI zone stuck at ~27.8 C, so ignore readings that low.
        cpu_temp=_num(data.get("cpu_temp"), 28.5, 115),
        gpu_temp=_num(data.get("gpu_temp"), 1, 120),
        gpu_load=_num(data.get("gpu_load"), 0, 100),
        gpu_fan=_num(data.get("gpu_fan"), 0, 100),
    )


class SensorMonitor:
    def __init__(self):
        self.latest = Reading()
        self.started = False
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        if self.started or not IS_WINDOWS:
            return
        self.started = True
        try:
            self._proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-Command", LOOP_SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW,
            )
        except OSError:
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            reading = parse(line.strip())
            if reading:
                self.latest = reading

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
