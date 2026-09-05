"""Space-time diagrams: the shrunk counterexample as a picture.

A distributed-systems failure is a story about *ordering*, and ordering is the
one thing a log file is bad at showing. One lane per node, time running left to
right, an arrow for every message that was delivered and a stub for every one
that was dropped. Crashes leave a gap in the lane. Partitions shade the
background. The moment the invariant broke gets a line through the whole
picture.

This only works because the counterexample has been shrunk first. Drawing forty
thousand events produces a solid black rectangle; drawing the thirty that
survived minimisation produces something a person can read in ten seconds and
argue with.

Everything is inlined SVG generated here in Python -- no client-side rendering,
no CDN, no build step.
"""

import html
import json

ROLE_COLOR = {"leader": "#5aa9e6", "candidate": "#e0a44b", "follower": "#6b7280"}
MSG_COLOR = {
    "RequestVote": "#e0a44b",
    "RequestVoteReply": "#c08a3e",
    "AppendEntries": "#5aa9e6",
    "AppendEntriesReply": "#4682ad",
    "ClientRequest": "#8b7ff0",
    "ClientReply": "#6f64c4",
}


def _msg_label(msg):
    kind = msg.kind
    if kind == "RequestVote":
        return "RV t%d (log %d@%d)" % (msg.term, msg.last_log_index, msg.last_log_term)
    if kind == "RequestVoteReply":
        return "RV-%s t%d" % ("yes" if msg.granted else "no", msg.term)
    if kind == "AppendEntries":
        return "AE t%d prev=%d@%d +%d c=%d" % (
            msg.term, msg.prev_log_index, msg.prev_log_term, len(msg.entries),
            msg.leader_commit)
    if kind == "AppendEntriesReply":
        return "AE-%s t%d m=%d" % ("ok" if msg.success else "no", msg.term,
                                   msg.match_index)
    if kind == "ClientRequest":
        return "%s(%s)" % (msg.op, msg.value)
    if kind == "ClientReply":
        return "reply %s" % ("ok" if msg.ok else "redirect")
    return kind


