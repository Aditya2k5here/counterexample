# Architecture notes

Why the pieces are shaped the way they are. The README says what it does; this
is the reasoning, including the parts that were wrong first.

---

## 1. The choice sequence is the whole design

A simulation run never calls a random number generator. Every nondeterministic
decision — how long a message takes, whether it is dropped, when an election
timer fires, whether to crash a node, how big the cluster is — is a `draw(n)`
from a `ChoiceStream`, which either replays a recorded integer or invents and
records one.

Three consequences, and they are the project:

**A run is a pure function of a list of integers.** Reproducing a failure needs
no log file, no timestamp, no "it happens about one run in two thousand". It
needs the list.

**Failures can be shrunk without domain knowledge.** Delete a span of integers,
re-run, did it still fail? The shrinker knows nothing about Raft, partitions or
clocks. This is Hypothesis's internal representation, and it is far stronger
than shrinking a domain-specific schedule object, precisely because it does not
understand what it is editing.

**Shrinking has a direction, and the direction is designed in.** Zero has to
mean *nothing unusual*: no fault, minimum delay, no packet loss, smallest
cluster. Then "make the numbers smaller and fewer" means "make the run calmer
and shorter", and a shrinker that understands only integers produces a
counterexample stated in terms of the two crashes that actually mattered.

### The bug in that idea, and the test that caught it

`draw_bool(percent)` was written the obvious way:

```python
return self.draw(100) < percent          # WRONG
```

Same probability, inverted meaning. Value 0 satisfies `0 < 4`, so zero meant
*drop this message* and *make this link slow*. Zeroing a span — the shrinker's
most useful move — turned the network into a black hole instead of a quiet one,
and every "minimised" counterexample was a run where the network had been
shrunk into losing everything.

```python
return self.draw(100) >= 100 - percent   # right
```

`tests/test_determinism.py::test_zero_means_nothing_unusual` is three lines and
is the reason this was found rather than shipped.

## 2. The simulator

Discrete event simulation: a virtual clock in integer milliseconds, a heap of
events ordered by `(time, sequence)` so simultaneous events have a deterministic
total order, and nodes that can only send messages, set timers, and be killed.

Faults available to the search:

| fault | what it models |
|---|---|
| `crash` | process dies, volatile state gone, persisted state kept |
| `restart` | process comes back and replays what it persisted |
| `bounce` | crash and restart a moment later — a process reboot |
| `partition` | the cluster is cut into two groups that cannot talk |
| `one_way_block` | A can reach B, B cannot reach A |
| `heal` | everything reconnects |

`bounce` exists as its own fault rather than as a lucky pairing of `crash` and
`restart`, because a real reboot takes tens of milliseconds and a whole class of
bugs — anything about state that was supposed to be persisted — is only
reachable inside that window. Waiting for the injector to randomly choose
crash-then-restart on the same node inside one election timeout is waiting for a
coincidence.

Asymmetric partitions are in the list deliberately. They are the shape that
breaks the most implementations and the one people forget to write a test for.

Clients sit outside the partition model. Modelling the client as stranded on one
side too is more realistic and less useful: it mostly stops the workload, and a
workload that stops finds nothing.

## 3. Cluster size is part of the search space

Three nodes and five nodes fail differently. A single node that votes twice in
one term elects two leaders in a three-node cluster and does not in a five-node
one. Fixing the size would silently put half the interesting failures out of
reach, so it is drawn per run — with value 0 giving the smaller cluster, so
shrinking prefers the simpler picture.

## 4. Raft, with named defects

One implementation, eight deliberate bugs, each a guard:

```python
if "no_log_up_to_date_check" not in self.bugs:
    ...the correct check...
```

The correct and broken variants share every other line, so when the search
reports that the broken one fails and the correct one does not, that difference
is attributable to the named defect and nothing else.

Implemented: leader election with randomised timeouts, log replication with the
consistency check and conflict backtracking, persistence across crashes,
commitment by majority restricted to the current term, and a replicated register
with at-most-once client semantics. Not implemented: snapshots, log compaction,
membership changes, pre-vote, leadership transfer.

## 5. At-most-once clients, and why it matters for the checker

A client is a sequential process — one outstanding operation at a time, which is
what makes the history checkable. On timeout it retries **with the same
operation id**, and the state machine deduplicates by that id.

