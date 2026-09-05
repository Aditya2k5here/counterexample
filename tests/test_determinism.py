"""The property everything else rests on.

If a run is not a pure function of its choice sequence then a "reproduction" is
a suggestion, shrinking is meaningless, and every number in the results table is
an anecdote. These tests are short and they are the most important ones here.
"""

import pytest

from cx.sim.choices import ChoiceStream, Rng, Overrun
from cx.sim.scenario import run


def test_rng_is_stable_across_processes():
    # SplitMix64, not random.Random: a recorded seed has to mean the same thing
    # on every Python version, forever.
    r = Rng(7)
    assert [r.next_u64() for _ in range(3)] == [
        17039259473404265729, 18363971414914884509, 8043341295829897994,
    ]


def test_rng_below_is_in_range():
    r = Rng(1)
    assert all(0 <= r.below(7) < 7 for _ in range(500))
    assert Rng(1).below(1) == 0


def test_recording_then_replaying_gives_the_same_draws():
    rec = ChoiceStream(seed=5)
    drawn = [rec.draw(10) for _ in range(50)]
    replay = ChoiceStream(values=rec.values)
    assert [replay.draw(10) for _ in range(50)] == drawn


def test_zero_means_nothing_unusual():
    ch = ChoiceStream(values=[0] * 10)
    assert ch.draw_bool(50) is False
    assert ch.draw_range(4, 900) == 4
    assert ch.draw_choice(("none", "crash", "partition")) == "none"


def test_pad_zero_makes_a_short_sequence_total():
    ch = ChoiceStream(values=[3], pad_zero=True)
    assert ch.draw(10) == 3
    assert [ch.draw(10) for _ in range(5)] == [0, 0, 0, 0, 0]


def test_strict_replay_refuses_to_invent_values():
    ch = ChoiceStream(values=[1, 2], strict=True)
    ch.draw(10)
    ch.draw(10)
    with pytest.raises(Overrun):
        ch.draw(10)


# --- the simulator ---------------------------------------------------------
def test_same_seed_gives_an_identical_run():
    a = run(seed=11)
    b = run(seed=11)
    assert a.choices == b.choices
    assert (a.steps, a.time, a.ops_completed) == (b.steps, b.time, b.ops_completed)
    assert a.summary() == b.summary()
    assert a.coverage == b.coverage


def test_different_seeds_give_different_runs():
    a, b = run(seed=1), run(seed=2)
    assert a.choices != b.choices


def test_replaying_a_recorded_sequence_reproduces_the_run():
    original = run(seed=23)
    replay = run(values=original.choices)
    assert replay.choices == original.choices
    assert replay.steps == original.steps
    assert replay.summary() == original.summary()
    assert [str(e) for e in replay.history] == [str(e) for e in original.history]


def test_replay_is_independent_of_the_seed():
    """A recorded sequence must not need the seed that produced it."""
    original = run(seed=31)
    assert run(values=original.choices, seed=999).steps == original.steps


def test_pad_zero_run_is_fully_determined_by_its_prefix():
    a = run(values=[4, 1, 9, 2], pad_zero=True, seed=0)
    b = run(values=[4, 1, 9, 2], pad_zero=True, seed=12345)
    assert a.steps == b.steps and a.summary() == b.summary()


def test_a_bug_does_not_change_the_schedule_it_is_given():
    """The defects change what the nodes *do*, not what the world does to them."""
    clean = run(seed=17)
    buggy = run(seed=17, bugs=["commit_any_term"])
    # The first few decisions -- cluster size, timers, the first faults -- are
    # drawn before the defect can possibly matter.
    assert clean.choices[:20] == buggy.choices[:20]
    assert clean.n_servers == buggy.n_servers