def render_svg(trace, servers, clients, violation=None, width=1180,
               lane_height=54, max_events=400, show_drops=True,
               label_budget=45):
    records = trace.records if hasattr(trace, "records") else trace
    if violation is not None:
        records = [r for r in records if r["t"] <= violation.time + 1]
    records = records[-max_events:] if len(records) > max_events else records
    if not records:
        return "<svg/>"

    lanes = list(servers) + list(clients)
    lane_y = {n: 46 + i * lane_height for i, n in enumerate(lanes)}
    height = 46 + len(lanes) * lane_height + 40

    t0 = records[0]["t"]
    t1 = max(r["t"] for r in records)
    span = max(1, t1 - t0)
    left, right = 108, width - 24

    def x(t):
        return left + (right - left) * (t - t0) / span

    parts = []
    add = parts.append
    add('<svg viewBox="0 0 %d %d" width="100%%" '
        'xmlns="http://www.w3.org/2000/svg" font-family="ui-sans-serif,system-ui,'
        'Segoe UI,Roboto,sans-serif">' % (width, height))
    add('<rect width="%d" height="%d" fill="#0f1115"/>' % (width, height))

    # --- partition bands -------------------------------------------------
    cuts = [r for r in records if r["kind"] in ("partition", "heal")]
    for i, r in enumerate(cuts):
        end = cuts[i + 1]["t"] if i + 1 < len(cuts) else t1
        if r["kind"] != "partition":
            continue
        groups = r["groups"]
        if len(set(groups.values())) < 2:
            continue
        for node, g in groups.items():
            if node not in lane_y or g == 0:
                continue
            add('<rect x="%.1f" y="%.1f" width="%.1f" height="%d" fill="#2a1f33" '
                'opacity="0.85"/>' % (x(r["t"]), lane_y[node] - 18,
                                      max(2, x(end) - x(r["t"])), 30))
        add('<text x="%.1f" y="30" fill="#8b7ff0" font-size="10">partition</text>'
            % x(r["t"]))

    # --- lanes -------------------------------------------------------------
    for n in lanes:
        y = lane_y[n]
        add('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#262b35"/>'
            % (left, y, right, y))
        add('<text x="8" y="%.1f" fill="#e7eaf0" font-size="12">%s</text>'
            % (y + 4, html.escape(n)))

    # --- crash / restart ---------------------------------------------------
    down_since = {}
    for r in records:
        if r["kind"] == "crash" and r["node"] in lane_y:
            down_since[r["node"]] = r["t"]
        elif r["kind"] == "restart" and r["node"] in lane_y:
            start = down_since.pop(r["node"], t0)
            y = lane_y[r["node"]]
            add('<rect x="%.1f" y="%.1f" width="%.1f" height="7" fill="#e0655f" '
                'opacity="0.55"/>' % (x(start), y - 3.5, max(2, x(r["t"]) - x(start))))
    for node, start in down_since.items():
        y = lane_y[node]
        add('<rect x="%.1f" y="%.1f" width="%.1f" height="7" fill="#e0655f" '
            'opacity="0.55"/>' % (x(start), y - 3.5, max(2, x(t1) - x(start))))

    # --- messages ----------------------------------------------------------
    # Labelling every arrow on a busy diagram produces an unreadable smear, so
    # past a budget only the election traffic -- the part that explains *why* a
    # leader changed -- keeps its text.
    n_delivered = sum(1 for r in records if r["kind"] == "deliver")
    label_all = n_delivered <= label_budget

    in_flight = {}
    for r in records:
        if r["kind"] == "send":
            in_flight.setdefault((r["src"], r["dst"], r["msg"].kind), []).append(r)
        elif r["kind"] == "deliver":
            key = (r["src"], r["dst"], r["msg"].kind)
            sent = in_flight.get(key)
            start_t = sent.pop(0)["t"] if sent else r["t"]
            if r["src"] not in lane_y or r["dst"] not in lane_y:
                continue
            colour = MSG_COLOR.get(r["msg"].kind, "#98a2b3")
            x1, y1 = x(start_t), lane_y[r["src"]]
            x2, y2 = x(r["t"]), lane_y[r["dst"]]
            add('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                'stroke-width="1.3" opacity="0.9"/>' % (x1, y1, x2, y2, colour))
            add('<circle cx="%.1f" cy="%.1f" r="2.6" fill="%s"/>' % (x2, y2, colour))
            if label_all or r["msg"].kind.startswith("RequestVote"):
                mx, my = (x1 + x2) / 2, (y1 + y2) / 2
                add('<text x="%.1f" y="%.1f" fill="%s" font-size="8.5" '
                    'opacity="0.85" text-anchor="middle">%s</text>'
                    % (mx, my - 4, colour, html.escape(_msg_label(r["msg"]))))
        elif r["kind"] == "drop" and show_drops:
            if r["src"] not in lane_y or r["dst"] not in lane_y:
                continue
            x1, y1 = x(r["t"]), lane_y[r["src"]]
            y2 = lane_y[r["dst"]]
            add('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e0655f" '
                'stroke-width="1" stroke-dasharray="2 3" opacity="0.5"/>'
                % (x1, y1, x1 + 14, y1 + (7 if y2 > y1 else -7)))

    # --- role changes -------------------------------------------------------
    for r in records:
        if r["kind"] != "state" or r["node"] not in lane_y:
            continue
        y = lane_y[r["node"]]
        colour = ROLE_COLOR.get(r["role"], "#98a2b3")
        add('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" stroke="#0f1115" '
            'stroke-width="1.5"/>' % (x(r["t"]), y, colour))
        add('<text x="%.1f" y="%.1f" fill="%s" font-size="9">%s%d</text>'
            % (x(r["t"]) + 7, y - 8, colour, r["role"][0].upper(), r["term"]))

    # --- client operations ---------------------------------------------------
    for r in records:
        if r["kind"] == "invoke" and r["client"] in lane_y:
            add('<circle cx="%.1f" cy="%.1f" r="3" fill="none" stroke="#4ec9a5"/>'
                % (x(r["t"]), lane_y[r["client"]]))
        elif r["kind"] == "ok" and r["client"] in lane_y:
            add('<circle cx="%.1f" cy="%.1f" r="3" fill="#4ec9a5"/>'
                % (x(r["t"]), lane_y[r["client"]]))
        elif r["kind"] == "apply" and r["node"] in lane_y:
            add('<rect x="%.1f" y="%.1f" width="2.5" height="9" fill="#4ec9a5" '
                'opacity="0.6"/>' % (x(r["t"]), lane_y[r["node"]] + 8))

    # --- the violation --------------------------------------------------------
    if violation is not None:
        vx = x(min(violation.time, t1))
        add('<line x1="%.1f" y1="24" x2="%.1f" y2="%d" stroke="#e0655f" '
            'stroke-width="1.6" stroke-dasharray="4 3"/>' % (vx, vx, height - 26))
        add('<text x="%.1f" y="18" fill="#e0655f" font-size="11" '
            'text-anchor="end">%s</text>'
            % (min(vx + 190, width - 8), html.escape(violation.prop)))

    # --- time axis -------------------------------------------------------------
    add('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#262b35"/>'
        % (left, height - 22, right, height - 22))
    for k in range(5):
        t = t0 + span * k / 4
        add('<text x="%.1f" y="%d" fill="#98a2b3" font-size="10" '
            'text-anchor="middle">%dms</text>' % (x(t), height - 8, t))
    add("</svg>")
    return "".join(parts)


def story(trace, violation, servers, limit=24):
    """The same counterexample as a numbered list of things that happened.

    Structural events -- crashes, restarts, partitions, elections -- are the
    story; applied log entries are the background hum. A run that commits two
    thousand entries and then loses one has a two-line story and a two-thousand
    line log, so consecutive applies by the same replica are collapsed into a
    single range and the structural events are never dropped to make room.
    """
    records = trace.records if hasattr(trace, "records") else trace
    events = []          # (time, text, structural?)
    pending = None       # a run of applies being collapsed

    def flush():
        if pending is None:
            return
        node, first, last, n, cmd = pending
        if n == 1:
            events.append((first[0], "%s applies index %d: %s"
                           % (node, first[1], _short_cmd(cmd)), False))
        else:
            events.append((first[0], "%s applies indices %d-%d (%d entries)"
                           % (node, first[1], last, n), False))

    for r in records:
        if violation is not None and r["t"] > violation.time:
            break
        k = r["kind"]
        if k == "apply":
            if pending and pending[0] == r["node"] and r["index"] == pending[2] + 1:
                pending = (pending[0], pending[1], r["index"], pending[3] + 1, pending[4])
            else:
                flush()
                pending = (r["node"], (r["t"], r["index"]), r["index"], 1, r["cmd"])
            continue
        flush()
        pending = None
        if k == "crash":
            events.append((r["t"], "%s crashes" % r["node"], True))
        elif k == "restart":
            events.append((r["t"], "%s restarts" % r["node"], True))
        elif k == "partition":
            groups = r["groups"]
            if len(set(groups.values())) > 1:
                a = sorted(n for n, g in groups.items() if g == 0)
                b = sorted(n for n, g in groups.items() if g != 0)
                events.append((r["t"], "network splits: {%s} | {%s}"
                               % (",".join(a), ",".join(b)), True))
        elif k == "heal":
            events.append((r["t"], "network heals", True))
        elif k == "block":
            events.append((r["t"], "%s can no longer reach %s" % (r["src"], r["dst"]), True))
        elif k == "state" and r["role"] in ("leader", "candidate"):
            events.append((r["t"], "%s becomes %s in term %d"
                           % (r["node"], r["role"], r["term"]), True))
    flush()

    # Keep every structural event; fill whatever room is left with the applies
    # nearest the failure, since those are the ones the violation talks about.
    structural = [e for e in events if e[2]]
    if len(events) > limit:
        room = max(0, limit - len(structural))
        filler = [e for e in events if not e[2]][-room:] if room else []
        keep = set(id(e) for e in structural) | set(id(e) for e in filler)
        events = [e for e in events if id(e) in keep]
        if len(events) > limit:
            events = events[-limit:]

    out = [(t, text) for t, text, _ in events]
    if violation is not None:
        out.append((violation.time, "VIOLATION -- %s: %s"
                    % (violation.prop, violation.detail)))
    return out


def _short_cmd(cmd):
    if cmd is None:
        return "(no-op)"
    if cmd["op"] == "cas":
        return "cas(%s->%s) %s" % (cmd["expect"], cmd["value"], cmd["op_id"])
    return "%s(%s) %s" % (cmd["op"], cmd["value"], cmd["op_id"])


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>counterexample &mdash; minimised failures</title>
<style>
 body{margin:0;background:#0f1115;color:#e7eaf0;
      font:14px/1.55 ui-sans-serif,system-ui,"Segoe UI",Roboto,sans-serif}
 .wrap{max-width:1240px;margin:0 auto;padding:36px 20px 80px}
 h1{font-size:25px;margin:0 0 6px;letter-spacing:-.02em}
 h2{font-size:17px;margin:40px 0 4px}
 p{color:#98a2b3;max-width:74ch;margin:6px 0 14px}
 .card{background:#171a21;border:1px solid #262b35;border-radius:10px;
       padding:14px 16px;margin:14px 0}
 .bug{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#e0a44b}
 .prop{color:#e0655f;font-weight:600}
 ol{color:#c4ccd8;font-size:13px;line-height:1.7;padding-left:22px}
 ol li:last-child{color:#e0655f;font-weight:600}
 .meta{color:#6b7280;font-size:12px;margin-top:8px;
       font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
 .legend{display:flex;gap:18px;flex-wrap:wrap;color:#98a2b3;font-size:12px;margin:6px 0 14px}
 .sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
 details{margin-top:10px}
 summary{cursor:pointer;color:#98a2b3;font-size:12px}
 pre{overflow-x:auto;background:#12151c;border:1px solid #262b35;border-radius:6px;
     padding:10px;font-size:11px;color:#98a2b3}
</style></head><body><div class="wrap">
<h1>counterexample</h1>
<p>Each of these is a deliberately introduced Raft defect, the schedule that
exposed it, and that schedule after minimisation. The choice sequence under each
diagram reproduces the failure exactly, on any machine.</p>
<div class="legend">
  <span><i class="sw" style="background:#e0a44b"></i>RequestVote</span>
  <span><i class="sw" style="background:#5aa9e6"></i>AppendEntries</span>
  <span><i class="sw" style="background:#8b7ff0"></i>client / partition</span>
  <span><i class="sw" style="background:#e0655f"></i>crashed &amp; dropped messages</span>
  <span><i class="sw" style="background:#4ec9a5"></i>client op / entry applied</span>
</div>
__BODY__
</div></body></html>
"""


def build_page(cases, out_path):
    """``cases``: list of dicts with bug, description, svg, story, choices, stats."""
    body = []
    for c in cases:
        body.append('<h2><span class="bug">%s</span></h2>' % html.escape(c["bug"]))
        body.append("<p>%s</p>" % html.escape(c["description"]))
        body.append('<div class="card">')
        body.append('<div class="prop">%s</div>' % html.escape(c["violation"]))
        body.append(c["svg"])
        body.append("<ol>")
        for t, line in c["story"]:
            body.append("<li>t=%dms &mdash; %s</li>" % (t, html.escape(line)))
        body.append("</ol>")
        body.append('<div class="meta">%s</div>' % html.escape(c["meta"]))
        body.append("<details><summary>reproducing choice sequence "
                    "(%d integers)</summary><pre>%s</pre></details>"
                    % (len(c["choices"]), html.escape(json.dumps(c["choices"]))))
        body.append("</div>")
    with open(out_path, "w") as fh:
        fh.write(PAGE.replace("__BODY__", "\n".join(body)))
    return out_path
