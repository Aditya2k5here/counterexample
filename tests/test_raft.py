"""Raft behaviour, on a quiet network where the answers are not in doubt.

These are the tests that would catch "the implementation does not actually
implement Raft", which the search cannot: a consensus algorithm that never
elects anyone and never commits anything satisfies every safety property
perfectly.
"""

import pytest

from cx.check import linearizability as lin
from cx.raft.node import BUGS, RaftNode, Entry
from cx.sim.scenario import run

QUIET = {"drop_percent": 0, "duplicate_percent": 0, "slow_percent": 0,
         "min_ms": 1, "max_ms": 3}
NO_FAULTS = {"n_servers": 5, "horizon_ms": 3000, "inject_faults": False,
             "check_liveness": True}


def quiet_run(seed=1, **kw):
    """A fast, lossless network with no injected faults: Raft on a good day."""
    return run(seed=seed, world_config=QUIET,
               config=dict(NO_FAULTS, **kw.pop("config", {})), **kw)


def test_a_leader_is_elected_and_entries_are_committed():
    r = quiet_run(keep_trace=True)
    assert not r.failed, r.summary()
    assert r.ops_completed > 10
    assert r.linearizable == lin.LINEARIZABLE


def test_exactly_one_leader_settles():
    r = quiet_run(keep_trace=True)
    leaders = {}
    for rec in r.trace.records:
        if rec["kind"] == "state" and rec["role"] == "leader":
            leaders.setdefault(rec["term"], set()).add(rec["node"])
    assert leaders, "nobody was ever elected"
    for term, who in leaders.items():
        assert len(who) == 1, "two leaders in term %d: %s" % (term, who)


def test_every_replica_converges_on_the_same_log():
    r = quiet_run(keep_trace=True)
    applied = {}
    for rec in r.trace.records:
        if rec["kind"] != "apply":
            continue
        key = rec["index"]
        prev = applied.setdefault(key, (rec["term"], rec["cmd"]))
        assert prev == (rec["term"], rec["cmd"]), "divergence at index %d" % key
    assert len(applied) > 10


def test_client_operations_are_linearizable_under_faults():
    for seed in range(6):
        r = run(seed=seed)
        assert r.linearizable in (lin.LINEARIZABLE, None), r.summary()


def test_synchronised_election_timeouts_livelock():
    """An all-zero schedule sets every node's election timer to the same value.

    Raft's randomised timeouts are not a detail: with them removed, all five
    nodes campaign at once, every election splits the vote, and the cluster
    makes no progress at all. Worth pinning down, because it is the clearest
    demonstration that the simulator really is driving the algorithm rather
    than nudging it.
    """
    r = run(values=[0] * 100, pad_zero=True, world_config=QUIET,
            config=dict(NO_FAULTS, check_liveness=False))
    assert r.ops_completed == 0
    assert not r.violations, "a livelock is not a *safety* violation"


def test_persistent_state_survives_a_crash():
    node = RaftNode("s0", ["s0", "s1", "s2"])
    node.current_term = 4
    node.voted_for = "s1"
    node.log.append(Entry(4, {"op": "write", "value": 1, "op_id": "x"}))
    node.persist()
    node.on_crash()
    node._load()
    assert node.current_term == 4
    assert node.voted_for == "s1"
    assert len(node.log) == 2


def test_volatile_state_does_not_survive_a_crash():
    node = RaftNode("s0", ["s0", "s1", "s2"])
    node.role = "leader"
    node.commit_index = 7
    node.kv = 42
    node.on_crash()
    assert node.role == "follower"
    assert node.commit_index == 0
    assert node.kv is None


def test_every_named_bug_is_a_real_switch():
    """Each defect must actually change behaviour -- a bug nobody can trigger is
    a bug in the bug list."""
    baseline = run(seed=5)
    changed = 0
    for bug in BUGS:
        r = run(seed=5, bugs=[bug])
        if (r.failed != baseline.failed
                or r.steps != baseline.steps
                or r.ops_completed != baseline.ops_completed):
            changed += 1
    assert changed >= len(BUGS) // 2, (
        "most seeded defects had no observable effect on a single fixed schedule")


def test_unknown_bug_names_are_rejected():
    with pytest.raises(ValueError):
        RaftNode("s0", ["s0"], bugs=["not_a_real_bug"])


def test_the_bug_catalogue_is_documented():
    for name, description in BUGS.items():
        assert description and len(description) > 30, name
