"""Clients, and the history they produce.

A client is a sequential process: one outstanding operation at a time, which is
what makes the history checkable for linearizability. It sends to whichever node
it currently believes is the leader, follows redirects, and retries on timeout
using the *same operation id* -- so a retry is not a second operation, it is the
same one arriving again. The replicated state machine deduplicates by that id,
giving at-most-once semantics.

That detail matters more than it looks. Without it, a retried write really is a
second write, and a history that records one invocation but applies two updates
is unlinearizable for reasons that have nothing to do with consensus.

Operations that never get an answer are recorded as **indeterminate**. They may
or may not have taken effect, and the checker is required to consider both. This
is the honest thing to do and it is also the interesting thing: a checker that
quietly discards timed-out operations will miss exactly the bugs that show up
when a client cannot tell whether its write landed.
"""

from ..raft.messages import ClientRequest

INVOKE = "invoke"
OK = "ok"
INFO = "info"          # indeterminate: may or may not have happened


class HistoryEntry(object):
    __slots__ = ("kind", "process", "op", "value", "expect", "result", "time", "op_id")

    def __init__(self, kind, process, op, value, expect, result, time, op_id):
        self.kind = kind
        self.process = process
        self.op = op
        self.value = value
        self.expect = expect
        self.result = result
        self.time = time
        self.op_id = op_id

    def to_json(self):
        return {s: getattr(self, s) for s in self.__slots__}

    def __repr__(self):
        if self.kind == INVOKE:
            arg = self.value if self.op != "cas" else (self.expect, self.value)
            return "t=%-6d %s invoke %s(%s)" % (self.time, self.process, self.op, arg)
        if self.kind == OK:
            return "t=%-6d %s ok     -> %r" % (self.time, self.process, self.result)
        return "t=%-6d %s ???    (no answer)" % (self.time, self.process)


class Client(object):
    """Issues operations against the cluster and records what it observed."""

    def __init__(self, client_id, servers, config=None):
        self.id = client_id
        self.servers = list(servers)
        self.cfg = dict(DEFAULT_CLIENT_CONFIG, **(config or {}))
        self.world = None
        self.history = []
        self.leader_hint = None
        self.op_counter = 0
        self.current = None       # (op_id, op, value, expect, attempts)
        self.timeout_event = None
        self.stopped = False

    # the world treats clients like nodes; they simply never crash
    def on_crash(self):
        pass

    def on_restart(self):
        pass

    def on_timer(self, name):
        if name == "next_op":
            self.issue()
        elif name == "op_timeout":
            self.retry()

    def start(self):
        delay = self.world.choices.draw_range(0, self.cfg["start_jitter_ms"], "client")
        self.world.schedule(delay, "timer", self.id, "next_op")

    # ------------------------------------------------------------------ ops
    def issue(self):
        if self.stopped or self.current is not None:
            return
        ch = self.world.choices
        self.op_counter += 1
        op_id = "%s-%d" % (self.id, self.op_counter)
        op = ch.draw_choice(self.cfg["ops"], "op")
        value = ch.draw_range(1, self.cfg["value_range"], "val")
        expect = ch.draw_range(0, self.cfg["value_range"], "expect") if op == "cas" else None
        self.current = [op_id, op, value, expect, 0]
        self.history.append(HistoryEntry(INVOKE, self.id, op, value, expect, None,
                                         self.world.time, op_id))
        self.world.trace.add(self.world.time, "invoke", client=self.id, op=op,
                             value=value, expect=expect, op_id=op_id)
        self.send_current()

    def send_current(self):
        op_id, op, value, expect, attempts = self.current
        target = self.leader_hint
        if target is None or target not in self.servers:
            ch = self.world.choices
            target = ch.draw_choice(self.servers, "target")
        self.world.send(self.id, target,
                        ClientRequest(op_id, op, value, expect, self.id))
        if self.timeout_event is not None:
            self.timeout_event.cancel()
        self.timeout_event = self.world.schedule(
            self.cfg["timeout_ms"], "timer", self.id, "op_timeout")

    def retry(self):
        if self.current is None:
            return
        self.current[4] += 1
        if self.current[4] > self.cfg["max_attempts"]:
            op_id, op, value, expect, _ = self.current
            self.history.append(HistoryEntry(INFO, self.id, op, value, expect, None,
                                             self.world.time, op_id))
            self.world.trace.add(self.world.time, "indeterminate", client=self.id,
                                 op_id=op_id)
            self.current = None
            self.leader_hint = None
            self.schedule_next()
            return
        self.leader_hint = None          # stop trusting a hint that timed out
        self.send_current()

    def on_message(self, src, msg):
        if msg.kind != "ClientReply" or self.current is None:
            return
        if msg.op_id != self.current[0]:
            return
        if msg.ok:
            if self.timeout_event is not None:
                self.timeout_event.cancel()
                self.timeout_event = None
            op_id, op, value, expect, _ = self.current
            self.history.append(HistoryEntry(OK, self.id, op, value, expect,
                                             msg.value, self.world.time, op_id))
            self.world.trace.add(self.world.time, "ok", client=self.id,
                                 op_id=op_id, result=msg.value)
            self.leader_hint = src
            self.current = None
            self.schedule_next()
        else:
            # A redirect is not a completion: the operation was never appended,
            # so the client simply tries somewhere else with the same op id.
            if msg.leader_hint is not None and msg.leader_hint in self.servers:
                self.leader_hint = msg.leader_hint
            else:
                self.leader_hint = None
            self.send_current()

    def schedule_next(self):
        if self.stopped:
            return
        gap = self.world.choices.draw_range(1, self.cfg["gap_ms"], "gap")
        self.world.schedule(gap, "timer", self.id, "next_op")

    def stop(self):
        self.stopped = True
        if self.timeout_event is not None:
            self.timeout_event.cancel()


DEFAULT_CLIENT_CONFIG = {
    "ops": ("read", "write", "write", "cas"),
    "value_range": 5,
    "timeout_ms": 400,
    "max_attempts": 4,
    "gap_ms": 60,
    "start_jitter_ms": 40,
}
