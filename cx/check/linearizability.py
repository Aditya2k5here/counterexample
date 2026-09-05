"""Is this history explainable by a single register operated on one at a time?

The Raft safety invariants are checked against internal state: logs, terms,
commit indices. This checks something a user would actually notice -- that the
sequence of client-visible results could have been produced by a single copy of
the object, with each operation taking effect instantaneously at some moment
between when it was called and when it returned.

The algorithm is Wing & Gong's, in the linked-list formulation Lowe describes,
with memoisation: walk the history, try linearising the earliest operation that
is still legal, and when a *return* is reached whose operation has not been
linearised yet, backtrack -- that operation had to take effect before it
returned. The memo is keyed on (set of linearised operations, resulting state),
which is exactly the information that determines the rest of the search, so
revisiting one is guaranteed to be a waste.

Two details that are easy to get wrong and matter here:

**Indeterminate operations.** A client that never got an answer may or may not
have had its write applied. Such operations are optional: the search may
linearise them anywhere or not at all, and their result is unconstrained. A
checker that silently drops them will happily accept histories where a write
appeared out of nowhere.

**The search can be exponential.** It is bounded, and when the bound is hit the
answer is ``UNKNOWN`` rather than "linearizable". Reporting a bound as a pass is
how a checker starts silently agreeing with everything.
"""

LINEARIZABLE = "linearizable"
NOT_LINEARIZABLE = "not-linearizable"
UNKNOWN = "unknown"


class Op(object):
    __slots__ = ("index", "process", "op", "value", "expect", "result",
                 "invoke_t", "return_t", "completed", "op_id")

    def __init__(self, index, process, op, value, expect, result,
                 invoke_t, return_t, completed, op_id):
        self.index = index
        self.process = process
        self.op = op
        self.value = value
        self.expect = expect
        self.result = result
        self.invoke_t = invoke_t
        self.return_t = return_t
        self.completed = completed
        self.op_id = op_id

    def __repr__(self):
        arg = self.value if self.op != "cas" else "%r->%r" % (self.expect, self.value)
        tail = "-> %r" % (self.result,) if self.completed else "(no answer)"
        return "%s %s(%s) %s [%s..%s]" % (
            self.process, self.op, arg, tail, self.invoke_t,
            self.return_t if self.completed else "")


def history_to_ops(entries):
    """Turn a client history (invoke / ok / info records) into operations."""
    pending = {}
    ops = []
    for e in entries:
        if e.kind == "invoke":
            pending[(e.process, e.op_id)] = e
        else:
            inv = pending.pop((e.process, e.op_id), None)
            if inv is None:
                continue
            ops.append(Op(len(ops), inv.process, inv.op, inv.value, inv.expect,
                          e.result, inv.time, e.time, e.kind == "ok", inv.op_id))
    # Operations still outstanding when the run ended are indeterminate too.
    for inv in pending.values():
        ops.append(Op(len(ops), inv.process, inv.op, inv.value, inv.expect,
                      None, inv.time, None, False, inv.op_id))
    ops.sort(key=lambda o: (o.invoke_t, o.process, o.op_id))
    for i, o in enumerate(ops):
        o.index = i
    return ops


def _apply(state, op):
    """Sequential register spec. Returns ``(legal, new_state)``."""
    if op.op == "read":
        if op.completed and op.result != state:
            return False, state
        return True, state
    if op.op == "write":
        if op.completed and op.result != op.value:
            return False, state
        return True, op.value
    if op.op == "cas":
        hit = state == op.expect
        if op.completed and op.result != hit:
            return False, state
        return True, (op.value if hit else state)
    raise AssertionError("unknown operation %r" % op.op)  # pragma: no cover


def check(ops, budget=400000):
    """Return ``(verdict, info)``."""
    # A pending read cannot change the state and its result is unconstrained, so
    # it can never be the reason a history fails. Dropping it shrinks the search.
    ops = [o for o in ops if o.completed or o.op != "read"]
    for i, o in enumerate(ops):
        o.index = i
    n = len(ops)
    if n == 0:
        return LINEARIZABLE, {"ops": 0, "steps": 0}

    # Event list: a call for every op, a return for completed ops.
    events = []
    for o in ops:
        events.append((o.invoke_t, 0, o.index, True))
        if o.completed:
            events.append((o.return_t, 1, o.index, False))
    events.sort()

    m = len(events)
    HEAD, END = -1, m
    nxt = {HEAD: 0 if m else END}
    prv = {}
    for i in range(m):
        nxt[i] = i + 1 if i + 1 < m else END
        prv[i] = i - 1 if i > 0 else HEAD
    prv[END] = m - 1 if m else HEAD

    ev_op = [e[2] for e in events]
    ev_is_call = [e[3] for e in events]
    call_of = {}
    ret_of = {}
    for i, e in enumerate(events):
        (call_of if e[3] else ret_of)[e[2]] = i

    def unlink(i):
        nxt[prv[i]] = nxt[i]
        prv[nxt[i]] = prv[i]

    def relink(i):
        nxt[prv[i]] = i
        prv[nxt[i]] = i

    state = None
    linearized = 0
    remaining = sum(1 for o in ops if o.completed)
    stack = []
    seen = set()
    steps = 0
    entry = nxt[HEAD]

    while remaining > 0:
        steps += 1
        if steps > budget:
            return UNKNOWN, {"ops": n, "steps": steps, "reason": "search budget"}

        if entry == END:
            if not stack:
                return NOT_LINEARIZABLE, _witness(ops, steps)
            entry, state, linearized, remaining = _undo(stack, relink, ops,
                                                        ev_op, ret_of)
            entry = nxt[entry]
            continue

        i = ev_op[entry]
        if ev_is_call[entry]:
            legal, new_state = _apply(state, ops[i])
            key = (linearized | (1 << i), new_state)
            if legal and key not in seen:
                seen.add(key)
                stack.append((entry, state, linearized, remaining))
                unlink(entry)
                if ops[i].completed:
                    unlink(ret_of[i])
                    remaining -= 1
                state = new_state
                linearized |= 1 << i
                entry = nxt[HEAD]
            else:
                entry = nxt[entry]
        else:
            # Reached a return whose operation is still un-linearised: illegal.
            if not stack:
                return NOT_LINEARIZABLE, _witness(ops, steps)
            entry, state, linearized, remaining = _undo(stack, relink, ops,
                                                        ev_op, ret_of)
            entry = nxt[entry]

    return LINEARIZABLE, {"ops": n, "steps": steps}


def _undo(stack, relink, ops, ev_op, ret_of):
    entry, state, linearized, remaining = stack.pop()
    i = ev_op[entry]
    if ops[i].completed:
        relink(ret_of[i])
    relink(entry)
    return entry, state, linearized, remaining


def _witness(ops, steps):
    return {
        "ops": len(ops),
        "steps": steps,
        "history": [repr(o) for o in ops[:40]],
    }


def check_history(entries, budget=400000):
    return check(history_to_ops(entries), budget)
