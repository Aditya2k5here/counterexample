"""The counterexample renderer.

A counterexample nobody can read is barely better than none, so the rendering
path is tested like everything else: it must survive a real trace, name the
property that broke, and end its story at the violation rather than rambling
past it.
"""

import pytest

from cx.search.fuzz import search
from cx.sim.scenario import run
from cx.viz import spacetime

BUG = "commit_index_unclamped"


@pytest.fixture(scope="module")
def failure():
    res = search("random", bugs=[BUG], budget=60, seed=0)
    assert res.found, "the easiest defect was not found in 60 schedules"
    r = run(values=res.choices, bugs=[BUG], keep_trace=True)
    assert r.violations
    return r


def test_the_story_ends_at_the_violation(failure):
    v = failure.violations[0]
    servers = ["s%d" % i for i in range(failure.n_servers)]
    lines = spacetime.story(failure.trace, v, servers)
    assert lines
    assert all(t <= v.time for t, _ in lines)
    assert v.prop in lines[-1][1]


def test_the_story_is_bounded(failure):
    v = failure.violations[0]
    servers = ["s%d" % i for i in range(failure.n_servers)]
    lines = spacetime.story(failure.trace, v, servers, limit=10)
    assert len(lines) <= 11        # the limit, plus the violation itself


def test_the_svg_is_well_formed(failure):
    v = failure.violations[0]
    servers = ["s%d" % i for i in range(failure.n_servers)]
    svg = spacetime.render_svg(failure.trace, servers, ["c0", "c1", "c2"],
                               violation=v)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert svg.count("<svg") == 1
    for node in servers:
        assert ">%s<" % node in svg
    assert v.prop in svg


def test_the_svg_survives_an_empty_trace():
    assert spacetime.render_svg([], ["s0"], []) == "<svg/>"


def test_the_page_is_self_contained(tmp_path, failure):
    v = failure.violations[0]
    servers = ["s%d" % i for i in range(failure.n_servers)]
    case = {
        "bug": BUG,
        "description": "a description",
        "violation": "%s -- %s" % (v.prop, v.detail),
        "svg": spacetime.render_svg(failure.trace, servers, ["c0"], violation=v),
        "story": spacetime.story(failure.trace, v, servers),
        "choices": [1, 2, 3],
        "meta": "meta",
    }
    out = tmp_path / "report.html"
    spacetime.build_page([case], str(out))
    html = out.read_text()
    assert "<svg" in html and BUG in html
    # No network dependencies: a report that stops rendering when a CDN moves
    # is not a report. The SVG namespace URL is a name, not a fetch.
    stripped = html.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "http://" not in stripped and "https://" not in stripped


def test_message_labels_are_short_and_informative():
    from cx.raft.messages import AppendEntries, RequestVote, RequestVoteReply
    ae = AppendEntries(3, "s0", 7, 2, (), 5)
    assert spacetime._msg_label(ae) == "AE t3 prev=7@2 +0 c=5"
    assert spacetime._msg_label(RequestVote(4, "s1", 9, 3)) == "RV t4 (log 9@3)"
    assert spacetime._msg_label(RequestVoteReply(4, True, "s2")) == "RV-yes t4"
