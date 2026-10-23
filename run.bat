@echo off
REM Launch PC Optimizer from source. Requires Python 3.10+ from python.org
REM (tick "Add python.exe to PATH" and keep "tcl/tk and IDLE" selected while installing).
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install it from https://www.python.org/downloads/ and try again,
    echo or download the ready-made PCOptimizer.exe from the GitHub Actions build.
    pause
    exit /b 1
)
start "" pythonw -m pcoptimizer %*
