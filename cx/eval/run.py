"""The experiment.

Three questions, in the order they have to be answered:

**1. Does the checker ever cry wolf?** Every search method is run against the
*correct* implementation for thousands of adversarial schedules. A single false
positive would make every other number in this repository worthless, so this
runs first and its result is reported first.

**2. Which bugs get found, and how quickly?** Eight deliberately seeded Raft
defects, three search strategies, several independent seeds each. The metric is
trials until the first violation, capped at a budget -- and because the correct
implementation survives the same search, a violation really is attributable to
the seeded defect.

**3. Is the reproduction usable?** A failure that takes forty thousand
scheduling decisions to describe is not a bug report. Each bug's first failure
is shrunk, and the reduction is measured.

Everything is deterministic. The same command produces the same numbers.
"""

import argparse
import json
import os
import time

from ..raft.node import BUGS
from ..search.fuzz import search
from ..search.shrink import shrink, make_predicate
from ..sim.scenario import run

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RESULTS_DIR = os.path.join(ROOT, "results")

CONFIG = {
    "methods": ["random", "coverage", "bandit"],
    "bug_seeds": 6,
    "bug_budget": 300,
    "validation_seeds": 4,
    "validation_budget": 250,
    "shrink_attempts": 12000,
    "shrink_seconds": 90.0,
    # The rare defects need a far bigger sample before "not found" means
    # anything. Running them at the same budget as the easy ones and calling it
    # a comparison would be an underpowered experiment dressed up as a result.
    "deep_budget": 1000,
    "deep_seeds": 2,
    "deep_threshold": 3,       # a bug found by fewer than this many seeds is rare
}

BUG_ORDER = [
    "commit_index_unclamped",
    "truncate_on_every_append",
    "no_log_up_to_date_check",
    "ignore_prev_log_term",
    "accept_stale_term",
    "no_step_down_on_higher_term",
    "no_persist_voted_for",
    "commit_any_term",
]


def validate_no_false_positives(log=print):
    """Hunt for failures in the *correct* implementation. Finding one is a bug
    in the checkers, not a result."""
    log("\n=== validation: searching the correct implementation ===")
    out = {}
    for method in CONFIG["methods"]:
        t0 = time.time()
        failures = []
        for seed in range(CONFIG["validation_seeds"]):
            r = search(method, bugs=(), budget=CONFIG["validation_budget"], seed=seed)
            if r.found:
                failures.append({"seed": seed, "trials": r.trials,
                                 "signature": r.signature,
                                 "summary": r.failure.summary()})
        runs = CONFIG["validation_seeds"] * CONFIG["validation_budget"]
        out[method] = {"schedules": runs, "false_positives": len(failures),
                       "detail": failures, "seconds": round(time.time() - t0, 1)}
        log("  %-9s %6d schedules, %d false positives  (%.0fs)"
            % (method, runs, len(failures), time.time() - t0))
    return out


def bug_benchmark(log=print):
    log("\n=== bug benchmark: trials until the first violation ===")
    budget = CONFIG["bug_budget"]
    results = {}
    for bug in BUG_ORDER:
        results[bug] = {"description": BUGS[bug], "methods": {}}
        row = []
        for method in CONFIG["methods"]:
            trials, found, sigs = [], 0, {}
            first = None
            for seed in range(CONFIG["bug_seeds"]):
                r = search(method, bugs=[bug], budget=budget, seed=seed)
                trials.append(r.trials if r.found else None)
                if r.found:
                    found += 1
                    sigs[r.signature] = sigs.get(r.signature, 0) + 1
                    if first is None:
                        first = {"seed": seed, "trials": r.trials,
                                 "signature": r.signature,
                                 "choices": r.choices,
                                 "summary": r.failure.summary()}
            hits = [t for t in trials if t is not None]
            results[bug]["methods"][method] = {
                "found": found,
                "of": CONFIG["bug_seeds"],
                "trials": trials,
                "median_trials": _median(hits) if hits else None,
                "mean_trials": (sum(hits) / len(hits)) if hits else None,
                "signatures": sigs,
                "first": first,
            }
            row.append("%s %d/%d%s" % (method, found, CONFIG["bug_seeds"],
                                       "" if not hits else " med=%g" % _median(hits)))
        log("  %-30s %s" % (bug, "   ".join(row)))
    return results


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def shrink_examples(bench, log=print):
    log("\n=== shrinking ===")
    out = {}
    for bug, entry in bench.items():
        best = None
        for method in CONFIG["methods"]:
            first = entry["methods"][method]["first"]
            if first and (best is None or len(first["choices"]) < len(best["choices"])):
                best = first
        if best is None:
            log("  %-30s not found by any method" % bug)
            continue
        # pad_zero: a shrunk sequence must fully determine the run.
        runner = lambda v, b=bug: run(values=v, bugs=[b], pad_zero=True)
        pred = make_predicate(runner, best["signature"])
        small, stats = shrink(best["choices"], pred,
                              max_attempts=CONFIG["shrink_attempts"],
                              time_budget=CONFIG["shrink_seconds"])
        out[bug] = {"signature": best["signature"], "choices": small,
                    "stats": stats.to_json(), "summary": best["summary"]}
        log("  %-30s %6d -> %4d decisions (%.0f%% smaller, %.0fs)"
            % (bug, stats.start_len, stats.end_len,
               100 * (1 - stats.end_len / max(1, stats.start_len)), stats.seconds))
    return out


