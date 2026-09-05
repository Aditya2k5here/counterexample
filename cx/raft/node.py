"""Raft, as described in the paper, on top of the simulated world.

This is one implementation with named defects rather than a family of separate
implementations. Every deliberate bug is a guard of the form::

    if "no_log_up_to_date_check" not in self.bugs:
        ...the correct check...

which means the correct and broken variants share every other line. When the
search reports that the buggy variant fails and the correct one does not, that
difference is attributable to the named defect and nothing else -- the same
discipline as swapping a single predicate in a collector.

Implemented: leader election with randomised timeouts, log replication with the
consistency check and conflict backtracking, persistence across crashes,
commitment by majority restricted to the current term, and a replicated register
state machine with at-most-once client semantics.

Not implemented: snapshots, log compaction, cluster membership changes,
pre-vote, leadership transfer. Those are called out in the README rather than
half-built.
"""

from .messages import (
    RequestVote, RequestVoteReply, AppendEntries, AppendEntriesReply, ClientReply,
)

FOLLOWER = "follower"
CANDIDATE = "candidate"
LEADER = "leader"

#: Every deliberate defect, with the real-world mistake it corresponds to.
BUGS = {
    "no_log_up_to_date_check":
        "grant a vote without checking the candidate's log is at least as "
        "up to date (violates Leader Completeness)",
    "commit_any_term":
        "a leader commits an entry as soon as a majority stores it, even if the "
        "entry is from an earlier term (the Figure 8 bug)",
    "no_persist_voted_for":
        "votedFor is not persisted, so a node can vote twice in one term after "
        "a crash (two leaders in the same term)",
    "ignore_prev_log_term":
        "the AppendEntries consistency check compares only the index, not the "
        "term (silent log divergence)",
    "commit_index_unclamped":
        "a follower sets commitIndex to leaderCommit without clamping it to the "
        "last entry it actually has",
    "truncate_on_every_append":
        "a follower truncates its log at prevLogIndex on every AppendEntries, "
        "even when the entries already match (drops committed entries)",
    "accept_stale_term":
        "messages from an older term are processed instead of rejected",
    "no_step_down_on_higher_term":
        "a leader seeing a higher term keeps leading",
}


class Entry(object):
    __slots__ = ("term", "cmd")

    def __init__(self, term, cmd):
        self.term = term
        self.cmd = cmd

    def __eq__(self, other):
        return self.term == other.term and self.cmd == other.cmd

    def __repr__(self):
        return "(%d,%r)" % (self.term, self.cmd)


