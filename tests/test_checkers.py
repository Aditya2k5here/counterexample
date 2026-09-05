"""The checkers, tested against histories whose verdict is not in doubt.

A checker that never fires is indistinguishable from a correct system, so each
of these gives the checker something it *must* reject alongside something it
must accept.
"""

from cx.check import linearizability as lin
from cx.check.linearizability import Op, check
from cx.sim.client import HistoryEntry, INVOKE, OK, INFO


def op(i, process, kind, value=None, expect=None, result=None,
       t0=0, t1=None, completed=True):
    return Op(i, process, kind, value, expect, result, t0, t1, completed, "o%d" % i)


def verdict(ops):
    return check(ops)[0]


# --- sequential histories --------------------------------------------------
def test_empty_history_is_linearizable():
    assert verdict([]) == lin.LINEARIZABLE


def test_write_then_read_the_same_value():
    ops = [op(0, "c0", "write", value=7, result=7, t0=0, t1=1),
           op(1, "c0", "read", result=7, t0=2, t1=3)]
    assert verdict(ops) == lin.LINEARIZABLE


def test_read_of_a_value_nobody_wrote():
    ops = [op(0, "c0", "write", value=7, result=7, t0=0, t1=1),
           op(1, "c0", "read", result=9, t0=2, t1=3)]
    assert verdict(ops) == lin.NOT_LINEARIZABLE


def test_stale_read_after_a_completed_write():
    """The write finished before the read started, so the old value is gone."""
    ops = [op(0, "c0", "write", value=1, result=1, t0=0, t1=1),
           op(1, "c0", "write", value=2, result=2, t0=2, t1=3),
           op(2, "c1", "read", result=1, t0=4, t1=5)]
    assert verdict(ops) == lin.NOT_LINEARIZABLE


def test_cas_semantics():
    ok = [op(0, "c0", "write", value=1, result=1, t0=0, t1=1),
          op(1, "c0", "cas", expect=1, value=2, result=True, t0=2, t1=3),
          op(2, "c0", "read", result=2, t0=4, t1=5)]
    assert verdict(ok) == lin.LINEARIZABLE
    bad = [op(0, "c0", "write", value=1, result=1, t0=0, t1=1),
           op(1, "c0", "cas", expect=5, value=2, result=True, t0=2, t1=3)]
    assert verdict(bad) == lin.NOT_LINEARIZABLE


# --- concurrency -----------------------------------------------------------
def test_concurrent_writes_can_be_ordered_either_way():
    """Overlapping writes, then a read of one of them. Both orders are allowed,
    so the checker has to find the one that works."""
    ops = [op(0, "c0", "write", value=1, result=1, t0=0, t1=10),
           op(1, "c1", "write", value=2, result=2, t0=1, t1=11),
           op(2, "c2", "read", result=1, t0=12, t1=13)]
    assert verdict(ops) == lin.LINEARIZABLE


def test_a_read_may_not_be_reordered_past_its_own_return():
    """c1's read returns before c0's write is invoked; it cannot see it."""
    ops = [op(0, "c1", "read", result=5, t0=0, t1=1),
           op(1, "c0", "write", value=5, result=5, t0=2, t1=3)]
    assert verdict(ops) == lin.NOT_LINEARIZABLE


# --- indeterminate operations ----------------------------------------------
def test_an_unanswered_write_may_have_taken_effect():
    ops = [op(0, "c0", "write", value=3, t0=0, t1=None, completed=False),
           op(1, "c1", "read", result=3, t0=5, t1=6)]
    assert verdict(ops) == lin.LINEARIZABLE


def test_an_unanswered_write_may_equally_not_have_taken_effect():
    ops = [op(0, "c0", "write", value=3, t0=0, t1=None, completed=False),
           op(1, "c1", "read", result=None, t0=5, t1=6)]
    assert verdict(ops) == lin.LINEARIZABLE


def test_unanswered_operations_do_not_excuse_an_impossible_read():
    ops = [op(0, "c0", "write", value=3, t0=0, t1=None, completed=False),
           op(1, "c1", "read", result=99, t0=5, t1=6)]
    assert verdict(ops) == lin.NOT_LINEARIZABLE


def test_the_search_budget_reports_unknown_not_success():
    """Hitting the bound must never be reported as a pass."""
    ops = [op(i, "c%d" % (i % 6), "write", value=i, result=i, t0=0, t1=1000)
           for i in range(24)]
    ops.append(op(99, "c0", "read", result=1234, t0=0, t1=1000))
    assert check(ops, budget=200)[0] == lin.UNKNOWN


# --- history construction ---------------------------------------------------
def test_history_pairs_invocations_with_responses():
    entries = [
        HistoryEntry(INVOKE, "c0", "write", 1, None, None, 0, "c0-1"),
        HistoryEntry(OK, "c0", "write", 1, None, 1, 5, "c0-1"),
        HistoryEntry(INVOKE, "c1", "read", None, None, None, 6, "c1-1"),
        HistoryEntry(INFO, "c1", "read", None, None, None, 9, "c1-1"),
    ]
    ops = lin.history_to_ops(entries)
    assert len(ops) == 2
    assert ops[0].completed and ops[0].result == 1
    assert not ops[1].completed


def test_operations_left_outstanding_at_the_end_are_indeterminate():
    entries = [HistoryEntry(INVOKE, "c0", "write", 1, None, None, 0, "c0-1")]
    ops = lin.history_to_ops(entries)
    assert len(ops) == 1 and not ops[0].completed


# --- safety invariants ------------------------------------------------------
def test_every_seeded_bug_is_caught_by_at_least_one_property():
    """Not a search test: this only asserts the checkers *can* speak.

    Two of the defects need a rare interleaving and are excluded here; the
    search benchmark is where they are dealt with honestly.
    """
    from cx.sim.scenario import run
    easy = ["commit_index_unclamped", "truncate_on_every_append",
            "no_log_up_to_date_check", "ignore_prev_log_term"]
    for bug in easy:
        found = any(run(seed=s, bugs=[bug]).failed for s in range(40))
        assert found, "no schedule in 40 caught %s" % bug
