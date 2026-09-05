"""One experiment: build a cluster, torture it, and check what happened.

``run(seed)`` and ``run(values=...)`` are the only two entry points the rest of
the project uses. Both return a :class:`RunResult` that either says "nothing
broke" or carries the property that broke, the exact choice sequence that broke
it, and enough trace to draw a picture.

The fault loop is written so that **choice value 0 means nothing happens**. That
one convention is what makes shrinking produce readable stories instead of
merely shorter ones: a shrinker that only knows how to make integers smaller
will, without being told anything about distributed systems, delete crashes,
heal partitions, and speed up the network -- keeping only the faults the failure
actually needs.
"""

from .choices import ChoiceStream
from .client import Client
from .world import World
from ..raft.node import RaftNode
from ..check.invariants import SafetyMonitor
from ..check import linearizability as lin

DEFAULT_SCENARIO = {
    "n_servers": None,          # drawn per run from CLUSTER_SIZES
    "n_clients": 3,
    "horizon_ms": 6000,
    "max_steps": 60000,
    "fault_min_gap_ms": 5,
    "fault_max_gap_ms": 400,
    "quiet_fraction": 0.65,     # no new faults after this fraction of the run
    "inject_faults": True,
    "check_linearizability": True,
    "check_liveness": True,
    "liveness_grace_ms": 1000,
    "coverage_stride": 8,
}

# Fault 0 is deliberately "nothing": shrinking toward zero removes faults.
#: Cluster size is part of the search space, not a constant. Three nodes and
#: five nodes fail in different ways -- a single double-voting node is enough to
#: elect two leaders in a three-node cluster but not in a five-node one -- and
#: fixing the size would silently put half the interesting failures out of reach.
#: Value 0 draws the smaller cluster, so shrinking prefers the simpler picture.
CLUSTER_SIZES = (3, 5)

# "bounce" is a process restart: crash and come back a moment later. It is a
# separate fault rather than a lucky pairing of "crash" then "restart" because a
# real node reboot takes tens of milliseconds, and several classes of bug -- any
# bug about state that was supposed to be persisted -- are only reachable inside
# that window. Waiting for the injector to randomly pick crash-then-restart on
# the same node within one election timeout is waiting for a coincidence.
FAULTS = ("none", "crash", "restart", "partition", "heal", "one_way_block",
          "bounce")


class RunResult(object):
    def __init__(self):
        self.violations = []
        self.linearizable = None
        self.lin_info = None
        self.trace = None
        self.history = []
        self.choices = []
        self.coverage = frozenset()
        self.steps = 0
        self.time = 0
        self.ops_completed = 0
        self.n_servers = None
        self.error = None

    @property
    def failed(self):
        return bool(self.violations) or self.linearizable == lin.NOT_LINEARIZABLE

    def summary(self):
        if self.violations:
            v = self.violations[0]
            return "%s: %s" % (v.prop, v.detail)
        if self.linearizable == lin.NOT_LINEARIZABLE:
            return "Linearizability: history has no valid sequential explanation"
        return "no violation"

    def signature(self):
        """What kind of failure this is -- used to tell bugs apart, not runs."""
        if self.violations:
            return self.violations[0].prop
        if self.linearizable == lin.NOT_LINEARIZABLE:
            return "Linearizability"
        return None


def _install_faults(world, servers, cfg, ch):
    """Schedule the fault-injection loop across the run."""
    quiet_after = int(cfg["horizon_ms"] * cfg["quiet_fraction"])

    def tick():
        if world.time >= quiet_after:
            # End of the storm: heal everything and let the cluster recover, so
            # that "did it come back?" is a fair question to ask.
            world.network.heal()
            for s in servers:
                world.restart(s)
            world.quiet_started = world.time
            world.commit_at_quiet = max(
                world.nodes[s].commit_index for s in servers)
            return
        kind = ch.draw_choice(FAULTS, "fault")
        if kind == "crash":
            world.crash(ch.draw_choice(servers, "crash_node"))
        elif kind == "restart":
            down = sorted(world.crashed)
            if down:
                world.restart(ch.draw_choice(down, "restart_node"))
        elif kind == "partition":
            # A random cut into two groups. Value 0 puts everyone in group 0,
            # i.e. no partition at all.
            groups = {s: ch.draw(2, "side") for s in servers}
            world.network.partition(groups)
        elif kind == "heal":
            world.network.heal()
        elif kind == "bounce":
            node = ch.draw_choice(servers, "bounce_node")
            if world.alive(node):
                world.crash(node)
                back = ch.draw_range(5, 120, "bounce_ms")
                world.schedule(back, "restart", node)
        elif kind == "one_way_block":
            a = ch.draw_choice(servers, "block_src")
            b = ch.draw_choice(servers, "block_dst")
            if a != b:
                world.network.block_one_way(a, b)
        gap = ch.draw_range(cfg["fault_min_gap_ms"], cfg["fault_max_gap_ms"], "fault_gap")
        world.schedule(gap, "client", None, tick)

    first = ch.draw_range(cfg["fault_min_gap_ms"], cfg["fault_max_gap_ms"], "fault_gap")
    world.schedule(first, "client", None, tick)


