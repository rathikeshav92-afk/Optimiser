# ⚡ PC Optimizer

A Windows desktop app that shows your PC's specs, rates it out of 100, and speeds it up:
Gaming Mode, Balanced Mode, registry and Windows tweaks, a junk cleaner, a RAM booster
and a toolkit of maintenance commands. **Every change it makes is backed up and can be undone.**

![Dashboard](docs/dashboard.png)

## Features

| Section | What it does |
|---|---|
| **Dashboard** | Full specs (CPU, GPU + VRAM, RAM speed and sticks, every drive and its type, motherboard, display, Windows build), a **performance rating out of 100** with a per-component breakdown and your weakest part, live CPU/RAM/disk usage, and **One-Click Boost**. |
| **Gaming Mode** | Ultimate Performance power plan, Game Mode, background recording (Game DVR) off, hardware-accelerated GPU scheduling, CPU/GPU priority for games (MMCSS), no network throttling, no CPU power throttling, raw mouse input. Lists RAM-heavy background apps so you can close them, and gives driver and in-game setting tips based on your GPU, RAM and monitor. |
| **Balanced Mode** | Switches to the Balanced power plan and puts back your original values for the power-hungry gaming tweaks. Your general speed tweaks stay on. Use it for everyday work and battery life. |
| **Speed Up PC** | Visual effects set to best performance (smooth fonts kept), transparency off, no startup-app delay, faster menus, Store apps stopped from running in the background, ads and suggestions off, web results removed from Start search, less telemetry, faster shutdown, NTFS last-access timestamps off, optional hibernation off. Also has a **startup-apps manager** that works the same way as Task Manager. |
| **Cleaner & RAM** | Scans and cleans user and Windows temp files, the Windows Update cache, the Delivery Optimization cache, thumbnail caches, crash dumps, old logs, browser caches and the Recycle Bin. **Free up RAM** trims the memory every app holds, and you can flush the DNS cache. |
| **CMD Toolkit** | One-click `sfc /scannow`, `DISM /RestoreHealth`, `chkdsk /scan`, WinSxS cleanup, TRIM/defrag, Disk Cleanup, DNS flush, IP renew, Winsock/TCP reset, TCP auto-tuning, restore point, `winget upgrade --all`, Explorer restart and a power report. Output streams live. You can also run your own commands. |
| **Backup & Restore** | Create a restore point, see every change that's currently applied, undo changes one by one or all at once, and open System Restore. |

## Getting it on your PC

### Option A: download the ready-made .exe (easiest)
1. Open this repository on GitHub and go to **Actions → "Test & build Windows app"**.
2. Open the latest green run and download the **PCOptimizer** artifact (a zip).
3. Unzip it and double-click **PCOptimizer.exe**. Click **Yes** on the administrator prompt.

> Windows SmartScreen may warn about an unsigned app: click **More info → Run anyway**.

### Option B: run from source
1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/) and tick **"Add python.exe to PATH"**.
2. Download this repository (**Code → Download ZIP**) and unzip it.
3. Double-click **`run.bat`**.

To build your own `.exe`, double-click **`build_exe.bat`**. The file ends up in `dist\PCOptimizer.exe`.

## Recommended first run
1. **Backup & Restore → Create restore point**
2. **Dashboard → One-Click Boost**
3. **Gaming Mode → Activate** if you play games, or **Balanced Mode** for work.
4. **Speed Up PC → Startup apps**: disable anything you don't need at boot.
5. **CMD Toolkit**: run *Repair Windows image (DISM)* and then *Repair system files (SFC)* once.
6. **Restart your PC.**

## Safety
- Before it changes a registry value, the app saves the original to
  `%LOCALAPPDATA%\PCOptimizer\tweak_backup.json`. **Undo** writes that original value back
  exactly, and deletes values that didn't exist before.
- Nothing here turns off Windows Defender, the firewall, Windows Update or other security features.
- Startup apps are disabled the same way Task Manager does it. Nothing is deleted.
- The cleaner skips files that are in use, temp files newer than 2 hours, and any path that isn't absolute.
- Windows system processes are hidden and protected in the "Close background apps" list.

## Development
```bash
pip install -r requirements-dev.txt
python -m pytest -q             # core logic tests (run on any OS)
python -m pcoptimizer --demo    # preview the UI with sample data; changes nothing
```

Project layout:
```
pcoptimizer/
  system_info.py   hardware detection (one PowerShell/CIM call) and live CPU/RAM meters
  rating.py        0-100 scoring
  tweaks.py        reversible tweak catalogue and JSON backup manager
  profiles.py      Gaming / Balanced modes
  cleaner.py       junk scanning and cleaning, RAM trimming
  processes.py     background apps and startup-app manager
  commands.py      CMD Toolkit catalogue
  backend.py       facade used by the UI, plus a demo backend
  ui/              Tkinter interface (theme, widgets, pages)
```
