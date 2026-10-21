@echo off
title %~n0
cd /d "%~dp0.."
set PYTHONPATH=.
set PY=python
where py >nul 2>nul && set PY=py -3
echo ==== STEP 9 (optional): the FULL experiment, ~30+ minutes ====
echo WHAT: validation on correct Raft, all 8 bugs x 3 searches x 6 seeds, deep runs, shrinking.
echo CMD : python -m cx bench  then  python -m cx report
echo Close this window now if you do not want to wait.
pause
%PY% -m cx bench
%PY% -m cx report
pause
