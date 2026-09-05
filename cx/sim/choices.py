"""The choice sequence: every nondeterministic decision a run makes, as a list of integers.

This is the single idea the rest of the project is built on.

A simulation run never calls a random number generator directly. Whenever it has
to make a decision -- how long this message takes, whether to drop it, when this
election timer fires, whether to crash a node right now -- it asks a
:class:`ChoiceStream` for an integer. The stream either replays a value it was
given, or invents one and *records* it.

Three things fall out of that, and they are the whole reason the project works:

**A run is a pure function of its choice sequence.** Reproducing a failure needs
no logs, no timestamps, no "it happens about once every thousand runs". It needs
a list of small integers.

**Failures can be shrunk.** A failing sequence of forty thousand integers can be
attacked mechanically -- delete a span, re-run, did it still fail? -- until what
is left is a dozen decisions a human can read. This is Hypothesis's internal
representation and it is far more powerful than shrinking a domain-specific
schedule object, because it needs to know nothing about what the integers mean.

**Shrinking has a direction.** The stream is designed so that *smaller is
simpler*: ``draw_bool`` is false at 0, fault injection is off at 0, delays are
minimal at 0. Deleting or lowering values monotonically moves a run toward
"nothing unusual happened", so a shrinker that only knows how to make numbers
smaller still produces a comprehensible story. Getting this direction wrong is
the classic way to build a shrinker that runs for an hour and returns something
no more readable than it started with.
"""

MASK64 = (1 << 64) - 1


class Rng(object):
    """A small deterministic PRNG (SplitMix64).

    Deliberately not ``random.Random``: this has to produce identical values on
    every Python version and platform, forever, or a recorded seed stops meaning
    anything.
    """

    __slots__ = ("state",)

    def __init__(self, seed):
        self.state = (seed ^ 0x9E3779B97F4A7C15) & MASK64

    def next_u64(self):
        self.state = (self.state + 0x9E3779B97F4A7C15) & MASK64
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
        return z ^ (z >> 31)

    def below(self, n):
        """Uniform in ``[0, n)``. Rejection-sampled, so no modulo bias."""
        if n <= 1:
            return 0
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            v = self.next_u64()
            if v < limit:
                return v % n


class Overrun(Exception):
    """Raised when a replay needs more decisions than the sequence provides.

    Only used by the shrinker: a candidate that overruns is simply rejected
    rather than being silently topped up with fresh randomness, because a
    top-up would make the candidate's behaviour depend on the RNG and the
    shrinker would stop converging.
    """


class ChoiceStream(object):
    """Replay-or-record source of decisions.

    ``ChoiceStream(seed)`` records: every draw invents a value and appends it to
    :attr:`values`. ``ChoiceStream(values=..., strict=True)`` replays: every draw
    consumes the next recorded value, and running off the end raises
    :class:`Overrun`.
    """

    def __init__(self, seed=0, values=None, strict=False, pad_zero=False,
                 limit=2_000_000):
        self.rng = Rng(seed)
        self.values = list(values) if values is not None else []
        self.replay_len = len(self.values)
        self.pos = 0
        self.strict = strict
        # pad_zero is what makes shrinking honest. Without it, a shortened
        # sequence has its tail filled in from the RNG, so the "minimal
        # reproduction" is really "this prefix, plus whatever the generator
        # happens to do next" -- which is not a reproduction at all. Padding
        # with zeros instead means a truncated sequence has an exact meaning:
        # and then nothing unusual happened for the rest of the run.
        self.pad_zero = pad_zero
        self.limit = limit
        self.labels = []

    # ------------------------------------------------------------------ draws
    def draw(self, n, label=""):
        """An integer in ``[0, n)``. Semantically, 0 should mean "nothing unusual"."""
        if n <= 1:
            return 0
        if self.pos < self.replay_len:
            v = self.values[self.pos] % n
        elif self.pad_zero:
            v = 0
        else:
            if self.strict:
                raise Overrun("choice sequence exhausted after %d draws" % self.pos)
            if self.pos >= self.limit:
                raise Overrun("choice sequence exceeded %d draws" % self.limit)
            v = self.rng.below(n)
            self.values.append(v)
        self.pos += 1
        self.labels.append(label)
        return v

    def draw_bool(self, percent, label=""):
        """True with roughly ``percent`` probability -- and *false* at value 0.

        The comparison is deliberately ``>= 100 - percent`` rather than the
        obvious ``< percent``. Both give the same probability; only this one
        puts the "yes" outcomes at the *top* of the range, so that value 0 means
        no and shrinking toward zero turns unusual events off. Written the
        obvious way, zero means "drop this message" and "make this link slow",
        which quietly inverts the entire shrinking story: minimising a failure
        would drive the network toward losing everything instead of toward
        calm. ``tests/test_determinism.py`` pins this down, and it caught the
        bug.
        """
        return self.draw(100, label) >= 100 - percent

    def draw_range(self, lo, hi, label=""):
        """Inclusive range, minimal at value 0."""
        return lo + self.draw(hi - lo + 1, label)

    def draw_choice(self, options, label=""):
        return options[self.draw(len(options), label)]

    # ----------------------------------------------------------------- state
    def consumed(self):
        return self.values[: self.pos]

    def __len__(self):
        return len(self.values)

    def __repr__(self):
        return "<ChoiceStream %d drawn / %d recorded>" % (self.pos, len(self.values))
