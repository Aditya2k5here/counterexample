@echo off
title %~n0
cd /d "%~dp0.."
set PYTHONPATH=.
set PY=python
where py >nul 2>nul && set PY=py -3
echo ==== STEP 7: run the 85-test suite ====
echo WHAT: pytest over determinism, Raft, checkers, search, rendering.
echo CMD : python -m pytest tests -q   (installs pytest first if missing)
echo.
%PY% -c "import pytest" 2>nul || %PY% -m pip install pytest
set PYTHONPATH=.;tests
%PY% -m pytest tests -q -p no:cacheprovider
echo.
pause
