#!/usr/bin/env python3
"""Re-shrink one defect's recorded failure with a larger budget, in place.

Shrinking is the slowest part of the experiment and the easiest to want more of
after seeing the first pass, so it is runnable one defect at a time against an
existing ``results.json``.

    python tools/reshrink.py <bug> [seconds] [attempts]
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cx.eval.run import RESULTS_DIR                     # noqa: E402
from cx.search.shrink import shrink, make_predicate     # noqa: E402
from cx.sim.scenario import run                         # noqa: E402

RESULTS = os.path.join(RESULTS_DIR, "results.json")
RAW = os.path.join(RESULTS_DIR, "raw_failures.json")


def main():
    bug = sys.argv[1]
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    attempts = int(sys.argv[3]) if len(sys.argv) > 3 else 20000

    with open(RESULTS) as fh:
        results = json.load(fh)
    with open(RAW) as fh:
        raw = json.load(fh)
    if bug not in raw:
        print("no recorded failure for %s" % bug)
        return 1

    entry = raw[bug]
    runner = lambda v: run(values=v, bugs=[bug], pad_zero=True)
    pred = make_predicate(runner, entry["signature"])
    small, stats = shrink(entry["choices"], pred,
                          max_attempts=attempts, time_budget=seconds)

    check = run(values=small, bugs=[bug], pad_zero=True)
    assert check.failed and check.signature() == entry["signature"], \
        "the shrunk sequence stopped reproducing -- refusing to record it"

    results.setdefault("shrunk", {})[bug] = {
        "signature": entry["signature"], "choices": small,
        "stats": stats.to_json(), "summary": entry["summary"],
    }
    with open(RESULTS, "w") as fh:
        json.dump(results, fh, indent=2, sort_keys=True)
    print("%-30s %6d -> %4d decisions (%.0f%% smaller, %.0fs, %d candidates)"
          % (bug, stats.start_len, stats.end_len,
             100 * (1 - stats.end_len / max(1, stats.start_len)),
             stats.seconds, stats.attempts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
