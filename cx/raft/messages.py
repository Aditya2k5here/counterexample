"""Raft RPCs.

Small explicit classes rather than dicts, because these objects end up in the
trace, in the space-time diagram and in shrunk counterexamples, and a
counterexample you cannot read is not much better than no counterexample.
"""


class Message(object):
    __slots__ = ()
    kind = "?"

    def fields(self):
        return {s: getattr(self, s) for s in self.__slots__}

    def to_json(self):
        d = {"kind": self.kind}
        for k, v in self.fields().items():
            d[k] = list(v) if isinstance(v, tuple) and k == "entries" else v
        if "entries" in d and d["entries"] is not None:
            d["entries"] = [{"term": e[0], "cmd": _cmd_json(e[1])} for e in d["entries"]]
        return d

    def __repr__(self):
        inner = " ".join("%s=%r" % (k, v) for k, v in self.fields().items())
        return "%s(%s)" % (self.kind, inner)


def _cmd_json(cmd):
    if cmd is None:
        return None
    return dict(cmd)


class RequestVote(Message):
    __slots__ = ("term", "candidate", "last_log_index", "last_log_term")
    kind = "RequestVote"

    def __init__(self, term, candidate, last_log_index, last_log_term):
        self.term = term
        self.candidate = candidate
        self.last_log_index = last_log_index
        self.last_log_term = last_log_term


class RequestVoteReply(Message):
    __slots__ = ("term", "granted", "voter")
    kind = "RequestVoteReply"

    def __init__(self, term, granted, voter):
        self.term = term
        self.granted = granted
        self.voter = voter


class AppendEntries(Message):
    __slots__ = ("term", "leader", "prev_log_index", "prev_log_term",
                 "entries", "leader_commit")
    kind = "AppendEntries"

    def __init__(self, term, leader, prev_log_index, prev_log_term, entries,
                 leader_commit):
        self.term = term
        self.leader = leader
        self.prev_log_index = prev_log_index
        self.prev_log_term = prev_log_term
        self.entries = entries
        self.leader_commit = leader_commit


class AppendEntriesReply(Message):
    __slots__ = ("term", "success", "follower", "match_index", "conflict_index")
    kind = "AppendEntriesReply"

    def __init__(self, term, success, follower, match_index, conflict_index=0):
        self.term = term
        self.success = success
        self.follower = follower
        self.match_index = match_index
        self.conflict_index = conflict_index


class ClientRequest(Message):
    __slots__ = ("op_id", "op", "value", "expect", "client")
    kind = "ClientRequest"

    def __init__(self, op_id, op, value, expect, client):
        self.op_id = op_id
        self.op = op            # "read" | "write" | "cas"
        self.value = value
        self.expect = expect    # for cas
        self.client = client


class ClientReply(Message):
    __slots__ = ("op_id", "ok", "value", "leader_hint")
    kind = "ClientReply"

    def __init__(self, op_id, ok, value=None, leader_hint=None):
        self.op_id = op_id
        self.ok = ok
        self.value = value
        self.leader_hint = leader_hint
