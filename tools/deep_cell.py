#!/usr/bin/env python3
"""Run one (defect, search method) cell of the deep hunt and append the result.

Split out of the main experiment so it can be run a cell at a time. The rare
defects need a budget large enough that "not found" means something, and a
single process holding that whole sweep is both slow to iterate on and easy to
lose. One cell per invocation, appended to a JSON file, is restartable.

    python tools/deep_cell.py <bug> <method> [budget] [seeds]
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cx.search.fuzz import search                       # noqa: E402
from cx.eval.run import RESULTS_DIR, _median            # noqa: E402

OUT = os.path.join(RESULTS_DIR, "deep.json")


def main():
    bug, method = sys.argv[1], sys.argv[2]
    budget = int(sys.argv[3]) if len(sys.argv) > 3 else 1000
    seeds = int(sys.argv[4]) if len(sys.argv) > 4 else 2

    t0 = time.time()
    trials, found, sigs, first = [], 0, {}, None
    for seed in range(seeds):
        r = search(method, bugs=[bug], budget=budget, seed=100 + seed)
        trials.append(r.trials if r.found else None)
        if r.found:
            found += 1
            sigs[r.signature] = sigs.get(r.signature, 0) + 1
            if first is None:
                first = {"seed": seed, "trials": r.trials,
                         "signature": r.signature, "choices": r.choices,
                         "summary": r.failure.summary()}
    hits = [t for t in trials if t is not None]

    os.makedirs(RESULTS_DIR, exist_ok=True)
    data = {}
    if os.path.exists(OUT):
        with open(OUT) as fh:
            data = json.load(fh)
    data.setdefault(bug, {})[method] = {
        "found": found, "of": seeds, "budget": budget, "trials": trials,
        "median_trials": _median(hits) if hits else None,
        "signatures": sigs, "first": first,
        "seconds": round(time.time() - t0, 1),
    }
    with open(OUT, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)

    print("%-30s %-9s %d/%d in %d trials  %s  (%.0fs)"
          % (bug, method, found, seeds, budget, sigs or "-", time.time() - t0))


if __name__ == "__main__":
    main()