def _coverage_hook(world, servers, cfg, sink):
    stride = cfg["coverage_stride"]
    state = {"n": 0}

    def hook(w, ev):
        state["n"] += 1
        if state["n"] % stride:
            return
        roles = []
        terms = []
        commits = []
        log_lens = []
        last_terms = set()
        for s in servers:
            node = w.nodes[s]
            roles.append(node.role[0] if w.alive(s) else "x")
            terms.append(node.current_term)
            commits.append(node.commit_index)
            log_lens.append(len(node.log))
            last_terms.add(node.log[-1].term)
        lo, hi = min(terms), max(terms)
        # Deliberately generic: role mix, how many nodes are down, how the
        # cluster is cut, and how far apart the replicas' terms, commit indices
        # and logs have drifted. Nothing here mentions a specific bug -- a
        # coverage metric tuned to the bugs it is meant to find would make the
        # comparison against random search meaningless.
        sink.add((
            ev.kind,
            tuple(sorted(roles)),
            len(w.crashed),
            len(set(w.network.group.values())) if w.network.group else 1,
            min(hi - lo, 3),
            min(max(commits) - min(commits), 3),
            min(max(commits), 6),
            min(max(log_lens) - min(log_lens), 4),
            min(len(last_terms), 4),
        ))

    return hook


def run(seed=0, values=None, bugs=(), config=None, raft_config=None,
        world_config=None, strict=False, pad_zero=False, keep_trace=False):
    cfg = dict(DEFAULT_SCENARIO, **(config or {}))
    ch = ChoiceStream(seed=seed, values=values, strict=strict, pad_zero=pad_zero)
    result = RunResult()

    n_servers = cfg["n_servers"]
    if n_servers is None:
        n_servers = ch.draw_choice(CLUSTER_SIZES, "cluster")
    servers = ["s%d" % i for i in range(n_servers)]
    clients = ["c%d" % i for i in range(cfg["n_clients"])]

    world = World(ch, config=world_config, max_steps=cfg["max_steps"])
    world.network.exempt.update(clients)

    for s in servers:
        world.add_node(RaftNode(s, servers, bugs=bugs, config=raft_config))
    client_objs = []
    for c in clients:
        cl = Client(c, servers)
        world.add_node(cl)
        client_objs.append(cl)

    monitor = SafetyMonitor(world, servers)
    coverage = set()
    world.hooks.append(monitor.on_event)
    world.hooks.append(_coverage_hook(world, servers, cfg, coverage))

    for s in servers:
        world.nodes[s].start()
    for cl in client_objs:
        cl.start()
    if cfg["inject_faults"]:
        _install_faults(world, servers, cfg, ch)

    try:
        world.run(until_time=cfg["horizon_ms"])
    except Exception as exc:                      # a crash *is* a counterexample
        result.error = "%s: %s" % (type(exc).__name__, exc)
        monitor.fail("Crash", result.error)

    monitor.finish()
    result.violations = monitor.violations
    result.steps = world.steps
    result.time = world.time
    result.coverage = frozenset(coverage)
    result.choices = ch.consumed()

    history = []
    for cl in client_objs:
        history.extend(cl.history)
    history.sort(key=lambda e: (e.time, e.process))
    result.history = history
    result.ops_completed = sum(1 for e in history if e.kind == "ok")
    result.n_servers = n_servers

    if cfg["check_linearizability"] and not result.violations:
        verdict, info = lin.check_history(history)
        result.linearizable = verdict
        result.lin_info = info

    if cfg["check_liveness"] and not result.failed:
        _check_liveness(world, servers, history, cfg, monitor)
        result.violations = monitor.violations

    if keep_trace:
        result.trace = world.trace
    return result


def _check_liveness(world, servers, history, cfg, monitor):
    """After the network heals and everyone is back, progress must resume.

    Stated as something a user would notice -- *some client operation
    completed* -- rather than "there is exactly one leader at this instant".
    The latter is brittle in a way that produces false alarms: a perfectly
    healthy cluster is leaderless for a couple of hundred milliseconds every
    time an election runs, and whether the run happens to end during one is
    luck, not a bug.

    The check is skipped entirely unless the run actually reached the calm
    period and stayed there long enough to recover. Reporting "it never
    recovered" about a run that was cut off mid-storm would be a lie.
    """
    quiet_started = getattr(world, "quiet_started", None)
    if quiet_started is None:
        return
    recovery_window = world.time - quiet_started
    if recovery_window < cfg["liveness_grace_ms"]:
        return

    # The property is stated at the cluster level -- *something* got committed
    # after the storm ended -- rather than "a client operation completed".
    # Client-level progress is the more natural phrasing and the wrong one to
    # check: a client that happens to be four hundred milliseconds into a
    # retry when the network heals may legitimately not finish inside the
    # window, and a liveness alarm that fires on unlucky timing is worse than
    # no liveness alarm at all.
    before = getattr(world, "commit_at_quiet", 0)
    after = max(world.nodes[s].commit_index for s in servers)
    if after <= before:
        monitor.fail("Liveness",
                     "no entry was committed in the %dms after the network healed "
                     "and every node restarted (commit index stuck at %d)"
                     % (recovery_window, after))