Without that, a retried write really is a second write, and a history that
records one invocation while two updates were applied is unlinearizable for
reasons that have nothing to do with consensus. The checker would fire
constantly and be right every time, about the wrong thing.

Operations that never get an answer are recorded as *indeterminate*: they may or
may not have taken effect, and the checker must consider both. Silently
discarding them would accept histories where a value appears from nowhere.

## 6. Checking

Six safety properties, checked against the whole cluster at once — something no
node can do:

`ElectionSafety`, `LeaderAppendOnly`, `LogMatching`, `StateMachineSafety`,
`LeaderCompleteness`, `CommitIndexInBounds`.

Plus **linearizability** of the client-visible history, via Wing & Gong in the
linked-list formulation, memoised on (set of linearised operations, resulting
state). The search is bounded, and hitting the bound reports `UNKNOWN` rather
than `linearizable` — reporting a bound as a pass is how a checker starts
silently agreeing with everything.

Plus one **liveness** property, which took two attempts to state correctly.

### The two checker bugs the tool found in itself

**Leader Completeness applied to the wrong leaders.** The first version compared
every leader against every committed entry. Under a partition, a stale leader
from an older term is still a leader, and it is *entitled* to a different log —
Leader Completeness constrains leaders of terms *later* than the one an entry
was committed in. The checker fired on the correct implementation. The fix is to
record the term each entry was committed in and only check leaders above it.

**Liveness stated at the wrong level.** The first version required "exactly one
leader when the run ends". A healthy cluster is leaderless for a couple of
hundred milliseconds every time an election runs, and whether the run ends during
one is luck. The second version required "some client operation completed after
the network healed", which is better and still brittle: a client four hundred
milliseconds into a retry when the storm ends may legitimately not finish inside
the window. The version that survives asks the cluster-level question — *did the
commit index advance after everything healed* — and skips the check entirely
unless the run actually reached the calm period.

Both were found by pointing the search at the correct implementation, which is
why that validation runs first and its result is reported first.

## 7. Search

Three strategies over the same choice-sequence representation:

- **random** — a fresh random schedule each trial. The honest baseline, and what
  most distributed-systems test suites actually do.
- **coverage** — keep a corpus of runs that reached a cluster state nothing else
  reached; mutate those. AFL applied to schedules.
- **bandit** — the same, plus UCB1 over the mutation operators, so the search
  learns which kind of edit pays off on this target.

The coverage signal is a tuple: role mix, how many nodes are down, how the
cluster is cut, and how far apart the replicas' terms, commit indices and logs
have drifted. It is deliberately generic. A coverage metric that mentioned any
specific defect would make the comparison against random search meaningless — it
would be measuring how well the metric was tuned, not how well the search works.

Mutation operators: zero a span, bump individual values, randomise a span,
duplicate a span, truncate the tail (so the rest is generated fresh), insert,
and splice two corpus entries. `zero_span` is the most productive, because zero
means calm: it quiets a stretch of the run and concentrates the remaining chaos.

## 8. Shrinking

Passes, largest reduction first, repeated to a fixed point: delete spans of 128
down to 1, zero spans, lower individual values by binary search toward zero, cut
the tail.

A candidate is accepted only if it reproduces the **same property violation**.
Drifting onto a different bug would be worse than not shrinking: the report would
name one failure and reproduce another.

Replay during shrinking pads short sequences with **zeros**, not fresh
randomness. This is not a detail. With random padding, a shortened sequence
means "this prefix, and then whatever the generator happens to do", so the
minimal reproduction is not a reproduction at all — and the first version of this
gleefully shrank a failure to *zero decisions*, which reproduced only because the
top-up RNG happened to trigger the same bug. With zero padding, a truncated
sequence has an exact meaning: and then nothing unusual happened.

## 9. What is not modelled

- No byzantine faults; nodes follow the code, correctly or with a seeded defect.
- No disk corruption or partial writes; `persist()` is atomic and durable.
- No message corruption; messages arrive intact or not at all.
- No clock skew between nodes; there is one global virtual clock.
- No snapshots or membership changes, so the bugs that live there are out of
  scope.
- Timing is simulated, so nothing here says anything about real-world
  performance.
