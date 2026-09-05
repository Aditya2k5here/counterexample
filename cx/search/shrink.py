"""Turn a failing run into one a human can read.

The search hands back a choice sequence of ten or twenty thousand integers that
produced a violation. That is a perfect reproduction and a useless explanation.
Shrinking attacks it mechanically: delete a span, re-run, did it still fail the
same way? Zero a span. Lower a value. Cut the tail. Repeat until nothing helps.

The passes are ordered by how much they remove, largest first, because a
successful large deletion saves a hundred small ones. Within a pass, spans are
tried from the end backwards, which keeps indices stable for the rest of the
pass after a successful deletion.

**Why this works without knowing anything about Raft.** The simulator is written
so that zero means "nothing unusual": no fault, minimum network delay, no packet
loss, no duplicate. So "make the numbers smaller and fewer" is the same thing as
"make the run calmer and shorter", and a shrinker that understands only integers
produces a counterexample stated in terms of the two crashes and one partition
that actually mattered.

**What is preserved.** A candidate is accepted only if it reproduces the *same
property violation*. Shrinking toward a different bug would be worse than
useless: it would report one failure and reproduce another.
"""

import time

SPANS = (128, 64, 32, 16, 8, 4, 2, 1)


class ShrinkStats(object):
    def __init__(self):
        self.attempts = 0
        self.accepted = 0
        self.start_len = 0
        self.end_len = 0
        self.seconds = 0.0
        self.passes = 0

    def to_json(self):
        return {
            "attempts": self.attempts, "accepted": self.accepted,
            "from": self.start_len, "to": self.end_len,
            "reduction": (1.0 - self.end_len / self.start_len) if self.start_len else 0.0,
            "seconds": round(self.seconds, 2), "passes": self.passes,
        }


def shrink(values, reproduces, max_attempts=4000, time_budget=60.0):
    """Minimise ``values`` while ``reproduces(values)`` stays true."""
    stats = ShrinkStats()
    stats.start_len = len(values)
    best = list(values)
    started = time.time()

    def try_candidate(candidate):
        if stats.attempts >= max_attempts or time.time() - started > time_budget:
            return None
        stats.attempts += 1
        if len(candidate) >= len(best):
            return None
        if reproduces(candidate):
            stats.accepted += 1
            return candidate
        return None

    def try_same_length(candidate):
        if stats.attempts >= max_attempts or time.time() - started > time_budget:
            return None
        stats.attempts += 1
        if reproduces(candidate):
            stats.accepted += 1
            return candidate
        return None

    progress = True
    while progress:
        progress = False
        stats.passes += 1
        if stats.attempts >= max_attempts or time.time() - started > time_budget:
            break

        # 1. delete spans, biggest first
        for span in SPANS:
            i = len(best) - span
            while i >= 0:
                candidate = best[:i] + best[i + span:]
                got = try_candidate(candidate)
                if got is not None:
                    best = got
                    progress = True
                    i = min(i, len(best) - span)
                i -= span

        # 2. zero spans: quiets faults and delays without changing alignment
        for span in SPANS:
            i = len(best) - span
            while i >= 0:
                if any(best[i:i + span]):
                    candidate = list(best)
                    for k in range(i, i + span):
                        candidate[k] = 0
                    got = try_same_length(candidate)
                    if got is not None:
                        best = got
                        progress = True
                i -= span

        # 3. lower individual values toward zero by binary search
        for i in range(len(best)):
            if best[i] == 0:
                continue
            lo, hi = 0, best[i]
            while lo < hi:
                mid = (lo + hi) // 2
                candidate = list(best)
                candidate[i] = mid
                if try_same_length(candidate) is not None:
                    best = candidate
                    hi = mid
                    progress = True
                else:
                    lo = mid + 1

        # 4. cut the tail
        cut = len(best) // 2
        while cut >= 1:
            got = try_candidate(best[:len(best) - cut])
            if got is not None:
                best = got
                progress = True
            else:
                cut //= 2

    stats.end_len = len(best)
    stats.seconds = time.time() - started
    return best, stats


def make_predicate(runner, signature, detail_prefix=None):
    """Accept a candidate only if it reproduces the same property violation."""

    def reproduces(values):
        r = runner(values)
        if not r.failed or r.signature() != signature:
            return False
        if detail_prefix is not None:
            got = r.violations[0].detail if r.violations else ""
            return got.startswith(detail_prefix)
        return True

    return reproduces