class RaftNode(object):
    def __init__(self, node_id, peers, bugs=(), config=None):
        self.id = node_id
        self.peers = [p for p in peers if p != node_id]
        self.all_nodes = list(peers)
        self.bugs = frozenset(bugs)
        for b in self.bugs:
            if b not in BUGS:
                raise ValueError("unknown bug %r" % b)
        self.cfg = dict(DEFAULT_RAFT_CONFIG, **(config or {}))
        self.world = None

        # ---- persistent state (survives a crash) --------------------------
        self.stable = {
            "current_term": 0,
            "voted_for": None,
            "log": [Entry(0, None)],   # index 0 is a sentinel
        }
        self._load()

        # ---- volatile state ------------------------------------------------
        self._reset_volatile()

    # ------------------------------------------------------------- lifecycle
    def _reset_volatile(self):
        self.role = FOLLOWER
        self.leader_id = None
        self.commit_index = 0
        self.last_applied = 0
        self.votes = set()
        self.next_index = {}
        self.match_index = {}
        self.election_timer = None
        self.heartbeat_timer = None
        self.pending = {}          # log index -> (client, op_id)
        self.kv = None             # replicated register
        self.applied_ops = {}      # op_id -> response value (at-most-once)

    def _load(self):
        self.current_term = self.stable["current_term"]
        self.voted_for = self.stable["voted_for"]
        self.log = list(self.stable["log"])

    def persist(self, voted_for=True):
        self.stable["current_term"] = self.current_term
        self.stable["log"] = list(self.log)
        if voted_for:
            self.stable["voted_for"] = self.voted_for

    def start(self):
        self.reset_election_timer()

    def on_crash(self):
        # Volatile state is lost. Whatever was persisted survives -- and the
        # applied state machine does *not*, because it is rebuilt by replaying
        # the log, which is the whole point of replicating the log.
        self._reset_volatile()

    def on_restart(self):
        self._load()
        self._reset_volatile()
        self.reset_election_timer()

    # ---------------------------------------------------------------- timers
    def reset_election_timer(self):
        if self.election_timer is not None:
            self.election_timer.cancel()
        ch = self.world.choices
        timeout = ch.draw_range(self.cfg["election_min_ms"],
                                self.cfg["election_max_ms"], "election")
        self.election_timer = self.world.schedule(timeout, "timer", self.id, "election")

    def reset_heartbeat_timer(self):
        if self.heartbeat_timer is not None:
            self.heartbeat_timer.cancel()
        self.heartbeat_timer = self.world.schedule(
            self.cfg["heartbeat_ms"], "timer", self.id, "heartbeat"
        )

    def on_timer(self, name):
        if name == "election":
            if self.role != LEADER:
                self.begin_election()
            else:
                self.reset_election_timer()
        elif name == "heartbeat":
            if self.role == LEADER:
                self.broadcast_append_entries()
                self.reset_heartbeat_timer()

    # ------------------------------------------------------------------ log
    def last_log_index(self):
        return len(self.log) - 1

    def last_log_term(self):
        return self.log[-1].term

    def log_term_at(self, index):
        if 0 <= index < len(self.log):
            return self.log[index].term
        return -1

    # ------------------------------------------------------------- elections
    def begin_election(self):
        self.role = CANDIDATE
        self.current_term += 1
        self.voted_for = self.id
        self.votes = {self.id}
        self.leader_id = None
        self.persist()
        self.world.trace.add(self.world.time, "state", node=self.id, role=CANDIDATE,
                             term=self.current_term)
        self.reset_election_timer()
        req = RequestVote(self.current_term, self.id,
                          self.last_log_index(), self.last_log_term())
        for p in self.peers:
            self.world.send(self.id, p, req)
        self.maybe_become_leader()

    def maybe_become_leader(self):
        if self.role != CANDIDATE:
            return
        if len(self.votes) * 2 > len(self.all_nodes):
            self.become_leader()

    def become_leader(self):
        self.role = LEADER
        self.leader_id = self.id
        nxt = self.last_log_index() + 1
        self.next_index = {p: nxt for p in self.peers}
        self.match_index = {p: 0 for p in self.peers}
        self.world.trace.add(self.world.time, "state", node=self.id, role=LEADER,
                             term=self.current_term)
        self.broadcast_append_entries()
        self.reset_heartbeat_timer()
        self.reset_election_timer()

    def step_down(self, term):
        changed = term > self.current_term
        if changed:
            self.current_term = term
            self.voted_for = None
            self.persist()
        if self.role != FOLLOWER:
            self.world.trace.add(self.world.time, "state", node=self.id,
                                 role=FOLLOWER, term=self.current_term)
            self.fail_pending()
        self.role = FOLLOWER
        self.leader_id = None
        if self.heartbeat_timer is not None:
            self.heartbeat_timer.cancel()
            self.heartbeat_timer = None

    # ------------------------------------------------------------ replication
    def broadcast_append_entries(self):
        for p in self.peers:
            self.send_append_entries(p)

    def send_append_entries(self, peer):
        nxt = self.next_index.get(peer, self.last_log_index() + 1)
        nxt = max(1, min(nxt, self.last_log_index() + 1))
        prev = nxt - 1
        entries = tuple(self.log[nxt:nxt + self.cfg["max_batch"]])
        msg = AppendEntries(self.current_term, self.id, prev,
                            self.log_term_at(prev), entries, self.commit_index)
        self.world.send(self.id, peer, msg)

    # -------------------------------------------------------------- dispatch
    def on_message(self, src, msg):
        kind = msg.kind

        # Every RPC carries a term. Seeing a higher one means this node is stale.
        term = getattr(msg, "term", None)
        if term is not None:
            if term > self.current_term:
                if "no_step_down_on_higher_term" in self.bugs and self.role == LEADER:
                    pass
                else:
                    self.step_down(term)
            elif term < self.current_term and "accept_stale_term" not in self.bugs:
                self._reject_stale(src, msg)
                return

        handler = getattr(self, "_on_" + kind, None)
        if handler is not None:
            handler(src, msg)

    def _reject_stale(self, src, msg):
        if msg.kind == "RequestVote":
            self.world.send(self.id, src,
                            RequestVoteReply(self.current_term, False, self.id))
        elif msg.kind == "AppendEntries":
            self.world.send(self.id, src,
                            AppendEntriesReply(self.current_term, False, self.id, 0))

    # --- voting -----------------------------------------------------------
    def _on_RequestVote(self, src, msg):
        granted = False
        if msg.term >= self.current_term and self.voted_for in (None, msg.candidate):
            if "no_log_up_to_date_check" in self.bugs:
                up_to_date = True
            else:
                up_to_date = (
                    msg.last_log_term > self.last_log_term()
                    or (msg.last_log_term == self.last_log_term()
                        and msg.last_log_index >= self.last_log_index())
                )
            if up_to_date:
                granted = True
                self.voted_for = msg.candidate
                # Not persisting votedFor is a real and popular bug: after a
                # crash the node forgets it voted and can vote again in the
                # same term, electing two leaders.
                self.persist(voted_for="no_persist_voted_for" not in self.bugs)
                self.reset_election_timer()
        self.world.send(self.id, src,
                        RequestVoteReply(self.current_term, granted, self.id))

    def _on_RequestVoteReply(self, src, msg):
        if self.role != CANDIDATE or msg.term != self.current_term:
            return
        if msg.granted:
            self.votes.add(msg.voter)
            self.maybe_become_leader()

    # --- log replication ---------------------------------------------------
    def _on_AppendEntries(self, src, msg):
        if msg.term >= self.current_term:
            self.role = FOLLOWER
            self.leader_id = msg.leader
            self.reset_election_timer()

        ok = True
        if msg.prev_log_index > self.last_log_index():
            ok = False
        elif "ignore_prev_log_term" not in self.bugs:
            if self.log_term_at(msg.prev_log_index) != msg.prev_log_term:
                ok = False

        if not ok:
            conflict = min(msg.prev_log_index, self.last_log_index() + 1)
            self.world.send(self.id, src,
                            AppendEntriesReply(self.current_term, False, self.id, 0,
                                               max(1, conflict)))
            return

        if "truncate_on_every_append" in self.bugs:
            # Truncating unconditionally throws away entries the follower
            # already had -- including committed ones, if this AppendEntries is
            # a delayed duplicate from an old leader.
            del self.log[msg.prev_log_index + 1:]
            self.log.extend(msg.entries)
        else:
            # Only truncate at the first genuine conflict, as the paper says.
            index = msg.prev_log_index + 1
            for i, entry in enumerate(msg.entries):
                pos = index + i
                if pos < len(self.log):
                    if self.log[pos].term != entry.term:
                        del self.log[pos:]
                        self.log.append(entry)
                else:
                    self.log.append(entry)

        last_new = msg.prev_log_index + len(msg.entries)
        if msg.leader_commit > self.commit_index:
            if "commit_index_unclamped" in self.bugs:
                self.commit_index = msg.leader_commit
            else:
                self.commit_index = min(msg.leader_commit, last_new)
        self.persist()
        self.apply_committed()
        self.world.send(self.id, src,
                        AppendEntriesReply(self.current_term, True, self.id, last_new))

    def _on_AppendEntriesReply(self, src, msg):
        if self.role != LEADER or msg.term != self.current_term:
            return
        if msg.success:
            self.match_index[msg.follower] = max(
                self.match_index.get(msg.follower, 0), msg.match_index)
            self.next_index[msg.follower] = self.match_index[msg.follower] + 1
            self.advance_commit_index()
            if self.next_index[msg.follower] <= self.last_log_index():
                self.send_append_entries(msg.follower)
        else:
            nxt = self.next_index.get(msg.follower, self.last_log_index() + 1)
            self.next_index[msg.follower] = max(1, min(nxt - 1, msg.conflict_index))
            self.send_append_entries(msg.follower)

    def advance_commit_index(self):
        for n in range(self.last_log_index(), self.commit_index, -1):
            replicas = 1 + sum(1 for p in self.peers if self.match_index.get(p, 0) >= n)
            if replicas * 2 <= len(self.all_nodes):
                continue
            # Raft only lets a leader commit by counting replicas for entries
            # from its *own* term. Dropping that check is the Figure 8 bug: an
            # entry replicated on a majority can still be overwritten later.
            if "commit_any_term" not in self.bugs and self.log[n].term != self.current_term:
                continue
            self.commit_index = n
            self.apply_committed()
            return

    # ---------------------------------------------------------- state machine
    def apply_committed(self):
        # The bound on last_log_index is not redundant: a follower with a
        # corrupted commit index can believe it has committed entries it has
        # never received. Crashing there would hide the bug behind a stack
        # trace; stopping lets the CommitIndexInBounds invariant report it.
        while self.last_applied < min(self.commit_index, self.last_log_index()):
            self.last_applied += 1
            entry = self.log[self.last_applied]
            result = self.apply_command(entry.cmd)
            self.world.trace.add(self.world.time, "apply", node=self.id,
                                 index=self.last_applied, term=entry.term,
                                 cmd=entry.cmd, result=result)
            waiter = self.pending.pop(self.last_applied, None)
            if waiter is not None and self.role == LEADER:
                client, op_id = waiter
                if entry.cmd is not None and entry.cmd.get("op_id") == op_id:
                    self.world.send(self.id, client,
                                    ClientReply(op_id, True, result))
                else:
                    self.world.send(self.id, client, ClientReply(op_id, False))

    def apply_command(self, cmd):
        if cmd is None:
            return None
        op_id = cmd.get("op_id")
        if op_id in self.applied_ops:          # at-most-once: replayed request
            return self.applied_ops[op_id]
        op = cmd["op"]
        if op == "read":
            result = self.kv
        elif op == "write":
            self.kv = cmd["value"]
            result = cmd["value"]
        elif op == "cas":
            if self.kv == cmd["expect"]:
                self.kv = cmd["value"]
                result = True
            else:
                result = False
        else:                                   # pragma: no cover - defensive
            raise AssertionError("unknown op %r" % op)
        self.applied_ops[op_id] = result
        return result

    # -------------------------------------------------------------- clients
    def _on_ClientRequest(self, src, msg):
        if self.role != LEADER:
            self.world.send(self.id, src,
                            ClientReply(msg.op_id, False, leader_hint=self.leader_id))
            return
        cmd = {"op": msg.op, "value": msg.value, "expect": msg.expect,
               "op_id": msg.op_id}
        self.log.append(Entry(self.current_term, cmd))
        self.persist()
        self.pending[self.last_log_index()] = (src, msg.op_id)
        self.match_index[self.id] = self.last_log_index()
        self.world.trace.add(self.world.time, "propose", node=self.id,
                             index=self.last_log_index(), op_id=msg.op_id, op=msg.op)
        self.broadcast_append_entries()
        self.advance_commit_index()

    def fail_pending(self):
        for index, (client, op_id) in list(self.pending.items()):
            self.world.send(self.id, client, ClientReply(op_id, False))
        self.pending.clear()

    def snapshot(self):
        return {
            "id": self.id, "role": self.role, "term": self.current_term,
            "voted_for": self.voted_for, "commit": self.commit_index,
            "log": [(e.term, e.cmd) for e in self.log],
            "kv": self.kv,
        }


DEFAULT_RAFT_CONFIG = {
    # Deliberately fast elections. Real deployments use hundreds of
    # milliseconds; here the only thing a long timeout buys is fewer elections
    # per simulated second, and elections are where the interesting bugs are.
    "election_min_ms": 50,
    "election_max_ms": 130,
    "heartbeat_ms": 25,
    "max_batch": 8,
}
