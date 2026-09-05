"""Raft's safety properties, checked continuously against the whole cluster.

A checker is allowed to do what no node can: look at every replica's state at
once. These are the five properties the Raft paper proves, restated as things
that can be observed:

``ElectionSafety``      at most one leader is ever elected in a given term
``LeaderAppendOnly``    a leader never overwrites or deletes entries in its log
``LogMatching``         if two logs hold the same term at the same index, every
                        entry before that index is identical
``StateMachineSafety``  no two replicas ever apply different commands at the
                        same log index
``LeaderCompleteness``  a newly elected leader's log contains every entry that
                        was already committed
``CommitIndexInBounds``  no replica claims to have committed an entry it has
                        never received

Checking cost is deliberately uneven. Applied-entry consistency is O(1) per
event and runs on every single step, because it is the property most likely to
catch a real divergence. The pairwise log comparison is O(nodes squared times log
length) and runs on a stride, plus unconditionally at the end of the run -- a
violation of log matching is permanent, so a stride cannot hide one, it can only
delay reporting it by a few events.
"""


class Violation(object):
    __slots__ = ("prop", "time", "detail", "nodes")

    def __init__(self, prop, time, detail, nodes=()):
        self.prop = prop
        self.time = time
        self.detail = detail
        self.nodes = tuple(nodes)

    def to_json(self):
        return {"property": self.prop, "t": self.time, "detail": self.detail,
                "nodes": list(self.nodes)}

    def __repr__(self):
        return "%s at t=%d: %s" % (self.prop, self.time, self.detail)


class SafetyMonitor(object):
    def __init__(self, world, servers, check_every=25, stop_on_first=True):
        self.world = world
        self.servers = list(servers)
        self.check_every = check_every
        self.stop_on_first = stop_on_first
        self.violations = []
        self.n_checks = 0

        self.leader_of_term = {}      # term -> node id
        self.leader_log_len = {}      # node -> log length while leader
        self.applied = {}             # index -> (term, cmd, node)
        # index -> (term, cmd, term_it_was_committed_in). The third element is
        # the part that is easy to leave out and wrong to leave out: Leader
        # Completeness constrains leaders of *later* terms only. A stale leader
        # stranded on the wrong side of a partition is still a leader, in an
        # older term, and it is entitled to have a different log.
        self.committed_entries = {}
        self._last_role = {}
        self._last_commit = {}

    # ------------------------------------------------------------------ hook
    def on_event(self, world, ev):
        self.n_checks += 1
        self._check_roles()
        self._check_applied()
        if self.n_checks % self.check_every == 0:
            self._check_logs()
        return not self.violations or not self.stop_on_first

    def finish(self):
        self._check_logs()
        return self.violations

    def fail(self, prop, detail, nodes=()):
        self.violations.append(Violation(prop, self.world.time, detail, nodes))

    # --------------------------------------------------------------- checks
    def _check_roles(self):
        for nid in self.servers:
            node = self.world.nodes[nid]

            if self._last_commit.get(nid) != node.commit_index:
                self._last_commit[nid] = node.commit_index
                if node.commit_index > node.last_log_index():
                    self.fail("CommitIndexInBounds",
                              "%s has commitIndex %d but only %d log entries"
                              % (nid, node.commit_index, node.last_log_index()), [nid])

            # The rest fires only on the *transition* into leadership. Re-running
            # it on every state change would keep re-examining a stale leader
            # against entries committed later, by someone else, in a higher term.
            key = (node.role, node.current_term)
            if self._last_role.get(nid) == key:
                continue
            self._last_role[nid] = key
            if node.role != "leader":
                continue

            # ElectionSafety
            prev = self.leader_of_term.get(node.current_term)
            if prev is not None and prev != nid:
                self.fail("ElectionSafety",
                          "nodes %s and %s were both elected leader in term %d"
                          % (prev, nid, node.current_term), [prev, nid])
            self.leader_of_term[node.current_term] = nid

            # LeaderCompleteness: every entry committed in an *earlier* term
            # must be present in this new leader's log.
            for index, (term, cmd, cterm) in self.committed_entries.items():
                if cterm >= node.current_term:
                    continue
                if index >= len(node.log):
                    self.fail("LeaderCompleteness",
                              "%s became leader in term %d without entry %d (%r), "
                              "which was committed in term %d"
                              % (nid, node.current_term, index, cmd, cterm), [nid])
                    break
                have = node.log[index]
                if have.term != term or have.cmd != cmd:
                    self.fail("LeaderCompleteness",
                              "%s became leader in term %d holding %r at index %d, "
                              "but %r was committed there in term %d"
                              % (nid, node.current_term, have.cmd, index, cmd, cterm),
                              [nid])
                    break

    def _check_applied(self):
        # Only look at apply records produced since the last call.
        records = self.world.trace.records
        start = getattr(self, "_apply_cursor", 0)
        for i in range(start, len(records)):
            r = records[i]
            if r["kind"] != "apply":
                continue
            index, term, cmd, node = r["index"], r["term"], r["cmd"], r["node"]
            prev = self.applied.get(index)
            if prev is None:
                self.applied[index] = (term, cmd, node)
                committed_in = self.world.nodes[node].current_term
                self.committed_entries[index] = (term, cmd, committed_in)
            elif prev[0] != term or prev[1] != cmd:
                self.fail("StateMachineSafety",
                          "index %d applied as %r (term %d) by %s and as %r "
                          "(term %d) by %s"
                          % (index, prev[1], prev[0], prev[2], cmd, term, node),
                          [prev[2], node])
        self._apply_cursor = len(records)

    def _check_logs(self):
        nodes = [self.world.nodes[n] for n in self.servers]

        # LeaderAppendOnly
        for node in nodes:
            if node.role == "leader":
                prev_len = self.leader_log_len.get(node.id)
                if prev_len is not None and len(node.log) < prev_len:
                    self.fail("LeaderAppendOnly",
                              "%s truncated its log while leader (%d -> %d entries)"
                              % (node.id, prev_len, len(node.log)), [node.id])
                self.leader_log_len[node.id] = len(node.log)
            else:
                self.leader_log_len.pop(node.id, None)

        # LogMatching
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = nodes[i], nodes[j]
                upto = min(len(a.log), len(b.log)) - 1
                for index in range(upto, 0, -1):
                    if a.log[index].term != b.log[index].term:
                        continue
                    for k in range(index, -1, -1):
                        if a.log[k].term != b.log[k].term or a.log[k].cmd != b.log[k].cmd:
                            self.fail("LogMatching",
                                      "%s and %s agree at index %d (term %d) but "
                                      "differ at index %d"
                                      % (a.id, b.id, index, a.log[index].term, k),
                                      [a.id, b.id])
                            return
                    break
