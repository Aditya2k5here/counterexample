@echo off
title %~n0
cd /d "%~dp0.."
set PYTHONPATH=.
set PY=python
where py >nul 2>nul && set PY=py -3
echo ==== STEP 5: REPLAY the saved failure ====
echo WHAT: re-runs the exact list of integers saved in repro.json.
echo WHY : proves determinism - the bug reproduces perfectly, forever.
echo CMD : python -m cx replay repro.json
echo.
if not exist repro.json (echo Run 4_hunt_and_shrink.bat first. & pause & exit /b)
%PY% -m cx replay repro.json
echo.
pause
