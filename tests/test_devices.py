"""Device pairing (desk/devices.py) and who may use the app (dayread_app.py)."""
import http.client, importlib, json, os, shutil, socket, stat, subprocess, sys, textwrap, time
from pathlib import Path

import pytest

PUB = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(PUB / "desk"))


# --- devices.py ----------------------------------------------------------------

@pytest.fixture
def dev(tmp_path, monkeypatch):
    monkeypatch.setenv("DAYREAD_HOME", str(tmp_path / "home"))
    import broker, devices
    importlib.reload(broker); return importlib.reload(devices)


def test_pair_verify_revoke(dev):
    code = dev.new_code()["code"]
    assert len(code) == 9 and code[4] == "-"
    key, d = dev.redeem(code.lower().replace("-", " "), "My iPhone")    # case, spaces and dash don't matter
    assert key and d["name"] == "My iPhone"
    assert stat.S_IMODE(os.stat(dev.DEVICES).st_mode) == 0o600
    assert key not in dev.DEVICES.read_text()                            # only a hash is stored
    assert dev.verify(key)["id"] == d["id"]
    assert dev.verify(key + "x") is None and dev.verify("") is None
    assert dev.revoke(d["id"]) and dev.verify(key) is None


def test_code_is_single_use(dev):
    code = dev.new_code()["code"]
    assert dev.redeem(code, "a")[0]
    assert dev.redeem(code, "b")[0] is None


def test_code_expires(dev, monkeypatch):
    code = dev.new_code()["code"]
    later = dev._now().timestamp() + dev.CODE_TTL_S + 1
    monkeypatch.setattr(dev, "_now", lambda: type("T", (), {"timestamp": lambda self: later})())
    key, why = dev.redeem(code, "x")
    assert key is None and "No pairing code" in why


def test_five_wrong_tries_burn_the_code(dev):
    code = dev.new_code()["code"]
    for _ in range(4): assert dev.redeem("AAAA-AAAA", "x")[1] == "That code is not right."
    assert "Too many" in dev.redeem("AAAA-AAAA", "x")[1]
    assert dev.redeem(code, "x")[0] is None                              # right code no longer works


def test_new_code_replaces_old(dev):
    old = dev.new_code()["code"]; new = dev.new_code()["code"]
    if old != new: assert dev.redeem(old, "x")[0] is None
    assert dev.redeem(new, "x")[0]


# --- the app, reached from "another device" ---------------------------------------

STUB = textwrap.dedent('''
    import json, sys
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    print(json.dumps({"type": "signal", "id": "T1", "sym": "NQ", "side": "LONG", "entry": 25000.0, "stop": 24980.0,
                      "target": 25060.0, "risk_pts": 20.0, "ts": now.isoformat(), "until": (now + timedelta(minutes=5)).isoformat(),
                      "replay": "--date" in sys.argv}), flush=True)
''')


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except OSError: return None
    finally: s.close()


@pytest.fixture
def app(tmp_path):
    ip = lan_ip()
    if not ip or ip.startswith("127."): pytest.skip("no non-loopback address to act as another device")
    root = tmp_path / "proj"; pub = root / "app"
    shutil.copytree(PUB, pub, ignore=shutil.ignore_patterns("__pycache__"))
    (pub / "desk" / "signals.py").write_text(STUB)
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    proc = subprocess.Popen([sys.executable, str(pub / "dayread_app.py"), "--no-browser", "--lan", "--port", str(port)],
                            env=dict(os.environ, DAYREAD_HOME=str(tmp_path / "home")), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try: socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
        except OSError: time.sleep(0.1)
    a = {"port": port, "ip": ip}
    assert req(a, "POST", "/api/accounts/add", {"type": "paper", "name": "Paper", "equity": 50000, "risk_pct": 0.5, "symbol": "MNQ", "max_qty": 10, "max_trades_day": 3, "max_day_risk_pct": 1.5}, remote=False)[1]["account"]["id"] == "paper"
    yield a
    proc.terminate(); proc.wait(5)


def req(app, method, path, body=None, remote=True, cookie=None, extra=None):
    host = app["ip"] if remote else "127.0.0.1"
    c = http.client.HTTPConnection(host, app["port"], timeout=5)
    h = {"X-DayRead": "1", "Host": f"{host}:{app['port']}"}
    if cookie: h["Cookie"] = cookie
    h.update(extra or {})
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse(); data = r.read(); setc = r.getheader("Set-Cookie"); c.close()
    try: data = json.loads(data)
    except ValueError: pass
    return r.status, data, setc


def test_unpaired_device_sees_only_the_pairing_screen(app):
    assert req(app, "GET", "/")[0] == 200
    assert req(app, "GET", "/api/state")[1] == {"paired": False}
    for path in ("/api/accounts", "/api/orders", "/api/backtest", "/api/events", "/api/devices"):
        assert req(app, "GET", path)[0] == 401, path
    for path in ("/api/run", "/api/stop", "/api/kill", "/api/mode", "/api/send"):
        assert req(app, "POST", path, {})[0] == 401, path
    assert req(app, "POST", "/api/pair/new", {})[0] == 403                # cannot start pairing itself
    assert req(app, "POST", "/api/accounts/add", {})[0] == 403            # nor add accounts


def test_paired_device_can_do_everything_until_removed(app):
    code = req(app, "POST", "/api/pair/new", {}, remote=False)[1]["code"]
    st, body, setc = req(app, "POST", "/api/pair/redeem", {"code": code, "name": "Phone"})
    assert body["ok"] and "HttpOnly" in setc and "SameSite=Strict" in setc
    cookie = setc.split(";")[0]
    assert req(app, "GET", "/api/state", cookie=cookie)[1]["who"] == "Phone"
    assert req(app, "POST", "/api/mode", {"account": "paper", "mode": "confirm"}, cookie=cookie)[1] == {"ok": True}
    assert req(app, "POST", "/api/run", {"mode": "live"}, cookie=cookie)[1]["ok"]
    time.sleep(1.5)
    r = req(app, "POST", "/api/send", {"signal": "T1", "account": "paper"}, cookie=cookie)[1]
    assert r["ok"] and r["order"]["status"] == "filled"
    assert req(app, "GET", "/api/devices", cookie=cookie)[0] == 403         # managing devices stays on the computer
    devs = req(app, "GET", "/api/devices", remote=False)[1]["devices"]
    assert [d["name"] for d in devs] == ["Phone"]
    assert req(app, "POST", "/api/devices/revoke", {"id": devs[0]["id"]}, remote=False)[1] == {"ok": True}
    assert req(app, "GET", "/api/accounts", cookie=cookie)[0] == 401


def test_wrong_codes_and_forged_keys_fail(app):
    req(app, "POST", "/api/pair/new", {}, remote=False)
    assert req(app, "POST", "/api/pair/redeem", {"code": "ZZZZ-ZZZZ", "name": "x"})[1]["error"] == "That code is not right."
    assert req(app, "GET", "/api/accounts", cookie="dayread_device=forged")[0] == 401


def test_relayed_requests_from_this_computer_need_pairing(app):
    """A tunnel or proxy running on this computer must not turn the internet into 'this computer'."""
    assert req(app, "GET", "/api/accounts", remote=False)[0] == 200
    for h in ({"X-Forwarded-For": "203.0.113.9"}, {"CF-Connecting-IP": "203.0.113.9"}, {"Tailscale-User-Login": "x@example.com"}):
        assert req(app, "GET", "/api/accounts", remote=False, extra=h)[0] == 401, h
