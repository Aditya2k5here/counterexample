@echo off
title %~n0
cd /d "%~dp0.."
set PYTHONPATH=.
set PY=python
where py >nul 2>nul && set PY=py -3
echo ==== STEP 6: try to break the CORRECT implementation ====
echo WHAT: same search, no bugs switched on.
echo WHY : validates the checkers - must report NO violation (zero false positives).
echo CMD : python -m cx hunt --budget 30
echo.
%PY% -m cx hunt --budget 30
echo.
pause
