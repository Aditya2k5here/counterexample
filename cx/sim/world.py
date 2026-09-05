"""The deterministic world: a virtual clock, an event queue, and a hostile network.

Nothing in here touches wall-clock time, threads, sockets or ``random``. A node
cannot tell the difference -- it sends messages, sets timers, and is occasionally
killed -- but the entire run is a pure function of the choice sequence, so
"reproduce the failure" means "pass back this list of integers".

The network is where the interesting cruelty lives. Messages can be delayed by
anything from a tick to several election timeouts, dropped, duplicated, and
reordered arbitrarily. Nodes can crash (losing all volatile state, keeping what
they persisted) and restart. The cluster can be partitioned into arbitrary
groups, including the asymmetric case where A can reach B but B cannot reach A --
which is the shape of partition that breaks the most implementations and the one
people forget to test.

Every one of those decisions is drawn from the choice stream, and every one of
them is minimal at zero, so a shrunk counterexample is one where almost nothing
went wrong except the part that mattered.
"""

import heapq

# Event kinds
DELIVER = "deliver"
TIMER = "timer"
CRASH = "crash"
RESTART = "restart"
PARTITION = "partition"
HEAL = "heal"
CLIENT = "client"


class Event(object):
    __slots__ = ("time", "seq", "kind", "node", "payload", "cancelled")

    def __init__(self, time, seq, kind, node, payload):
        self.time = time
        self.seq = seq
        self.kind = kind
        self.node = node
        self.payload = payload
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def __lt__(self, other):
        # Total order, so the heap is deterministic even for simultaneous events.
        return (self.time, self.seq) < (other.time, other.seq)

    def __repr__(self):
        return "<%s t=%d node=%s %r>" % (self.kind, self.time, self.node, self.payload)


class Trace(object):
    """Everything that happened, in order. Feeds the checkers and the diagrams."""

    def __init__(self, limit=200000):
        self.records = []
        self.limit = limit

    def add(self, time, kind, **fields):
        if len(self.records) < self.limit:
            fields["t"] = time
            fields["kind"] = kind
            self.records.append(fields)

    def of_kind(self, *kinds):
        s = set(kinds)
        return [r for r in self.records if r["kind"] in s]

    def __len__(self):
        return len(self.records)


class Network(object):
    """Delays, drops, duplicates and partitions -- all drawn from the stream."""

    def __init__(self, world, config):
        self.world = world
        self.cfg = config
        # node -> partition group id. Nodes in different groups cannot talk.
        self.group = {}
        # asymmetric one-way blocks: set of (src, dst) that silently drop
        self.blocked = set()
        # Clients sit outside the partition model. A partition is a statement
        # about the replicas' ability to reach each other; modelling the client
        # as stuck on one side as well would mostly just stop the workload,
        # which hides bugs rather than finding them.
        self.exempt = set()

    def reachable(self, src, dst):
        if (src, dst) in self.blocked:
            return False
        if src in self.exempt or dst in self.exempt:
            return True
        return self.group.get(src, 0) == self.group.get(dst, 0)

    def partition(self, groups):
        """``groups`` maps node id -> group id."""
        self.group = dict(groups)
        self.world.trace.add(self.world.time, "partition", groups=dict(groups))

    def heal(self):
        self.group = {}
        self.blocked = set()
        self.world.trace.add(self.world.time, "heal")

    def block_one_way(self, src, dst):
        self.blocked.add((src, dst))
        self.world.trace.add(self.world.time, "block", src=src, dst=dst)

    def send(self, src, dst, msg):
        w = self.world
        ch = w.choices
        if not w.alive(src):
            return
        if not self.reachable(src, dst):
            w.trace.add(w.time, "drop", src=src, dst=dst, msg=msg, why="partition")
            return
        if ch.draw_bool(self.cfg["drop_percent"], "drop"):
            w.trace.add(w.time, "drop", src=src, dst=dst, msg=msg, why="loss")
            return
        delay = self._delay()
        w.trace.add(w.time, "send", src=src, dst=dst, msg=msg, delay=delay)
        w.schedule(delay, DELIVER, dst, (src, msg))
        if ch.draw_bool(self.cfg["duplicate_percent"], "dup"):
            extra = delay + self._delay()
            w.trace.add(w.time, "send", src=src, dst=dst, msg=msg, delay=extra, dup=True)
            w.schedule(extra, DELIVER, dst, (src, msg))

    def _delay(self):
        ch = self.world.choices
        # Value 0 gives the minimum delay, so shrinking pulls the network toward
        # being fast and boring.
        if ch.draw_bool(self.cfg["slow_percent"], "slow"):
            return ch.draw_range(self.cfg["slow_min_ms"], self.cfg["slow_max_ms"], "slow_ms")
        return ch.draw_range(self.cfg["min_ms"], self.cfg["max_ms"], "ms")


