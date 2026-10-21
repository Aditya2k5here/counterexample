@echo off
title %~n0
cd /d "%~dp0.."
set PYTHONPATH=.
set PY=python
where py >nul 2>nul && set PY=py -3
echo ==== STEP 3: same run, but with a seeded bug switched on ====
echo WHAT: turns on commit_index_unclamped and runs a few seeds.
echo WHY : shows the checkers catching a broken implementation.
echo CMD : python -m cx run --seed N --bugs commit_index_unclamped
echo.
for %%s in (1 2 3) do (
  echo ---- seed %%s ----
  %PY% -m cx run --seed %%s --bugs commit_index_unclamped
  echo.
)
pause
