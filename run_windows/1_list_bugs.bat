@echo off
title %~n0
cd /d "%~dp0.."
set PYTHONPATH=.
set PY=python
where py >nul 2>nul && set PY=py -3
echo ==== STEP 1: list the 8 seeded Raft defects ====
echo WHAT: prints the bugs you can switch on in the Raft implementation.
echo WHY : these are the "ground truth" the search must find.
echo CMD : python -m cx bugs
echo.
%PY% -m cx bugs
echo.
pause
