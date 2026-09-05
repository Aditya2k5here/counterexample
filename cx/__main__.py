"""Command line: ``python -m cx <command>``."""

import argparse
import json
import sys

from .raft.node import BUGS
from .search.fuzz import METHODS, search
from .search.shrink import shrink, make_predicate
from .sim.scenario import run
from .viz import spacetime


def cmd_run(args):
    r = run(seed=args.seed, bugs=args.bugs or (), keep_trace=True)
    print("cluster       %d nodes" % r.n_servers)
    print("simulated     %d ms, %d events" % (r.time, r.steps))
    print("decisions     %d" % len(r.choices))
    print("client ops    %d completed" % r.ops_completed)
    print("linearizable  %s" % (r.linearizable or "not checked"))
    print("verdict       %s" % r.summary())
    return 1 if r.failed else 0


def cmd_hunt(args):
    bugs = args.bugs or ()
    print("searching with %s, budget %d, bugs=%s"
          % (args.method, args.budget, list(bugs) or "none (correct implementation)"))
    res = search(args.method, bugs=bugs, budget=args.budget, seed=args.seed)
    if not res.found:
        print("no violation in %d schedules" % res.trials)
        print("coverage: %d distinct cluster states" % res.coverage_size)
        return 0

    print("\nFOUND after %d schedules: %s" % (res.trials, res.failure.summary()))
    print("choice sequence: %d decisions" % len(res.choices))

    runner = lambda v: run(values=v, bugs=bugs, pad_zero=True)
    pred = make_predicate(runner, res.signature)
    small, stats = shrink(res.choices, pred, max_attempts=args.shrink_attempts,
                          time_budget=args.shrink_seconds)
    print("shrunk to %d decisions in %.1fs (%d candidate runs)"
          % (len(small), stats.seconds, stats.attempts))

    final = run(values=small, bugs=bugs, pad_zero=True, keep_trace=True)
    v = final.violations[0]
    servers = ["s%d" % i for i in range(final.n_servers)]
    print("\n--- minimal counterexample (%d nodes) ---" % final.n_servers)
    for t, line in spacetime.story(final.trace, v, servers):
        print("  t=%-6d %s" % (t, line))
    if args.save:
        with open(args.save, "w") as fh:
            json.dump({"bugs": list(bugs), "choices": small,
                       "violation": v.to_json()}, fh)
        print("\nreproduction written to %s" % args.save)
    return 1


def cmd_replay(args):
    with open(args.file) as fh:
        data = json.load(fh)
    r = run(values=data["choices"], bugs=data.get("bugs", ()), pad_zero=True,
            keep_trace=True)
    print("verdict: %s" % r.summary())
    if r.violations:
        servers = ["s%d" % i for i in range(r.n_servers)]
        for t, line in spacetime.story(r.trace, r.violations[0], servers):
            print("  t=%-6d %s" % (t, line))
    return 0 if not r.failed else 1


def cmd_bugs(args):
    width = max(len(b) for b in BUGS)
    for name in sorted(BUGS):
        print("%-*s  %s" % (width, name, BUGS[name]))
    return 0


def cmd_bench(args):
    from .eval.run import main as run_main
    argv = []
    if args.skip_validation:
        argv.append("--skip-validation")
    if args.skip_deep:
        argv.append("--skip-deep")
    if args.bugs:
        argv += ["--bugs"] + args.bugs
    run_main(argv)
    return 0


def cmd_report(args):
    from .viz.report import main as report_main
    report_main()
    return 0


def main(argv=None):
    # Shared options live on a parent parser so they can be written after the
    # subcommand, which is where anybody types them.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--seed", type=int, default=0)
    common.add_argument("--bugs", nargs="*", choices=sorted(BUGS), default=None,
                        help="seed one or more known Raft defects")

    ap = argparse.ArgumentParser(prog="cx", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", parents=[common],
                       help="run a single simulated cluster")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("hunt", parents=[common],
                       help="search for a failing schedule, then shrink it")
    p.add_argument("--method", default="coverage", choices=sorted(METHODS))
    p.add_argument("--budget", type=int, default=300)
    p.add_argument("--shrink-attempts", type=int, default=3000)
    p.add_argument("--shrink-seconds", type=float, default=60.0)
    p.add_argument("--save", default=None, help="write the reproduction to a file")
    p.set_defaults(func=cmd_hunt)

    p = sub.add_parser("replay", help="replay a saved reproduction")
    p.add_argument("file")
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("bugs", help="list the seeded defects")
    p.set_defaults(func=cmd_bugs)

    p = sub.add_parser("bench", help="run the full experiment")
    p.add_argument("--skip-validation", action="store_true")
    p.add_argument("--skip-deep", action="store_true")
    p.add_argument("--bugs", nargs="*", dest="bugs", default=None)
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("report", help="rebuild results/report.html and summary.md")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
