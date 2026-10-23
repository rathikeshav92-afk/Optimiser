@echo off
REM Build a single PCOptimizer.exe (asks for admin rights when opened).
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller -r requirements.txt || goto :error
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --collect-submodules anthropic ^
    --name PCOptimizer launcher.py || goto :error
echo.
echo Done! Your app is in the "dist" folder: dist\PCOptimizer.exe
pause
exit /b 0
:error
echo Build failed.
pause
exit /b 1
