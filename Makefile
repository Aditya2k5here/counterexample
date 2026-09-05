PY ?= python3

.PHONY: help test bench report figures hunt clean all

help:
	@echo "make test     run the test suite (~45s)"
	@echo "make bench    run the full experiment -> results/results.json (~30min)"
	@echo "make report   rebuild results/report.html and results/summary.md"
	@echo "make hunt     search for a failure in the correct implementation"
	@echo "make figures  re-render the README figures (needs playwright)"

test:
	PYTHONPATH=.:tests $(PY) -m pytest tests -q

bench:
	PYTHONPATH=. $(PY) -m cx bench

report:
	PYTHONPATH=. $(PY) -m cx report

hunt:
	PYTHONPATH=. $(PY) -m cx hunt --budget 500

figures:
	PYTHONPATH=. $(PY) tools/render_figures.py

all: bench report

clean:
	rm -rf __pycache__ */__pycache__ */*/__pycache__ .pytest_cache
