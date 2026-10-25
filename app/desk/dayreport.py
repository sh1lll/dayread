"""End-of-day report: every call the read made, grouped by pillar, with what actually happened.

Ground truth comes from the same day's facts: the day's running high/low (status lines every few minutes,
exact new highs), the touches, holds and breaks, and the last reported price. Where that evidence is too
coarse to judge a call fairly, the call is shown with its outcome and no verdict.
"""
from datetime import datetime, timedelta

HIT, MISS, NONE = "right", "wrong", "not graded"
ET_CLOSE = "16:00"

def _t(s):
    try: return datetime.fromisoformat(str(s).replace(" ", "T", 1))
    except (TypeError, ValueError): return None

def _hm(s):
    t = _t(s); return t.strftime("%H:%M") if t else "—"

def _px(v):
    return "—" if v is None else f"{round(v * 4) / 4:,.2f}"

def _pct(p):
    return "—" if p is None else f"{round(p * 100)}%"


class Path:
    """What price did during the day, as far as the facts show it."""
    def __init__(self, facts):
        self.obs = []                                   # (time, lo, hi, exact)
        self.touch = []                                 # (time, level)
        self.price = None; self.price_at = None
        lo = hi = None
        for f in facts:
            t = _t(f.get("at"))
            if t is None: continue
            if f["f"] == "status":
                lo, hi = f.get("lo"), f.get("hi"); self.obs.append((t, lo, hi, False)); self.price, self.price_at = f.get("price"), t
            elif f["f"] == "range":
                lo, hi = f.get("lo"), f.get("hi"); self.obs.append((t, lo, hi, True))
            elif f["f"] == "touch" and f.get("l") is not None:
                self.touch.append((t, f["l"]))
        first = next((_t(f.get("at")) for f in facts if _t(f.get("at"))), None)
        self.tz = first.tzinfo if first else None
        self.day = first.date() if first else None

    def at(self, when):
        """Cumulative (lo, hi) as known at `when`."""
        best = None
        for t, lo, hi, _ in self.obs:
            if t <= when: best = (lo, hi)
        return best

    def final(self):
        return (self.obs[-1][1], self.obs[-1][2]) if self.obs else None

    def first_reach(self, level, after):
        """(time, exact) of the first sign price was at `level` after `after`, or (None, None)."""
        cands = [(t, True) for t, l in self.touch if t >= after and abs(l - level) <= 0.75]
        prev = None
        for t, lo, hi, exact in self.obs:
            if t >= after and lo is not None and lo <= level <= hi and (prev is None or not (prev[0] <= level <= prev[1])):
                cands.append((t, exact)); break
            prev = (lo, hi)
        if not cands: return None, None
        return min(cands, key=lambda c: c[0])

    def reached_by(self, level, deadline):
        r = self.at(deadline); return None if r is None or r[0] is None else r[0] <= level <= r[1]


def _row(at, call, outcome, verdict):
    return {"at": _hm(at), "call": call, "outcome": outcome, "verdict": verdict}


