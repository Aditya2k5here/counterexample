@echo off
title %~n0
cd /d "%~dp0.."
set PYTHONPATH=.
set PY=python
where py >nul 2>nul && set PY=py -3
echo ==== STEP 4: HUNT for a failing schedule, then SHRINK it ====
echo WHAT: coverage-guided search tries schedules until an invariant breaks,
echo       then delta-debugging deletes/zeroes decisions until only the ones
echo       that matter remain, and prints the failure as a numbered story.
echo WHY : this is the core of the project - find + minimise + explain.
echo CMD : python -m cx hunt --bugs commit_index_unclamped --budget 100 --shrink-seconds 40 --save repro.json
echo (takes up to ~1 minute)
echo.
%PY% -m cx hunt --bugs commit_index_unclamped --budget 100 --shrink-seconds 40 --save repro.json
echo.
pause
