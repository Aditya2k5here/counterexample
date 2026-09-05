"""The simulated world: ordering, the network, crashes, and the trace."""

from cx.sim.choices import ChoiceStream
from cx.sim.world import World, DELIVER, TIMER, Event


class Recorder(object):
    """A minimal node: records what it is told, sends what it is asked to."""

    def __init__(self, node_id):
        self.id = node_id
        self.world = None
        self.seen = []
        self.timers = []
        self.crashes = 0
        self.restarts = 0

    def on_message(self, src, msg):
        self.seen.append((self.world.time, src, msg))

    def on_timer(self, name):
        self.timers.append((self.world.time, name))

    def on_crash(self):
        self.crashes += 1
        self.seen = []          # volatile state is lost

    def on_restart(self):
        self.restarts += 1


def make_world(values=None, **cfg):
    ch = ChoiceStream(values=values or [0] * 5000, pad_zero=True)
    w = World(ch, config=dict({"drop_percent": 0, "duplicate_percent": 0,
                               "slow_percent": 0, "min_ms": 1, "max_ms": 1}, **cfg))
    for i in range(3):
        w.add_node(Recorder("n%d" % i))
    return w


def test_events_fire_in_time_order():
    w = make_world()
    w.schedule(30, TIMER, "n0", "c")
    w.schedule(10, TIMER, "n0", "a")
    w.schedule(20, TIMER, "n0", "b")
    w.run()
    assert [name for _, name in w.nodes["n0"].timers] == ["a", "b", "c"]


def test_simultaneous_events_keep_insertion_order():
    """Ties break on a monotonic sequence number, so the run is deterministic."""
    w = make_world()
    for name in "abcd":
        w.schedule(10, TIMER, "n0", name)
    w.run()
    assert [name for _, name in w.nodes["n0"].timers] == list("abcd")


def test_cancelled_events_never_fire():
    w = make_world()
    ev = w.schedule(10, TIMER, "n0", "gone")
    w.schedule(20, TIMER, "n0", "kept")
    ev.cancel()
    w.run()
    assert [name for _, name in w.nodes["n0"].timers] == ["kept"]


def test_messages_are_delivered_after_a_delay():
    w = make_world()
    w.send("n0", "n1", "ping")
    w.run()
    assert len(w.nodes["n1"].seen) == 1
    assert w.nodes["n1"].seen[0][1] == "n0"


def test_a_partition_stops_messages_crossing_it():
    w = make_world()
    w.network.partition({"n0": 0, "n1": 1, "n2": 0})
    w.send("n0", "n1", "blocked")
    w.send("n0", "n2", "allowed")
    w.run()
    assert w.nodes["n1"].seen == []
    assert len(w.nodes["n2"].seen) == 1


def test_healing_reconnects_everything():
    w = make_world()
    w.network.partition({"n0": 0, "n1": 1, "n2": 1})
    w.network.heal()
    w.send("n0", "n1", "ping")
    w.run()
    assert len(w.nodes["n1"].seen) == 1


def test_a_one_way_block_is_asymmetric():
    """The partition shape that breaks the most implementations."""
    w = make_world()
    w.network.block_one_way("n0", "n1")
    w.send("n0", "n1", "lost")
    w.send("n1", "n0", "fine")
    w.run()
    assert w.nodes["n1"].seen == []
    assert len(w.nodes["n0"].seen) == 1


def test_messages_to_a_crashed_node_are_dropped():
    w = make_world()
    w.crash("n1")
    w.send("n0", "n1", "ping")
    w.run()
    assert w.nodes["n1"].seen == []
    assert any(r["kind"] == "drop" and r.get("why") == "crashed"
               for r in w.trace.records)


def test_a_crashed_node_does_not_send():
    w = make_world()
    w.crash("n0")
    w.send("n0", "n1", "ping")
    w.run()
    assert w.nodes["n1"].seen == []


def test_crash_and_restart_call_the_node_back():
    w = make_world()
    w.crash("n0")
    w.restart("n0")
    assert w.nodes["n0"].crashes == 1 and w.nodes["n0"].restarts == 1
    w.crash("n0")
    w.crash("n0")               # crashing twice is not two crashes
    assert w.nodes["n0"].crashes == 2


def test_exempt_nodes_ignore_partitions():
    w = make_world()
    w.network.exempt.add("client")
    w.network.partition({"n0": 0, "n1": 1, "n2": 1})
    assert w.network.reachable("client", "n1")
    assert not w.network.reachable("n0", "n1")


def test_the_run_stops_at_the_deadline():
    w = make_world()
    w.schedule(50, TIMER, "n0", "inside")
    w.schedule(500, TIMER, "n0", "outside")
    w.run(until_time=100)
    assert [name for _, name in w.nodes["n0"].timers] == ["inside"]
    assert w.stop_reason == "deadline"
    assert w.time == 100


def test_the_run_stops_at_the_step_limit():
    ch = ChoiceStream(values=[0] * 100, pad_zero=True)
    w = World(ch, max_steps=5)

    class Loop(Recorder):
        def on_timer(self, name):
            Recorder.on_timer(self, name)
            self.world.schedule(1, TIMER, self.id, name)

    w.add_node(Loop("n0"))
    w.schedule(1, TIMER, "n0", "tick")
    w.run()
    assert w.stop_reason == "max_steps"
    assert w.steps <= 6


def test_hooks_run_after_every_event():
    w = make_world()
    seen = []
    w.hooks.append(lambda world, ev: seen.append(ev.kind))
    w.schedule(1, TIMER, "n0", "a")
    w.send("n0", "n1", "m")
    w.run()
    assert seen.count(TIMER) == 1 and seen.count(DELIVER) == 1


def test_the_trace_is_bounded():
    ch = ChoiceStream(values=[0] * 100, pad_zero=True)
    w = World(ch, trace_limit=5)
    for _ in range(50):
        w.trace.add(0, "noise")
    assert len(w.trace) == 5


def test_event_ordering_is_a_total_order():
    a = Event(10, 1, TIMER, "n0", None)
    b = Event(10, 2, TIMER, "n0", None)
    c = Event(9, 99, TIMER, "n0", None)
    assert a < b and c < a
