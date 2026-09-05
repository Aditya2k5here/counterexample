"""Mutation, search and shrinking."""

import pytest

from cx.search.mutate import OPERATORS, OPERATOR_NAMES, apply_operator
from cx.search.fuzz import METHODS, search
from cx.search.shrink import shrink, make_predicate
from cx.sim.choices import Rng
from cx.sim.scenario import run


# --- mutation --------------------------------------------------------------
@pytest.mark.parametrize("index", range(len(OPERATORS)))
def test_every_operator_returns_a_valid_sequence(index):
    rng = Rng(index + 1)
    values = [rng.below(40) for _ in range(200)]
    other = [rng.below(40) for _ in range(200)]
    out = apply_operator(index, values, rng, other)
    assert isinstance(out, list)
    assert all(isinstance(v, int) and v >= 0 for v in out)


@pytest.mark.parametrize("index", range(len(OPERATORS)))
def test_operators_do_not_mutate_their_input(index):
    rng = Rng(index + 7)
    values = [rng.below(40) for _ in range(120)]
    snapshot = list(values)
    apply_operator(index, values, rng, list(values))
    assert values == snapshot


def test_operators_handle_empty_and_tiny_inputs():
    rng = Rng(3)
    for i in range(len(OPERATORS)):
        assert isinstance(apply_operator(i, [], rng, []), list)
        assert isinstance(apply_operator(i, [1], rng, [2]), list)


def test_zero_span_only_removes():
    rng = Rng(9)
    values = [5] * 100
    out = apply_operator(OPERATOR_NAMES.index("zero_span"), values, rng)
    assert len(out) == len(values)
    assert all(v in (0, 5) for v in out)
    assert any(v == 0 for v in out)


# --- search -----------------------------------------------------------------
@pytest.mark.parametrize("method", sorted(METHODS))
def test_search_finds_an_easy_bug(method):
    res = search(method, bugs=["commit_index_unclamped"], budget=60, seed=0)
    assert res.found, "%s failed to find a defect that fires on most schedules" % method
    assert res.choices


@pytest.mark.parametrize("method", sorted(METHODS))
def test_search_reports_a_reproducible_sequence(method):
    res = search(method, bugs=["no_log_up_to_date_check"], budget=120, seed=1)
    if not res.found:
        pytest.skip("not found within the test budget")
    replay = run(values=res.choices, bugs=["no_log_up_to_date_check"])
    assert replay.failed
    assert replay.signature() == res.signature


def test_search_returns_cleanly_when_nothing_is_wrong():
    res = search("random", bugs=(), budget=25, seed=0)
    assert not res.found
    assert res.trials == 25
    assert res.coverage_size > 0


def test_the_bandit_reports_which_operators_it_used():
    res = search("bandit", bugs=(), budget=40, seed=2)
    assert set(res.operator_stats).issubset(set(OPERATOR_NAMES))


# --- shrinking ---------------------------------------------------------------
def test_shrinking_preserves_the_failure_and_makes_it_smaller():
    bug = "commit_index_unclamped"
    res = search("random", bugs=[bug], budget=40, seed=4)
    assert res.found
    runner = lambda v: run(values=v, bugs=[bug], pad_zero=True)
    small, stats = shrink(res.choices, make_predicate(runner, res.signature),
                          max_attempts=400, time_budget=20.0)
    assert len(small) <= len(res.choices)
    final = run(values=small, bugs=[bug], pad_zero=True)
    assert final.failed and final.signature() == res.signature


def test_the_predicate_rejects_a_different_failure():
    """Shrinking must not drift onto another bug: reporting one property and
    reproducing another is worse than not shrinking at all."""
    runner = lambda v: run(values=v, bugs=["commit_index_unclamped"], pad_zero=True)
    pred = make_predicate(runner, "SomePropertyThatNeverFires")
    assert pred([1, 2, 3]) is False


def test_shrinking_a_sequence_that_never_failed_changes_nothing():
    runner = lambda v: run(values=v, pad_zero=True)
    pred = make_predicate(runner, "ElectionSafety")
    values = [3] * 50
    small, stats = shrink(values, pred, max_attempts=100, time_budget=5.0)
    assert small == values
    assert stats.accepted == 0
