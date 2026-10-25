"""Backtest: trade history of the signal set over a date range. Results only — no rules, levels or parameters are exposed."""
import math, os, random
from pathlib import Path

HERE = Path(__file__).resolve().parent

def _source():
    """Where the trade history lives and what its columns are called: DAYREAD_HISTORY, else a local private_paths.py, else history/trades.parquet."""
    try: import private_paths; return private_paths.HISTORY, private_paths.COLUMNS
    except ImportError: pass
    return Path(os.environ.get("DAYREAD_HISTORY", HERE.parents[1] / "history" / "trades.parquet")), {"day": "day", "side": "side", "r": "r"}

def _load():
    import pandas as pd
    path, c = _source()
    if os.environ.get("DAYREAD_HISTORY"): path, c = Path(os.environ["DAYREAD_HISTORY"]), {"day": "day", "side": "side", "r": "r"}
    z = pd.read_parquet(path).sort_values(c["day"]).reset_index(drop=True)
    side = c.get("side_from_sign")
    return [{"day": pd.Timestamp(d).strftime("%Y-%m-%d"), "side": ("SHORT" if s > 0 else "LONG") if side else str(s).upper(), "r": float(x)}
            for d, s, x in zip(z[c["day"]], z[c["side"]], z[c["r"]])]

def run(start=None, end=None, risk_pct=0.5):
    rows = [t for t in _load() if (not start or t["day"] >= start) and (not end or t["day"] <= end)]
    if not rows: return {"n": 0}
    risk = max(0.05, min(1.0, float(risk_pct))) / 100.0
    r = [t["r"] for t in rows]; n = len(r); wins = sum(1 for x in r if x > 0); gp = sum(x for x in r if x > 0); gl = -sum(x for x in r if x < 0)
    cum, eq, peak, mdd_r, peak_r, curve = 0.0, 1.0, 1.0, 0.0, 0.0, []
    streak = worst = 0; mdd_pct = 0.0
    for t in rows:
        cum += t["r"]; eq *= 1 + risk * t["r"]; peak = max(peak, eq); peak_r = max(peak_r, cum); mdd_r = max(mdd_r, peak_r - cum); mdd_pct = max(mdd_pct, 1 - eq / peak)
        streak = streak + 1 if t["r"] <= 0 else 0; worst = max(worst, streak); curve.append({"d": t["day"], "r": round(cum, 3), "eq": round((eq - 1) * 100, 2)})
    rng = random.Random(7); boots = sorted(sum(rng.choice(r) for _ in range(n)) / n for _ in range(2000)); mean = sum(r) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / max(n - 1, 1)); years = {}
    for t in rows: years.setdefault(t["day"][:4], []).append(t["r"])
    return {"n": n, "start": rows[0]["day"], "end": rows[-1]["day"], "win": wins / n, "avg_r": mean, "total_r": cum, "pf": (gp / gl) if gl else None,
            "ci_lo": boots[50], "ci_hi": boots[1949], "max_dd_r": mdd_r, "max_dd_pct": mdd_pct * 100, "worst_streak": worst, "end_equity_pct": (eq - 1) * 100, "risk_pct": risk * 100,
            "per_year": [{"year": y, "n": len(v), "win": sum(1 for x in v if x > 0) / len(v), "avg_r": sum(v) / len(v)} for y, v in sorted(years.items())],
            "curve": curve, "trades": [{"day": t["day"], "side": t["side"], "r": round(t["r"], 2)} for t in rows[::-1]]}
