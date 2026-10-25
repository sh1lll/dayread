"""Backtest summary in docs/publication/desk/backtest.py, on a hand-made trade list."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "desk"))
import backtest  # noqa: E402

TRADES = [  # day, side, R
    ("2023-03-01", "LONG", 3.0), ("2023-06-01", "SHORT", -1.0), ("2024-01-02", "LONG", -1.0),
    ("2024-02-01", "LONG", -1.0), ("2024-05-01", "SHORT", 3.0), ("2025-01-02", "LONG", -1.0),
]


@pytest.fixture(autouse=True)
def fake_history(monkeypatch):
    monkeypatch.setattr(backtest, "_load", lambda: [{"day": d, "side": s, "r": r} for d, s, r in TRADES])


def test_summary_numbers():
    out = backtest.run(risk_pct=1.0)
    assert out["n"] == 6 and out["start"] == "2023-03-01" and out["end"] == "2025-01-02"
    assert out["win"] == pytest.approx(2 / 6)
    assert out["total_r"] == pytest.approx(2.0) and out["avg_r"] == pytest.approx(2 / 6)
    assert out["pf"] == pytest.approx(6 / 4)
    assert out["worst_streak"] == 3                       # Jun 2023, Jan and Feb 2024
    assert out["max_dd_r"] == pytest.approx(3.0)          # running total +3 → 0
    assert out["ci_lo"] <= out["avg_r"] <= out["ci_hi"]
    assert out["trades"][0]["day"] == "2025-01-02"       # newest first


def test_compounding_at_one_percent():
    eq = 1.0
    for _, _, r in TRADES: eq *= 1 + 0.01 * r
    assert backtest.run(risk_pct=1.0)["end_equity_pct"] == pytest.approx((eq - 1) * 100)


def test_per_year():
    years = {y["year"]: y for y in backtest.run()["per_year"]}
    assert years["2024"]["n"] == 3 and years["2024"]["win"] == pytest.approx(1 / 3)
    assert years["2025"]["avg_r"] == pytest.approx(-1.0)


def test_date_filter_and_empty_range():
    assert backtest.run("2024-01-01", "2024-12-31")["n"] == 3
    assert backtest.run("2030-01-01") == {"n": 0}


def test_risk_is_clamped():
    assert backtest.run(risk_pct=50)["risk_pct"] == pytest.approx(1.0)
    assert backtest.run(risk_pct=0)["risk_pct"] == pytest.approx(0.05)


def test_output_carries_results_only():
    out = backtest.run()
    assert set(out["trades"][0]) == {"day", "side", "r"}
