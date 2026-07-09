@echo off
setlocal
cd /d "%~dp0"
py -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 goto fail
py build_windows.py
if errorlevel 1 goto fail
echo.
echo Fertig: dist\ArbeitszeitTracker.exe
pause
exit /b 0

:fail
echo.
echo Build fehlgeschlagen.
pause
exit /b 1
