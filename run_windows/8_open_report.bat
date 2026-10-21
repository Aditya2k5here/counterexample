@echo off
title %~n0
cd /d "%~dp0.."
set PYTHONPATH=.
set PY=python
where py >nul 2>nul && set PY=py -3
echo ==== STEP 8: open the HTML report ====
echo WHAT: results\report.html - all 8 minimised counterexamples with space-time diagrams.
echo (rebuild it any time with: python -m cx report)
start "" "results\report.html"
