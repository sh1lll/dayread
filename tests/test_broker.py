"""Risk guards and order sending in docs/publication/desk/broker.py."""
import importlib, json, os, stat, sys, threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

DESK = Path(__file__).resolve().parents[1] / "app" / "desk"
sys.path.insert(0, str(DESK))


@pytest.fixture
def broker(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYREAD_HOME", str(tmp_path / "home"))
    import broker as b
    return importlib.reload(b)


def write_accounts(b, *accts):
    b._ensure(); b.ACCOUNTS.write_text(json.dumps({"accounts": list(accts)}))


def paper(**kw):
    a = {"id": "paper", "name": "Paper", "type": "paper", "enabled": True, "equity": 50000, "risk_pct": 0.5,
         "point_value": 2, "max_qty": 10, "max_trades_day": 3, "max_day_risk_pct": 1.5, "symbol": "MNQ"}
    a.update(kw); return a


def sig(i=1, replay=False, risk_pts=20.0, minutes_left=10, **kw):
    now = datetime.now(__import__("broker").NY)
    s = {"id": f"S{i}", "type": "signal", "sym": "NQ", "side": "LONG", "entry": 25000.0, "stop": 25000.0 - risk_pts,
         "target": 25000.0 + 3 * risk_pts, "risk_pts": risk_pts, "ts": now.isoformat(),
         "until": (now + timedelta(minutes=minutes_left)).isoformat(), "replay": replay}
    s.update(kw); return s


class Hook:
    """Local stand-in for a broker webhook bridge."""
    def __init__(self, code=200):
        hook = self; self.calls = []
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                hook.calls.append({"body": self.rfile.read(n).decode(), "headers": dict(self.headers)})
                self.send_response(code); self.end_headers()
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/hook"
    def close(self): self.srv.shutdown()


def webhook(url, **kw):
    a = {"id": "hook", "name": "Bridge", "type": "webhook", "enabled": True, "live": True, "equity": 25000,
         "risk_pct": 0.5, "point_value": 2, "max_qty": 5, "max_trades_day": 2, "max_day_risk_pct": 1.0,
         "symbol": "MNQ", "url": url, "headers": {"Content-Type": "application/json", "X-Token": "secret-token"},
         "payload": "{\"ticker\":\"{symbol}\",\"action\":\"{side_lc}\",\"quantity\":{qty},\"stop\":{stop},\"target\":{target},\"signalId\":\"{id}\"}"}
    a.update(kw); return a


# --- accounts file -----------------------------------------------------------

def test_new_install_has_no_accounts(broker):
    assert broker.accounts() == []
    assert stat.S_IMODE(os.stat(broker.ACCOUNTS).st_mode) == 0o600


def test_accounts_older_versions_made_are_cleaned_up(broker):
    """Earlier versions wrote a paper account and a placeholder bridge by themselves; both go, accounts the owner made or changed stay."""
    broker._ensure(); broker.ACCOUNTS.write_text(json.dumps(broker.TEMPLATE))
    assert broker.accounts() == [] and "example.invalid" not in broker.ACCOUNTS.read_text()
    edited = dict(broker.TEMPLATE["accounts"][0], equity=75000)
    broker.ACCOUNTS.write_text(json.dumps({"accounts": [edited]}))
    assert [a["equity"] for a in broker.accounts()] == [75000]


def test_add_and_remove_account(broker):
    base = {"name": "Prop eval", "equity": 50000, "risk_pct": 0.5, "symbol": "MNQ", "max_qty": 5, "max_trades_day": 2, "max_day_risk_pct": 1}
    a = broker.add_account({"type": "paper", **base}); b = broker.add_account({"type": "paper", **base})
    assert (a["id"], b["id"]) == ("propeval", "propeval2")
    assert broker.size(next(x for x in broker.accounts() if x["id"] == "propeval"), {"risk_pts": 20})[0] == 5
    with pytest.raises(ValueError, match="between"): broker.add_account({"type": "paper", **base, "risk_pct": 3})
    with pytest.raises(ValueError, match="MNQ or NQ"): broker.add_account({"type": "paper", **base, "symbol": "ES"})
    h = broker.add_account({"type": "webhook", **base, "url": "https://b.example.com/x", "auth": "Bearer t"})
    raw = next(x for x in broker.accounts() if x["id"] == h["id"])
    assert raw["live"] is False and raw["headers"]["Authorization"] == "Bearer t" and "{qty}" in raw["payload"]
    broker.set_mode("propeval", "confirm"); broker.remove_account("propeval")
    assert "propeval" not in broker.modes() and all(x["id"] != "propeval" for x in broker.accounts())
    with pytest.raises(ValueError): broker.remove_account("nope")


def test_public_view_hides_url_path_and_headers(broker):
    p = broker.public(webhook("https://bridge.example.com/secret/path?key=abc"))
    assert p["host"] == "bridge.example.com"
    assert "headers" not in p and "url" not in p and "payload" not in p
    assert "secret" not in json.dumps(p)


# --- guards --------------------------------------------------------------------

def test_paper_fill_is_logged(broker):
    write_accounts(broker, paper())
    r = broker.send("paper", sig())
    assert r["ok"] and r["order"]["status"] == "filled" and r["order"]["simulated"]
    # 50,000 × 0.5% = $250; 20 pts × $2 = $40 per contract → 6 contracts
    assert r["order"]["qty"] == 6
    assert len(broker.orders()) == 1


def test_kill_switch_blocks(broker):
    write_accounts(broker, paper()); broker.set_kill(True)
    assert broker.send("paper", sig())["error"] == "kill switch is on"
    broker.set_kill(False)
    assert broker.send("paper", sig())["ok"]


def test_disabled_account_blocks(broker):
    write_accounts(broker, paper(enabled=False))
    assert "disabled" in broker.send("paper", sig())["error"]


def test_webhook_needs_live_flag(broker):
    write_accounts(broker, webhook("http://127.0.0.1:9/never", live=False))
    assert "live sending is off" in broker.send("hook", sig())["error"]


def test_past_day_signals_are_never_sent(broker):
    hook = Hook()
    try:
        write_accounts(broker, paper(), webhook(hook.url))
        for acct in ("paper", "hook"): assert "test day" in broker.send(acct, sig(replay=True))["error"]
        assert hook.calls == [] and broker.orders() == []
    finally: hook.close()


def test_expired_live_signal_blocks(broker):
    write_accounts(broker, paper())
    assert broker.send("paper", sig(minutes_left=-1))["error"] == "signal has expired"


def test_until_without_timezone_is_new_york_time(broker):
    """The engine formats its own timestamp; a zone-less one must not crash the live check."""
    write_accounts(broker, paper())
    naive = lambda m: (datetime.now(broker.NY) + timedelta(minutes=m)).replace(tzinfo=None).isoformat()
    assert broker.send("paper", sig(1, until=naive(10)))["ok"]
    assert broker.send("paper", sig(2, until=naive(-1)))["error"] == "signal has expired"


def test_stop_too_wide_for_full_size_contract(broker):
    write_accounts(broker, paper(point_value=20, symbol="NQ"))     # $250 risk, 60 pts × $20 = $1,200
    assert "too wide" in broker.send("paper", sig(risk_pts=60))["error"]


def test_hard_cap_one_percent(broker):
    write_accounts(broker, paper(risk_pct=5, max_qty=100))          # asks 5%, gets 1%: $500 / $40 = 12
    r = broker.send("paper", sig())
    assert r["order"]["qty"] == 12 and r["order"]["risk_pct"] <= 1.0


def test_max_qty_cap(broker):
    write_accounts(broker, paper(max_qty=2))
    assert broker.send("paper", sig())["order"]["qty"] == 2


def test_daily_trade_limit(broker):
    write_accounts(broker, paper(max_trades_day=2, max_day_risk_pct=10))
    assert broker.send("paper", sig(1))["ok"] and broker.send("paper", sig(2))["ok"]
    assert broker.send("paper", sig(3))["error"] == "daily trade limit reached"


def test_daily_risk_budget(broker):
    write_accounts(broker, paper(max_trades_day=10, max_day_risk_pct=0.9))   # each trade ≈ 0.48%
    assert broker.send("paper", sig(1))["ok"]
    assert broker.send("paper", sig(2))["error"] == "daily risk budget reached"


# --- webhook -------------------------------------------------------------------

def test_webhook_payload_and_headers(broker):
    hook = Hook()
    try:
        write_accounts(broker, webhook(hook.url))
        r = broker.send("hook", sig())
        assert r["ok"] and r["order"]["status"] == "sent"
        body = json.loads(hook.calls[0]["body"])
        assert body == {"ticker": "MNQ", "action": "buy", "quantity": 3, "stop": 24980.0, "target": 25060.0, "signalId": "S1"}
        assert hook.calls[0]["headers"]["X-Token"] == "secret-token"
    finally: hook.close()


def test_webhook_failure_logged_and_not_counted(broker):
    hook = Hook(code=500)
    try:
        write_accounts(broker, webhook(hook.url, max_trades_day=1))
        r = broker.send("hook", sig(1))
        assert not r["ok"] and r["error"] == "HTTP 500" and r["order"]["status"] == "failed"
        assert broker._today_used("hook", sig()["ts"][:10])[0] == 0
    finally: hook.close()


# --- duplicates ----------------------------------------------------------------

def test_same_signal_not_sent_twice_to_one_account(broker):
    """A double-click, or Confirm after Auto already sent, must not place a second order."""
    write_accounts(broker, paper())
    s = sig(1)
    assert broker.send("paper", s)["ok"]
    r = broker.send("paper", s)
    assert not r["ok"] and "already" in r["error"]
    assert len(broker.orders()) == 1


def test_concurrent_sends_place_one_order(broker):
    """Auto (engine thread) and a manual send (web thread) racing on the same signal."""
    hook = Hook()
    try:
        write_accounts(broker, webhook(hook.url, max_trades_day=5, max_day_risk_pct=5))
        s = sig(1); barrier = threading.Barrier(8)
        def go(): barrier.wait(); broker.send("hook", s)
        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        assert len(hook.calls) == 1
        assert len([o for o in broker.orders() if o["status"] == "sent"]) == 1
    finally: hook.close()