DEFAULT_CONFIG = {
    "min_ms": 1,
    "max_ms": 12,
    "slow_percent": 12,
    "slow_min_ms": 40,
    "slow_max_ms": 400,
    "drop_percent": 4,
    "duplicate_percent": 2,
}


class World(object):
    def __init__(self, choices, config=None, max_steps=60000, trace_limit=200000):
        self.choices = choices
        self.cfg = dict(DEFAULT_CONFIG, **(config or {}))
        self.time = 0
        self.seq = 0
        self.queue = []
        self.nodes = {}
        self.crashed = set()
        self.trace = Trace(trace_limit)
        self.network = Network(self, self.cfg)
        self.max_steps = max_steps
        self.steps = 0
        self.hooks = []           # called after every event: fn(world, event)
        self.stop_reason = None
        self.quiet_started = None  # when fault injection stopped, if it did
        self.commit_at_quiet = 0   # highest commit index at that moment

    # ------------------------------------------------------------- scheduling
    def schedule(self, delay, kind, node, payload=None):
        self.seq += 1
        ev = Event(self.time + max(0, delay), self.seq, kind, node, payload)
        heapq.heappush(self.queue, ev)
        return ev

    def at(self, time, kind, node, payload=None):
        return self.schedule(time - self.time, kind, node, payload)

    # ------------------------------------------------------------------ nodes
    def add_node(self, node):
        self.nodes[node.id] = node
        node.world = self

    def alive(self, node_id):
        return node_id not in self.crashed

    def crash(self, node_id):
        if node_id in self.crashed:
            return
        self.crashed.add(node_id)
        self.trace.add(self.time, "crash", node=node_id)
        self.nodes[node_id].on_crash()

    def restart(self, node_id):
        if node_id not in self.crashed:
            return
        self.crashed.discard(node_id)
        self.trace.add(self.time, "restart", node=node_id)
        self.nodes[node_id].on_restart()

    def send(self, src, dst, msg):
        self.network.send(src, dst, msg)

    # -------------------------------------------------------------- main loop
    def step(self):
        while self.queue:
            ev = heapq.heappop(self.queue)
            if ev.cancelled:
                continue
            self.time = ev.time
            self.steps += 1
            self._dispatch(ev)
            for hook in self.hooks:
                hook(self, ev)
            return ev
        return None

    def run(self, until_time=None):
        while True:
            if self.steps >= self.max_steps:
                self.stop_reason = "max_steps"
                return
            if until_time is not None and self.queue and self.queue[0].time > until_time:
                self.time = until_time
                self.stop_reason = "deadline"
                return
            ev = self.step()
            if ev is None:
                self.stop_reason = "quiescent"
                return

    def _dispatch(self, ev):
        if ev.kind == DELIVER:
            src, msg = ev.payload
            if not self.alive(ev.node):
                self.trace.add(self.time, "drop", src=src, dst=ev.node, msg=msg,
                               why="crashed")
                return
            self.trace.add(self.time, "deliver", src=src, dst=ev.node, msg=msg)
            self.nodes[ev.node].on_message(src, msg)
        elif ev.kind == TIMER:
            if not self.alive(ev.node):
                return
            self.trace.add(self.time, "timer", node=ev.node, name=ev.payload)
            self.nodes[ev.node].on_timer(ev.payload)
        elif ev.kind == CRASH:
            self.crash(ev.node)
        elif ev.kind == RESTART:
            self.restart(ev.node)
        elif ev.kind == PARTITION:
            self.network.partition(ev.payload)
        elif ev.kind == HEAL:
            self.network.heal()
        elif ev.kind == CLIENT:
            ev.payload()
        else:                                     # pragma: no cover - defensive
            raise AssertionError("unknown event kind %r" % ev.kind)