def build(facts, signals=(), results=None):
    """facts: the run's facts in order (each with "at"); signals: the run's signals; results: {(day, side): R} from the trade history."""
    P = Path(facts); day = P.day
    if day is None: return None
    T = lambda hhmm: datetime.combine(day, datetime.strptime(hhmm, "%H:%M").time()).replace(tzinfo=P.tz)
    fin = P.final(); close = P.price
    rep = {k: [] for k in ("bias", "where", "when", "how", "payoff")}
    first = {}
    for f in facts: first.setdefault(f["f"], f)

    # --- Bias
    if (f := first.get("openrange")):
        r = P.at(T("10:00")); act = round(r[1] - r[0]) if r and r[0] is not None else None
        rep["bias"].append(_row(f["at"], f"First 30 min range ≈ {f['c']} pts ({f['lo']}–{f['hi']})", f"{act} pts" if act is not None else "—",
                                NONE if act is None else HIT if f["lo"] <= act <= f["hi"] else MISS))
    if (f := first.get("push")):
        done = first.get("pushdone")
        rep["bias"].append(_row(f["at"], f"Overnight {f['side']} {_px(f['px'])} trades first (~{f['p']}%)",
                                f"yes, at {_hm(done['at'])}" if done else "no", HIT if done else MISS))
    if (f := first.get("fake")) and f.get("adv") is not None:
        done = first.get("pushdone"); r = P.at(_t(done["at"])) if done else P.final()
        went = r is not None and r[0] is not None and r[0] < f["adv"] - 0.25
        said_done = f.get("tier") != "hi"
        rep["bias"].append(_row(f["at"], "Opening dip is done" if said_done else "Opening dip goes further first",
                                f"lowest before the push {_px(r[0]) if r else '—'} (said: {'not below' if said_done else 'below'} {_px(f['adv'])})", HIT if said_done != went else MISS))
    if (f := first.get("gap")) and f.get("c") is not None:
        until = f.get("until") or ET_CLOSE; filled = P.reached_by(f["c"], T(until))
        rep["bias"].append(_row(f["at"], f"Gap {f.get('pts') or ''} pts fills by {until} {f.get('fill', '')} (a coin flip)",
                                "—" if filled is None else "filled" if filled else "not filled", NONE))
    if (f := first.get("lean")) and f.get("ref") is not None and close is not None:
        up = close > f["ref"]
        rep["bias"].append(_row(f["at"], f"Day closes {'above' if f['up'] else 'below'} {_px(f['ref'])} (~{_pct(f['p'])})",
                                f"last price {_px(close)} at {P.price_at.strftime('%H:%M')}", HIT if up == bool(f["up"]) else MISS))
    else:
        rep["bias"].append(_row(None, "Day direction: no call", "—", NONE))
    for f in [x for x in facts if x["f"] == "dayread"]:
        then = P.at(_t(f["at"]))
        if not then or not fin: continue
        if f.get("hs"):
            later_high = fin[1] > then[1] + 0.25
            rep["bias"].append(_row(f["at"], f"Day high {'still to come' if f['hs'] == 'to come' else 'already in'} ({f['hp']}%)",
                                    f"high {_px(then[1])} then, {_px(fin[1])} at the end", HIT if later_high == (f["hs"] == "to come") else MISS))
        if f.get("ls"):
            later_low = fin[0] < then[0] - 0.25
            rep["bias"].append(_row(f["at"], f"Day low {'still to come' if f['ls'] == 'to come' else 'already in'} ({f['lp']}%)",
                                    f"low {_px(then[0])} then, {_px(fin[0])} at the end", HIT if later_low == (f["ls"] == "to come") else MISS))
    for f in [x for x in facts if x["f"] == "extremes"][:1]:
        if fin: rep["bias"].append(_row(f["at"], f"Day high ≤ {_px(f['hi'])}, low ≥ {_px(f['lo'])} (80%)", f"high {_px(fin[1])}, low {_px(fin[0])}",
                                        HIT if fin[1] <= f["hi"] and fin[0] >= f["lo"] else MISS))

    # --- Where: the levels named, and whether they were reached in time
    seen = set()
    for f in [x for x in facts if x["f"] == "levels"]:
        deadline = T(f.get("until") or ET_CLOSE)
        for lv in (f.get("list") or [])[:3]:
            k = round(lv["l"], 1)
            if k in seen: continue
            seen.add(k); got = P.reached_by(lv["l"], deadline); p = lv.get("p")
            verdict = NONE if got is None or p is None or 0.3 < p < 0.7 else HIT if got == (p >= 0.7) else MISS
            rep["where"].append(_row(f["at"], f"{_px(lv['l'])} reached {f.get('by')} ({_pct(p)})", "—" if got is None else "reached" if got else "not reached", verdict))

    # --- When: timing calls, one per level (the first call made for it)
    timed = set()
    for f in facts:
        if f["f"] not in ("lc", "mc", "ww", "ap") or f.get("l") is None: continue
        k = round(f["l"], 1)
        if k in timed: continue
        timed.add(k); made = _t(f["at"]); hit_t, exact = P.first_reach(f["l"], made)
        if f["f"] == "mc":
            by = _t(f.get("by")); call = f"{_px(f['l'])} tagged within 60 s (by {_hm(f.get('by'))})"
            v = (HIT if hit_t and hit_t <= by + timedelta(seconds=10) else MISS) if (hit_t is None or exact) else NONE
        elif f["f"] == "ww":
            lo, hi = _t(f.get("lo")), _t(f.get("hi")); call = f"{_px(f['l'])} touched {_hm(f.get('lo'))}–{_hm(f.get('hi'))}"
            v = (HIT if hit_t and lo <= hit_t <= hi + timedelta(minutes=1) else MISS) if (hit_t is None or exact) else NONE
        else:
            eta = _t(f.get("time") if f["f"] == "lc" else f.get("eta")); call = f"{_px(f['l'])} touched ≈ {_hm(f.get('time') if f['f'] == 'lc' else f.get('eta'))} ±2 min"
            v = (HIT if hit_t and abs((hit_t - eta).total_seconds()) <= 150 else MISS) if (hit_t is None or exact) else NONE
        out = "not touched" if hit_t is None else f"touched {hit_t.strftime('%H:%M:%S')}" + ("" if exact else " (seen at a 5-min check)")
        rep["when"].append(_row(f["at"], call, out, v))

    # --- How: for each touch that resolved, the last read before the outcome and the wick
    touches = {}
    for f in facts:
        if f.get("l") is None or f["f"] not in ("touch", "read", "candle", "hold", "brk"): continue
        d = touches.setdefault(round(f["l"], 2), {"l": f["l"]})
        if f["f"] == "touch": d["at"] = f["at"]
        elif f["f"] == "read" and "out" not in d: d["read"] = f
        elif f["f"] == "candle" and "out" not in d: d["wick"] = f.get("wick")
        elif f["f"] in ("hold", "brk") and "out" not in d: d["out"] = f["f"]; d["poke"] = f.get("poke"); d["out_at"] = f["at"]
    for d in sorted([d for d in touches.values() if "out" in d and "at" in d], key=lambda d: d["at"]):
        held = d["out"] == "hold"; r = d.get("read")
        if r:
            said_hold = r["p"] >= 0.5
            rep["how"].append(_row(d["at"], f"{_px(d['l'])}: +{r['s']} s read says {'hold' if said_hold else 'break'} ({_pct(r['p'] if said_hold else 1 - r['p'])})",
                                   "held" if held else "broke", HIT if said_hold == held else MISS))
        if d.get("wick") is not None and held and d.get("poke") is not None:
            rep["how"].append(_row(d["at"], f"{_px(d['l'])}: wick ≈ {round(d['wick'])} pts", f"poked {d['poke']} pts", NONE))

    # --- Payoff: the trade, from the trade history for that day
    for s in signals:
        r = (results or {}).get((str(s.get("ts", ""))[:10], s.get("side")))
        out = "—" if r is None else "target hit +3R" if r >= 2.99 else "stopped −1R" if r <= -0.99 else f"closed at the time limit {r:+.2f}R"
        rep["payoff"].append(_row(s.get("ts"), f"{s.get('side')} {_px(s.get('entry'))}, stop {_px(s.get('stop'))}, target {_px(s.get('target'))}",
                                  out, NONE if r is None else HIT if r > 0 else MISS))

    summary = {k: {"right": sum(r["verdict"] == HIT for r in v), "graded": sum(r["verdict"] in (HIT, MISS) for r in v)} for k, v in rep.items()}
    return {"day": str(day), "pillars": rep, "summary": summary,
            "note": "Graded from what the day's own updates show (high/low checks every few minutes, exact new highs and touches). Calls the evidence cannot settle are shown without a verdict."}
