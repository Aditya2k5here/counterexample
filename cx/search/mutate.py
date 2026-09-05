"""Mutation operators over a choice sequence.

Every operator takes a list of integers and returns a new one. None of them know
anything about Raft, partitions or clocks -- they are edits to a list of numbers,
and the meaning comes entirely from how the simulator consumes it. That is the
point of the choice-sequence representation: the search and the system under
test share no vocabulary, so the search cannot accidentally be tuned to the bugs
it is supposed to find.

``zero_span`` deserves a note. Setting a run of values to zero is the *most*
useful operator here, because zero means "nothing unusual happens" throughout the
simulator: no fault, minimum delay, no packet loss. Zeroing a span therefore
quiets a whole stretch of the run and concentrates the remaining chaos, which
turns out to be how most of the interesting schedules are found -- and it is the
same operator the shrinker leans on afterwards.
"""


def zero_span(values, rng):
    """Quieten a stretch of the run: no faults, minimum delays."""
    if not values:
        return values
    out = list(values)
    lo = rng.below(len(out))
    span = 1 + rng.below(max(1, min(len(out) - lo, 64)))
    for i in range(lo, lo + span):
        out[i] = 0
    return out


def bump(values, rng):
    """Nudge a handful of individual decisions."""
    if not values:
        return values
    out = list(values)
    for _ in range(1 + rng.below(4)):
        i = rng.below(len(out))
        out[i] = max(0, out[i] + (1 if rng.below(2) else -1) * (1 + rng.below(4)))
    return out


def randomize_span(values, rng):
    if not values:
        return values
    out = list(values)
    lo = rng.below(len(out))
    span = 1 + rng.below(max(1, min(len(out) - lo, 32)))
    for i in range(lo, lo + span):
        out[i] = rng.below(64)
    return out


def duplicate_span(values, rng):
    """Repeat a stretch of decisions elsewhere -- good at repeating a fault pattern."""
    if len(values) < 4:
        return values
    out = list(values)
    lo = rng.below(len(out))
    span = 1 + rng.below(max(1, min(len(out) - lo, 48)))
    dst = rng.below(len(out))
    chunk = out[lo:lo + span]
    for k, v in enumerate(chunk):
        if dst + k < len(out):
            out[dst + k] = v
    return out


def truncate(values, rng):
    """Cut the tail off, so the rest of the run is generated fresh.

    This is how the search explores genuinely new suffixes rather than only
    perturbing an existing one: the prefix reaches an interesting state and
    everything after it is re-rolled.
    """
    if len(values) < 8:
        return values
    keep = 1 + rng.below(len(values) - 1)
    return list(values[:keep])


def insert(values, rng):
    out = list(values)
    at = rng.below(len(out) + 1) if out else 0
    n = 1 + rng.below(16)
    chunk = [rng.below(64) for _ in range(n)]
    return out[:at] + chunk + out[at:]


def splice(values, rng, other=None):
    """Prefix from one run, suffix from another."""
    if other is None or not values or not other:
        return list(values)
    cut_a = rng.below(len(values))
    cut_b = rng.below(len(other))
    return list(values[:cut_a]) + list(other[cut_b:])


#: Ordered so the bandit's arm indices stay stable across runs.
OPERATORS = [
    ("zero_span", zero_span),
    ("bump", bump),
    ("randomize_span", randomize_span),
    ("duplicate_span", duplicate_span),
    ("truncate", truncate),
    ("insert", insert),
    ("splice", splice),
]
OPERATOR_NAMES = [name for name, _ in OPERATORS]


def apply_operator(index, values, rng, other=None):
    name, fn = OPERATORS[index]
    if name == "splice":
        return fn(values, rng, other)
    return fn(values, rng)
