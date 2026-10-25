"""Accounts, risk guards and order sending. Nothing here sends a live order unless the owner has turned it on in the accounts file."""
import json, math, os, threading, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path(os.environ.get("DAYREAD_HOME", Path.home() / ".dayread")); NY = ZoneInfo("America/New_York")
ACCOUNTS, SETTINGS, ORDERS, KILL = HOME / "accounts.json", HOME / "settings.json", HOME / "orders.jsonl", HOME / "KILL"
HARD_CAP_RISK_PCT = 1.0                                              # no account can ask for more than this per trade
_SEND = threading.Lock()                                             # one order decision at a time: Auto and a manual send cannot race past the limits
TEMPLATE = {"accounts": [
    {"id": "paper", "name": "Paper account", "type": "paper", "enabled": True, "equity": 50000, "risk_pct": 0.5, "point_value": 2, "max_qty": 10, "max_trades_day": 3, "max_day_risk_pct": 1.5, "symbol": "MNQ"},
    {"id": "bridge", "name": "Webhook bridge (example — off)", "type": "webhook", "enabled": False, "live": False, "equity": 25000, "risk_pct": 0.25, "point_value": 2, "max_qty": 5, "max_trades_day": 2, "max_day_risk_pct": 0.5,
     "symbol": "MNQ", "url": "https://example.invalid/hook", "headers": {"Content-Type": "application/json"},
     "payload": "{\"ticker\":\"{symbol}\",\"action\":\"{side_lc}\",\"orderType\":\"market\",\"quantity\":{qty},\"stopLoss\":{\"stopPrice\":{stop}},\"takeProfit\":{\"limitPrice\":{target}},\"signalId\":\"{id}\"}"}]}

PAYLOAD = TEMPLATE["accounts"][1]["payload"]                          # default webhook body; TEMPLATE itself is no longer written for new users
CONTRACTS = {"MNQ": 2.0, "NQ": 20.0}                                 # dollars per index point

def _write_accounts(accts):
    HOME.mkdir(parents=True, exist_ok=True); tmp = ACCOUNTS.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump({"accounts": accts}, f, indent=2)
    os.replace(tmp, ACCOUNTS)

def _ensure():
    HOME.mkdir(parents=True, exist_ok=True)
    if not ACCOUNTS.exists(): _write_accounts([])                    # new users start with no accounts and add their own
    if not SETTINGS.exists(): SETTINGS.write_text(json.dumps({"modes": {}}))

def _is_old_example(a):                                              # accounts older versions wrote by themselves: the placeholder bridge, and the untouched paper account
    if a.get("type") == "webhook" and "example.invalid" in str(a.get("url", "")) and not a.get("enabled"): return True
    return a == TEMPLATE["accounts"][0]

def accounts():
    _ensure(); accts = json.loads(ACCOUNTS.read_text()).get("accounts", [])
    if any(_is_old_example(a) for a in accts): accts = [a for a in accts if not _is_old_example(a)]; _write_accounts(accts)
    return accts

def _num(d, k, lo, hi, label):
    try: v = float(d.get(k))
    except (TypeError, ValueError): raise ValueError(f"{label} must be a number")
    if not (lo <= v <= hi): raise ValueError(f"{label} must be between {lo:g} and {hi:g}")
    return v

def add_account(d):
    """Validate and save a new account; returns its public view. Raises ValueError with a plain message."""
    kind = d.get("type")
    if kind not in ("paper", "webhook"): raise ValueError("choose Practice or Webhook")
    name = str(d.get("name", "")).strip()[:40]
    if not name: raise ValueError("give the account a name")
    contract = str(d.get("symbol", "MNQ")).upper()
    if contract not in CONTRACTS: raise ValueError("contract must be MNQ or NQ")
    a = {"id": "", "name": name, "type": kind, "enabled": True, "equity": _num(d, "equity", 100, 1e8, "Account size"),
         "risk_pct": _num(d, "risk_pct", 0.01, HARD_CAP_RISK_PCT, "Risk per trade"), "point_value": CONTRACTS[contract], "symbol": contract,
         "max_qty": int(_num(d, "max_qty", 1, 100, "Most contracts")), "max_trades_day": int(_num(d, "max_trades_day", 1, 20, "Trades a day")),
         "max_day_risk_pct": _num(d, "max_day_risk_pct", 0.01, 5, "Day risk budget")}
    if kind == "webhook":
        url = str(d.get("url", "")).strip()
        if not url.startswith(("https://", "http://")): raise ValueError("the webhook address must start with https://")
        a.update(url=url, live=bool(d.get("live")), payload=str(d.get("payload") or PAYLOAD), headers={"Content-Type": "application/json"})
        if str(d.get("auth", "")).strip(): a["headers"]["Authorization"] = str(d["auth"]).strip()
    accts = accounts(); base = "".join(c for c in name.lower() if c.isalnum())[:16] or "account"; a["id"] = base; n = 2
    while any(x["id"] == a["id"] for x in accts): a["id"] = f"{base}{n}"; n += 1
    accts.append(a); _write_accounts(accts); return public(a)

def remove_account(acct_id):
    accts = accounts(); keep = [a for a in accts if a["id"] != acct_id]
    if len(keep) == len(accts): raise ValueError("unknown account")
    _write_accounts(keep); m = modes(); m.pop(acct_id, None); SETTINGS.write_text(json.dumps({"modes": m}))