def deep_hunt(bench, log=print):
    """Give the rare defects a much larger budget.

    This is where guidance has a chance to matter. On a defect that fires on
    most schedules, a corpus-mutating search is *slower* than random -- it
    spends its trials perturbing a corpus when it could just roll the dice. The
    interesting question is what happens when the target needs a specific
    interleaving that random sampling reaches roughly never.
    """
    rare = [bug for bug, e in bench.items()
            if max(e["methods"][m]["found"] for m in CONFIG["methods"])
            < CONFIG["deep_threshold"]]
    if not rare:
        return {}
    log("\n=== deep hunt: %d rare defects at %d trials x %d seeds ==="
        % (len(rare), CONFIG["deep_budget"], CONFIG["deep_seeds"]))
    out = {}
    for bug in rare:
        out[bug] = {}
        row = []
        for method in CONFIG["methods"]:
            t0 = time.time()
            trials, found = [], 0
            for seed in range(CONFIG["deep_seeds"]):
                r = search(method, bugs=[bug], budget=CONFIG["deep_budget"],
                           seed=100 + seed)
                trials.append(r.trials if r.found else None)
                found += bool(r.found)
            hits = [t for t in trials if t is not None]
            out[bug][method] = {
                "found": found, "of": CONFIG["deep_seeds"], "trials": trials,
                "median_trials": _median(hits) if hits else None,
                "seconds": round(time.time() - t0, 1),
            }
            row.append("%s %d/%d%s" % (method, found, CONFIG["deep_seeds"],
                                       "" if not hits else " med=%g" % _median(hits)))
        log("  %-30s %s" % (bug, "   ".join(row)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="run the counterexample experiment")
    ap.add_argument("--out", default=os.path.join(RESULTS_DIR, "results.json"))
    ap.add_argument("--skip-validation", action="store_true")
    ap.add_argument("--skip-deep", action="store_true")
    ap.add_argument("--bugs", nargs="*", default=None)
    args = ap.parse_args(argv)

    if args.bugs:
        global BUG_ORDER
        BUG_ORDER = [b for b in BUG_ORDER if b in args.bugs]

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    started = time.time()

    validation = None if args.skip_validation else validate_no_false_positives()
    bench = bug_benchmark()
    deep = {} if args.skip_deep else deep_hunt(bench)
    shrunk = shrink_examples(bench)

    # strip the bulky choice sequences out of the benchmark section
    for bug in bench:
        for m in bench[bug]["methods"]:
            first = bench[bug]["methods"][m]["first"]
            if first:
                first["choices_len"] = len(first["choices"])
                del first["choices"]

    payload = {
        "config": CONFIG,
        "bugs": BUGS,
        "validation": validation,
        "benchmark": bench,
        "deep": deep,
        "shrunk": shrunk,
        "seconds": round(time.time() - started, 1),
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    print("\nwrote %s in %.0fs" % (args.out, time.time() - started))
    return payload


if __name__ == "__main__":
    main()
