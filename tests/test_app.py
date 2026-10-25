"""End-to-end smoke test of dayread_app.py over HTTP, with a stand-in engine.

The real desk/signals.py and desk/ui.html are swapped for stubs in a temporary copy,
so this checks the app shell and broker wiring (run → signal → ticket → order →
ledger), not the signals themselves.
"""
import http.client, json, os, shutil, socket, subprocess, sys, textwrap, time
from pathlib import Path

import pytest

PUB = Path(__file__).resolve().parents[1] / "app"

STUB_ENGINE = textwrap.dedent('''
    import json, sys
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    live = "--live" in sys.argv
    print("engine warming up")                                   # non-JSON lines are ignored
    close = now.replace(hour=15, minute=58, second=0, microsecond=0).isoformat()       # late in the day, so the run counts as a full day
    print(json.dumps({"type": "fact", "at": close, "f": "touch", "l": 25000.0, "side": "support", "poke": None}), flush=True)
    print(json.dumps({"type": "signal", "id": "T1", "sym": "NQ", "side": "SHORT", "entry": 25000.0,
                      "stop": 25020.0, "target": 24940.0, "risk_pts": 20.0, "ts": now.isoformat(),
                      "until": (now + timedelta(minutes=5)).isoformat(), "replay": not live}), flush=True)
''')


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "proj"; pub = root / "app"
    shutil.copytree(PUB, pub, ignore=shutil.ignore_patterns("__pycache__"))
    (pub / "desk" / "signals.py").write_text(STUB_ENGINE)
    (pub / "desk" / "ui.html").write_text("<!doctype html><title>stub</title>")
    port = free_port(); env = dict(os.environ, DAYREAD_HOME=str(tmp_path / "home"))
    proc = subprocess.Popen([sys.executable, str(pub / "dayread_app.py"), "--no-browser", "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(50):
        try: socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
        except OSError: time.sleep(0.1)
    assert call(port, "POST", "/api/accounts/add", {"type": "paper", "name": "Paper", "equity": 50000, "risk_pct": 0.5, "symbol": "MNQ", "max_qty": 10, "max_trades_day": 3, "max_day_risk_pct": 1.5})[1]["account"]["id"] == "paper"
    yield port
    proc.terminate(); proc.wait(5)


def call(port, method, path, body=None, headers=None, host="127.0.0.1", timeout=5):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    h = {"Host": f"{host}:{port}", "X-DayRead": "1", "Content-Type": "application/json"}
    h.update(headers or {})
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse(); data = r.read(); c.close()
    try: return r.status, json.loads(data)
    except ValueError: return r.status, data


def events_until_done(port, start=0, timeout=20):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request("GET", f"/api/events?from={start}", headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse(); out = []; end = time.time() + timeout
    while time.time() < end:
        line = r.fp.readline().decode().strip()
        if line.startswith("data: "):
            ev = json.loads(line[6:]); out.append(ev)
            if ev["t"] == "done": break
    c.close(); return out


def test_page_and_request_guards(app):
    assert call(app, "GET", "/")[0] == 200
    assert call(app, "GET", "/api/state", host="evil.example")[0] == 403          # DNS-rebinding guard
    assert call(app, "POST", "/api/stop", {}, headers={"X-DayRead": ""})[0] == 403  # cross-site POST guard


def test_bad_inputs_get_a_reply(app):
    assert "Read view" in call(app, "POST", "/api/run", {"kind": "read"})[1]["error"]
    assert "Check the date" in call(app, "POST", "/api/run", {"date": "yesterday"})[1]["error"]
    assert "whole number" in call(app, "POST", "/api/run", {"date": "2026-10-08", "step": "abc"})[1]["error"]
    c = http.client.HTTPConnection("127.0.0.1", app, timeout=2)
    c.request("GET", "/api/events?from=abc", headers={"Host": f"127.0.0.1:{app}"})
    assert c.getresponse().status == 200; c.close()


def test_screen_gets_numbers_never_engine_wording(app):
    call(app, "POST", "/api/run", {"date": "2026-10-08"})
    evs = events_until_done(app); blob = json.dumps(evs)
    assert {"t": "fact", "at": evs[0]["at"], "f": "touch", "l": 25000.0, "side": "support", "poke": None} in evs
    assert '"text"' not in blob


def test_run_ends_with_a_report(app):
    call(app, "POST", "/api/run", {"date": "2026-10-08"})
    evs = events_until_done(app)
    rep = next(e for e in evs if e["t"] == "report")
    assert set(rep["pillars"]) == {"bias", "where", "when", "how", "payoff"}
    assert rep["pillars"]["payoff"][0]["call"].startswith("SHORT 25,000.00")


def test_no_report_for_a_day_that_stopped_early(tmp_path):
    """A replay that ends before the close would grade calls on half a day, so no report is made."""
    root = tmp_path / "p"; pub = root / "app"; shutil.copytree(PUB, pub, ignore=shutil.ignore_patterns("__pycache__"))
    (pub / "desk" / "signals.py").write_text('import json\nprint(json.dumps({"type": "fact", "at": "2026-10-08T09:40:00-04:00", "f": "touch", "l": 25000.0, "side": "support", "poke": None}))\n')
    port = free_port(); env = dict(os.environ, DAYREAD_HOME=str(tmp_path / "h"))
    proc = subprocess.Popen([sys.executable, str(pub / "dayread_app.py"), "--no-browser", "--port", str(port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
            except OSError: time.sleep(0.1)
        call(port, "POST", "/api/run", {"date": "2026-10-08"})
        evs = events_until_done(port)
        assert any(e["t"] == "fact" for e in evs) and not any(e["t"] == "report" for e in evs)
    finally: proc.terminate(); proc.wait(5)


def test_past_day_signals_cannot_be_sent(app):
    call(app, "POST", "/api/mode", {"account": "paper", "mode": "auto"})
    call(app, "POST", "/api/run", {"date": "2026-10-08"})
    evs = events_until_done(app)
    sig = next(e for e in evs if e["t"] == "signal")
    assert sig["replay"] is True and sig["plans"] == [] and not [e for e in evs if e["t"] == "order"]
    assert "test day" in call(app, "POST", "/api/send", {"signal": "T1", "account": "paper"})[1]["error"]


def test_live_signal_ticket_paper_fill_and_no_duplicate(app):
    assert call(app, "POST", "/api/mode", {"account": "paper", "mode": "confirm"})[1] == {"ok": True}
    assert call(app, "POST", "/api/run", {"mode": "live"})[1]["ok"]
    evs = events_until_done(app)
    sig = next(e for e in evs if e["t"] == "signal")
    plan = sig["plans"][0]
    assert plan["account"] == "paper" and plan["blocked"] is None and plan["qty"] == 6   # $250 / (20 pts × $2)
    r = call(app, "POST", "/api/send", {"signal": "T1", "account": "paper"})[1]
    assert r["ok"] and r["order"]["status"] == "filled"
    again = call(app, "POST", "/api/send", {"signal": "T1", "account": "paper"})[1]
    assert not again["ok"] and "already sent" in again["error"]
    assert len(call(app, "GET", "/api/orders")[1]) == 1
    acct = next(a for a in call(app, "GET", "/api/accounts")[1] if a["id"] == "paper")
    assert acct["today_trades"] == 1


def test_auto_mode_sends_once(app):
    call(app, "POST", "/api/mode", {"account": "paper", "mode": "auto"})
    call(app, "POST", "/api/run", {"mode": "live"})
    evs = events_until_done(app)
    orders = [e for e in evs if e["t"] == "order"]
    assert len(orders) == 1 and orders[0]["ok"]
    assert "already sent" in call(app, "POST", "/api/send", {"signal": "T1", "account": "paper"})[1]["error"]


def test_kill_switch_blocks_tickets(app):
    call(app, "POST", "/api/mode", {"account": "paper", "mode": "confirm"})
    assert call(app, "POST", "/api/kill", {"on": True})[1]["kill"] is True
    call(app, "POST", "/api/run", {"mode": "live"})
    sig = next(e for e in events_until_done(app) if e["t"] == "signal")
    assert sig["plans"][0]["blocked"] == "kill switch is on"


def test_accounts_start_empty_and_are_managed_here(tmp_path):
    """A fresh install has no accounts; adding needs valid numbers; removing works."""
    root = tmp_path / "p"; shutil.copytree(PUB, root / "app", ignore=shutil.ignore_patterns("__pycache__"))
    port = free_port(); env = dict(os.environ, DAYREAD_HOME=str(tmp_path / "h"))
    proc = subprocess.Popen([sys.executable, str(root / "app/dayread_app.py"), "--no-browser", "--port", str(port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
            except OSError: time.sleep(0.1)
        assert call(port, "GET", "/api/accounts")[1] == []
        assert "Risk per trade" in call(port, "POST", "/api/accounts/add", {"type": "paper", "name": "X", "equity": 1000, "risk_pct": 9, "symbol": "MNQ", "max_qty": 1, "max_trades_day": 1, "max_day_risk_pct": 1})[1]["error"]
        assert "https" in call(port, "POST", "/api/accounts/add", {"type": "webhook", "name": "Bridge", "equity": 1000, "risk_pct": 0.5, "symbol": "MNQ", "max_qty": 1, "max_trades_day": 1, "max_day_risk_pct": 1, "url": "ftp://x"})[1]["error"]
        r = call(port, "POST", "/api/accounts/add", {"type": "webhook", "name": "My Bridge", "equity": 25000, "risk_pct": 0.25, "symbol": "NQ", "max_qty": 2, "max_trades_day": 2, "max_day_risk_pct": 0.5, "url": "https://bridge.example.com/hook/SECRET", "auth": "Bearer TOKEN"})[1]
        acct = r["account"]; assert acct["host"] == "bridge.example.com" and acct["live"] is False and "SECRET" not in json.dumps(r) and "TOKEN" not in json.dumps(r)
        assert call(port, "POST", "/api/accounts/remove", {"id": acct["id"]})[1] == {"ok": True}
        assert call(port, "GET", "/api/accounts")[1] == []
    finally: proc.terminate(); proc.wait(5)


def test_backtest_without_history_explains(app):
    r = call(app, "GET", "/api/backtest", timeout=60)[1]          # first call loads pandas; slow on a cold start
    assert "not available" in r["error"]
