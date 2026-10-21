@echo off
title %~n0
cd /d "%~dp0.."
set PYTHONPATH=.
set PY=python
where py >nul 2>nul && set PY=py -3
echo ==== STEP 0: is Python installed? ====
echo This project needs Python 3.8+ and NOTHING else (zero dependencies).
echo.
%PY% --version
%PY% --version > run_windows\python_check.log 2>&1
echo.
echo If you see "Python 3.x.x" above, you are good. If not, install Python from python.org
echo (tick "Add python.exe to PATH" during install).
pause