def modes(): _ensure(); return json.loads(SETTINGS.read_text()).get("modes", {})
def set_mode(acct_id, mode):
    if mode not in ("off", "confirm", "auto"): raise ValueError("mode must be off, confirm or auto")
    m = modes(); acct = next((a for a in accounts() if a["id"] == acct_id), None)
    if not acct: raise ValueError("unknown account")
    if mode != "off" and not acct.get("enabled"): raise ValueError("this account is disabled in the accounts file")
    m[acct_id] = mode; SETTINGS.write_text(json.dumps({"modes": m}))
def kill_on(): return KILL.exists()
def set_kill(on):
    _ensure()
    if on: KILL.write_text(datetime.now(timezone.utc).isoformat())
    elif KILL.exists(): KILL.unlink()

def public(a):
    host = ""
    if a.get("url"): host = a["url"].split("//")[-1].split("/")[0]
    return {"id": a["id"], "name": a["name"], "type": a["type"], "enabled": bool(a.get("enabled")), "live": bool(a.get("live")) or a["type"] == "paper", "equity": a.get("equity"),
            "risk_pct": a.get("risk_pct"), "max_qty": a.get("max_qty"), "max_trades_day": a.get("max_trades_day"), "max_day_risk_pct": a.get("max_day_risk_pct"), "symbol": a.get("symbol"), "host": host}

def orders(limit=50):
    if not ORDERS.exists(): return []
    rows = [json.loads(l) for l in ORDERS.read_text().splitlines() if l.strip()]; return rows[-limit:][::-1]

def _already_sent(acct_id, sig_id):
    return any(o["account"] == acct_id and o["signal"] == sig_id and o["status"] in ("sent", "filled") for o in orders(500))

def _today_used(acct_id, day):
    n, risk = 0, 0.0
    for o in orders(500):
        if o["account"] == acct_id and o["status"] in ("sent", "filled") and o["day"] == day: n += 1; risk += o.get("risk_pct", 0)
    return n, risk

def size(acct, sig):
    risk_usd = acct["equity"] * min(float(acct["risk_pct"]), HARD_CAP_RISK_PCT) / 100.0; per = sig["risk_pts"] * float(acct["point_value"])
    qty = math.floor(risk_usd / per) if per > 0 else 0
    return max(0, min(qty, int(acct.get("max_qty", 1)))), (per * max(0, min(qty, int(acct.get("max_qty", 1)))) / acct["equity"] * 100.0)

def check(acct, sig, now=None):
    """Returns (qty, risk_pct, None) or (0, 0, reason). Every guard is a hard stop."""
    now = now or datetime.now(NY)
    if kill_on(): return 0, 0, "kill switch is on"
    if not acct.get("enabled"): return 0, 0, "account is disabled in the accounts file"
    if acct["type"] != "paper" and not acct.get("live"): return 0, 0, "live sending is off for this account (set \"live\": true in the accounts file)"
    if sig.get("replay"): return 0, 0, "this is a test day — nothing is sent"
    until = datetime.fromisoformat(sig["until"]); until = until if until.tzinfo else until.replace(tzinfo=NY)   # engine times without a zone are New York time
    if not sig.get("replay") and now > until: return 0, 0, "signal has expired"
    if _already_sent(acct["id"], sig["id"]): return 0, 0, "this signal was already sent to this account"
    qty, rp = size(acct, sig)
    if qty < 1: return 0, 0, "the stop is too wide for this account's risk limit"
    n, used = _today_used(acct["id"], sig["ts"][:10])
    if n >= int(acct.get("max_trades_day", 1)): return 0, 0, "daily trade limit reached"
    if used + rp > float(acct.get("max_day_risk_pct", 1.0)) + 1e-9: return 0, 0, "daily risk budget reached"
    return qty, rp, None

def _post(acct, order):
    body = acct.get("payload") or json.dumps({"id": "{id}"})
    for k, v in {"{symbol}": acct.get("symbol", "NQ"), "{side_lc}": "buy" if order["side"] == "LONG" else "sell", "{side}": order["side"], "{qty}": order["qty"], "{entry}": order["entry"],
                 "{stop}": order["stop"], "{target}": order["target"], "{id}": order["signal"]}.items(): body = body.replace(k, str(v))
    req = urllib.request.Request(acct["url"], data=body.encode(), headers=acct.get("headers", {"Content-Type": "application/json"}), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=8) as r: return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e: return False, f"HTTP {e.code}"
    except Exception as e: return False, type(e).__name__

def send(acct_id, sig, source="confirm"):
    with _SEND: return _send(acct_id, sig, source)

def _send(acct_id, sig, source):
    acct = next((a for a in accounts() if a["id"] == acct_id), None)
    if not acct: return {"ok": False, "error": "unknown account"}
    qty, rp, why = check(acct, sig)
    if why: return {"ok": False, "error": why}
    order = {"ts": datetime.now(timezone.utc).isoformat(), "day": sig["ts"][:10], "account": acct_id, "signal": sig["id"], "sym": acct.get("symbol", sig["sym"]), "side": sig["side"], "qty": qty, "entry": sig["entry"],
             "stop": sig["stop"], "target": sig["target"], "risk_pct": round(rp, 3), "source": source, "simulated": acct["type"] == "paper"}
    if acct["type"] == "paper": order["status"] = "filled"; detail = "simulated fill at the signal price"
    elif acct["type"] == "webhook":
        ok, detail = _post(acct, order); order["status"] = "sent" if ok else "failed"
    else: return {"ok": False, "error": "unsupported account type"}
    order["detail"] = detail; ORDERS.parent.mkdir(parents=True, exist_ok=True)
    with ORDERS.open("a") as f: f.write(json.dumps(order) + "\n")
    return {"ok": order["status"] in ("filled", "sent"), "order": order, "error": None if order["status"] != "failed" else detail}
