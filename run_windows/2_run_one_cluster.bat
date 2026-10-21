@echo off
title %~n0
cd /d "%~dp0.."
set PYTHONPATH=.
set PY=python
where py >nul 2>nul && set PY=py -3
echo ==== STEP 2: simulate ONE Raft cluster under faults (correct code) ====
echo WHAT: builds a 3 or 5 node cluster, throws crashes/partitions/slow links at it,
echo       runs clients against it, then checks 6 safety invariants + linearizability.
echo WHY : shows the healthy baseline - expect "no violation".
echo CMD : python -m cx run --seed 1
echo.
%PY% -m cx run --seed 1
echo.
echo Same seed = exact same run, every time, on every machine. Try --seed 2, 3...
pause
