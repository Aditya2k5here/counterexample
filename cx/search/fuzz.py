"""Three ways to look for a schedule that breaks the cluster.

``RandomSearch``    a fresh random run every trial. The honest baseline, and the
                    thing most distributed-systems test suites actually do.
``CoverageGuided``  keep a corpus of runs that reached a cluster state nothing
                    else reached; mutate those instead of starting over. This is
                    AFL's idea applied to schedules rather than file formats.
``BanditGuided``    the same, plus UCB1 over the mutation operators, so the
                    search learns *which kind of edit* is paying off on this
                    particular target rather than sampling operators uniformly.

All three are measured the same way: trials until the first failure, on the same
seeds, against the same bugs. The comparison is only meaningful because the
coverage signal is generic -- it counts role mixes, crashed nodes, partition
shapes and how far the replicas' terms, commits and logs have drifted apart, and
mentions no specific defect.

One property matters more than the search quality: *anything found is
reproducible*. The result carries the choice sequence, and re-running it gives
the identical failure, on any machine, forever.
"""

import math

from ..sim.choices import Rng
from ..sim.scenario import run
from .mutate import OPERATORS, OPERATOR_NAMES, apply_operator


class SearchResult(object):
    def __init__(self, method):
        self.method = method
        self.found = False
        self.trials = 0
        self.failure = None          # the RunResult that failed
        self.choices = None
        self.signature = None
        self.coverage_size = 0
        self.corpus_size = 0
        self.operator_stats = {}

    def to_json(self):
        return {
            "method": self.method, "found": self.found, "trials": self.trials,
            "signature": self.signature, "coverage": self.coverage_size,
            "corpus": self.corpus_size,
            "choices_len": len(self.choices) if self.choices else 0,
            "operator_stats": self.operator_stats,
            "summary": self.failure.summary() if self.failure else None,
        }


class RandomSearch(object):
    name = "random"

    def __init__(self, bugs=(), config=None, seed=0):
        self.bugs = bugs
        self.config = config
        self.seed = seed

    def run(self, budget):
        res = SearchResult(self.name)
        seen = set()
        for i in range(budget):
            res.trials = i + 1
            r = run(seed=self.seed * 1000003 + i, bugs=self.bugs, config=self.config)
            seen |= r.coverage
            if r.failed:
                res.found = True
                res.failure = r
                res.choices = r.choices
                res.signature = r.signature()
                break
        res.coverage_size = len(seen)
        return res


class CoverageGuided(object):
    name = "coverage"
    use_bandit = False

    def __init__(self, bugs=(), config=None, seed=0, corpus_cap=200,
                 fresh_percent=15):
        self.bugs = bugs
        self.config = config
        self.seed = seed
        self.corpus_cap = corpus_cap
        self.fresh_percent = fresh_percent

    # -------------------------------------------------------------- bandit
    def pick_operator(self, rng, counts, rewards, total):
        if not self.use_bandit:
            return rng.below(len(OPERATORS))
        for i in range(len(OPERATORS)):
            if counts[i] == 0:
                return i
        best, best_score = 0, -1.0
        for i in range(len(OPERATORS)):
            mean = rewards[i] / counts[i]
            bonus = math.sqrt(2.0 * math.log(total) / counts[i])
            score = mean + bonus
            if score > best_score:
                best, best_score = i, score
        return best

    # ---------------------------------------------------------------- main
    def run(self, budget):
        res = SearchResult(self.name)
        rng = Rng(self.seed * 7919 + 13)
        corpus = []                 # list of (choices, coverage)
        global_cov = set()
        counts = [0] * len(OPERATORS)
        rewards = [0.0] * len(OPERATORS)
        total = 1

        for i in range(budget):
            res.trials = i + 1
            op_index = None
            if not corpus or rng.below(100) < self.fresh_percent:
                r = run(seed=self.seed * 1000003 + i, bugs=self.bugs,
                        config=self.config)
            else:
                parent = corpus[rng.below(len(corpus))]
                other = corpus[rng.below(len(corpus))][0]
                op_index = self.pick_operator(rng, counts, rewards, total)
                values = apply_operator(op_index, parent[0], rng, other)
                r = run(seed=self.seed * 1000003 + i, values=values,
                        bugs=self.bugs, config=self.config)

            new_cov = r.coverage - global_cov
            if r.failed:
                if op_index is not None:
                    counts[op_index] += 1
                    rewards[op_index] += 1.0
                res.found = True
                res.failure = r
                res.choices = r.choices
                res.signature = r.signature()
                break

            if op_index is not None:
                counts[op_index] += 1
                # Reward is "did this edit reach somewhere new", normalised so a
                # single jackpot cannot pin an arm at the top forever.
                rewards[op_index] += min(1.0, len(new_cov) / 4.0)
                total += 1

            if new_cov:
                global_cov |= r.coverage
                corpus.append((r.choices, r.coverage))
                if len(corpus) > self.corpus_cap:
                    corpus.pop(rng.below(len(corpus) // 2))

        res.coverage_size = len(global_cov)
        res.corpus_size = len(corpus)
        res.operator_stats = {
            OPERATOR_NAMES[i]: {"uses": counts[i], "reward": round(rewards[i], 2)}
            for i in range(len(OPERATORS)) if counts[i]
        }
        return res


class BanditGuided(CoverageGuided):
    name = "bandit"
    use_bandit = True


METHODS = {m.name: m for m in (RandomSearch, CoverageGuided, BanditGuided)}


def search(method, bugs=(), budget=200, seed=0, config=None):
    return METHODS[method](bugs=bugs, config=config, seed=seed).run(budget)
