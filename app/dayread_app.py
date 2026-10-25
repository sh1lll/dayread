#!/usr/bin/env python3
"""Day Read — signals, backtest and accounts in one local app. Mac, Windows and Linux; standard library only.

    python dayread_app.py                  # opens http://127.0.0.1:8765
    python dayread_app.py --full           # also shows the long-form Read tab (owner build)
    python dayread_app.py --lan            # let paired devices in: a phone on this Wi-Fi, or anywhere through Tailscale

Signals are the only thing the default build prints. Orders are never sent unless an account is switched on
in ~/.dayread/accounts.json and set to Confirm or Auto in the app. Paper accounts need nothing.

Only this computer and devices paired from it (Accounts → Devices) can see or control anything. A device
that is not paired gets the pairing screen and nothing else.
"""
import argparse, json, os, re, socket, subprocess, sys, threading, time, urllib.request, webbrowser
from http.cookies import SimpleCookie
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent; DESK = HERE / "desk"
sys.path.insert(0, str(DESK))
import broker, backtest, dayreport, devices                                 # noqa: E402
try: import readfacts                                                       # private: turns the engine's statements into facts
except ImportError: readfacts = None

VERSION = "2026-10-11.5"                                                    # an older copy still running on the port is replaced by a newer one

FULL = False; LAN = False

def project_python() -> str:
    for rel in (".venv/bin/python", ".venv/Scripts/python.exe", "venv/bin/python", "venv/Scripts/python.exe"):
        if (ROOT / rel).exists(): return str(ROOT / rel)
    return sys.executable

RUN = {"proc": None, "kind": "", "label": "", "done": True, "events": [], "signals": {}, "facts": [], "tail": [], "lock": threading.Lock()}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$"); CLOCK = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")

def plans(sig):
    if sig.get("replay"): return []                                         # a test day is for looking at, never for sending
    out = []; md = broker.modes()
    for a in broker.accounts():
        mode = md.get(a["id"], "off")
        if not a.get("enabled") or mode == "off": continue
        qty, rp, why = broker.check(a, sig)
        out.append({"account": a["id"], "name": a["name"], "mode": mode, "qty": qty, "risk_pct": round(rp, 2), "blocked": why})
    return out

def _event(e): RUN["events"].append(e)

def _on_signal(sig):
    RUN["signals"][sig["id"]] = sig; sig["plans"] = plans(sig); _event({"t": "signal", **sig})
    for p in sig["plans"]:
        if p["mode"] == "auto" and not p["blocked"]:
            r = broker.send(p["account"], sig, "auto"); _event({"t": "order", "signal": sig["id"], "account": p["account"], **({"order": r["order"]} if r.get("order") else {}), "ok": r["ok"], "error": r.get("error")})

def start(req):
    with RUN["lock"]:
        if RUN["proc"] and RUN["proc"].poll() is None: return {"error": "a run is already going — stop it first"}
        kind = req.get("kind", "signals")
        if kind == "read" and not FULL: return {"error": "the Read view is not part of this build"}
        py = project_python(); script = DESK / "signals.py" if kind == "signals" else HERE / "dayread.py"; cmd = [py, "-u", str(script)]
        if not script.exists(): return {"error": "No engine is installed. Day Read needs a private engine at desk/signals.py (see the README)."}
        if req.get("mode") == "live": cmd += ["--live"]; label = "Live — today"
        else:
            d, a, b = req.get("date", ""), req.get("from", "09:30"), req.get("to", "16:00")
            if not (DATE.match(d) and CLOCK.match(a) and CLOCK.match(b)): return {"error": "Check the date (YYYY-MM-DD) and the times (HH:MM)."}
            try: step = max(1, min(60, int(req.get("step", 10))))
            except (TypeError, ValueError): return {"error": "The step must be a whole number of seconds."}
            cmd += ["--date", d, "--from", a, "--to", b, "--step", str(step)]; label = f"Replay {d}"
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONWARNINGS="ignore"); kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env, **kw)
        RUN.update(proc=proc, kind=kind, label=label, done=False, events=[], signals={}, facts=[], tail=[])
    threading.Thread(target=_pump, args=(proc, kind), daemon=True).start(); return {"ok": True, "label": label, "kind": kind}

def _pump(proc, kind):
    for ln in proc.stdout:
        ln = ln.rstrip("\n")
        if not ln.strip(): continue
        if kind == "read":
            if "Warning" not in ln: _event({"t": "read", "line": ln})
            continue
        try: o = json.loads(ln)
        except ValueError: RUN["tail"] = (RUN["tail"] + [ln])[-6:]; continue
        if o.get("type") == "signal": _on_signal(o)
        elif o.get("type") == "fact" and o.get("f"):                         # an engine that already speaks in facts
            f = {k: v for k, v in o.items() if k not in ("type", "at")}; RUN["facts"].append({"at": o.get("at"), **f}); _event({"t": "fact", "at": o.get("at"), **f})
        elif o.get("type") == "read" and readfacts:
            if o.get("log"): continue                                       # forward-test notes are not shown
            for f in readfacts.extract(o.get("kind"), o.get("text"), o.get("kw")):                 # numbers only; the engine's wording stays here
                RUN["facts"].append({"at": o.get("t"), **f}); _event({"t": "fact", "at": o.get("t"), **f})
        elif o.get("type") == "error": _event({"t": "error", "msg": o["msg"]})
    proc.wait()
    if proc.returncode not in (0, None, -15): _event({"t": "error", "msg": "The engine stopped unexpectedly." + (" " + RUN["tail"][-1][:160] if RUN["tail"] else "")})
    last = str(RUN["facts"][-1].get("at", "")) if RUN["facts"] else ""
    if kind == "signals" and proc.returncode == 0 and last[11:16] >= "15:45":   # only a day that ran to the close gets a report
        try:
            try: results = {(t["day"], t["side"]): t["r"] for t in backtest._load()}
            except Exception: results = None                                    # no trade history here: the trade is shown without a result
            rep = dayreport.build(RUN["facts"], list(RUN["signals"].values()), results)
            if rep: _event({"t": "report", **rep})
        except Exception as e: _event({"t": "error", "msg": f"The end-of-day report could not be made ({type(e).__name__})."})
    _event({"t": "done"}); RUN["done"] = True

def stop():
    p = RUN["proc"]
    if p and p.poll() is None: p.terminate()
    return {"ok": True}

def state(who):
    return {"paired": True, "version": VERSION, "who": who, "local": who == "this computer", "running": bool(RUN["proc"]) and not RUN["done"], "kind": RUN["kind"], "label": RUN["label"], "full": FULL, "kill": broker.kill_on(), "lan": LAN}

def addresses(port):
    """Where a paired device can reach this computer (LAN address; Tailscale shows its own in its app)."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); out.append(s.getsockname()[0]); s.close()   # no packet is sent
    except OSError: pass
    return [f"http://{a}:{port}" for a in out if not a.startswith("127.")]

COOKIE = "dayread_device"
PROXY_HEADERS = ("X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "X-Real-IP", "CF-Connecting-IP", "Tailscale-User-Login")

def accounts_view():
    md = broker.modes(); day = datetime.now(broker.NY).strftime("%Y-%m-%d"); out = []
    for a in broker.accounts():
        n, used = broker._today_used(a["id"], day); out.append({**broker.public(a), "mode": md.get(a["id"], "off"), "today_trades": n, "today_risk_pct": round(used, 2)})
    return out

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode(); self.send_response(code); self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store"); self.send_header("X-Content-Type-Options", "nosniff"); self.end_headers(); self.wfile.write(b)
    def _local(self):                       # this computer, and not something relayed through a local proxy or tunnel
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1") and not any(self.headers.get(h) for h in PROXY_HEADERS)
    def _host_ok(self):
        if LAN: return True
        h = (self.headers.get("Host") or "").split(":")[0]; return h in ("127.0.0.1", "localhost", "[::1]")
    def _who(self):
        """'this computer', a paired device's name, or None."""
        if self._local(): return "this computer"
        c = SimpleCookie()
        try: c.load(self.headers.get("Cookie") or "")
        except Exception: return None
        d = devices.verify(c[COOKIE].value if COOKIE in c else ""); return d["name"] if d else None
    def do_GET(self):
        if not self._host_ok(): return self._send(403, "{}")
        u = urlparse(self.path); q = parse_qs(u.query); who = self._who()
        if u.path == "/": return self._send(200, (DESK / "ui.html").read_bytes(), "text/html")
        if not who: return self._send(200, json.dumps({"paired": False})) if u.path == "/api/state" else self._send(401, json.dumps({"error": "This device is not paired."}))
        if u.path == "/api/state": return self._send(200, json.dumps(state(who)))
        if u.path == "/api/devices":
            if not self._local(): return self._send(403, json.dumps({"error": "Devices can only be managed on the computer running Day Read."}))
            return self._send(200, json.dumps({"devices": devices.listing(), "addresses": addresses(self.server.server_port) if LAN else [], "lan": LAN}))
        if u.path == "/api/accounts": return self._send(200, json.dumps(accounts_view()))
        if u.path == "/api/orders": return self._send(200, json.dumps(broker.orders(60)))
        if u.path == "/api/backtest":
            try: return self._send(200, json.dumps(backtest.run(q.get("start", [""])[0] or None, q.get("end", [""])[0] or None, float(q.get("risk", ["0.5"])[0]))))
            except Exception as e: return self._send(200, json.dumps({"error": f"The trade history is not available here ({type(e).__name__})."}))
        if u.path == "/api/events":
            i = int(q.get("from", ["0"])[0]) if q.get("from", ["0"])[0].isdigit() else 0; self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store"); self.end_headers(); idle = 0
            try:
                while True:
                    ev = RUN["events"]
                    if i < len(ev): self.wfile.write(f"data: {json.dumps(ev[i])}\n\n".encode()); self.wfile.flush(); i += 1; idle = 0
                    else:
                        time.sleep(0.25); idle += 1
                        if idle % 60 == 0: self.wfile.write(b": keep-alive\n\n"); self.wfile.flush()
                        if idle % 120 == 0 and not self._who(): break                       # device removed while connected
            except (BrokenPipeError, ConnectionResetError, OSError): pass
            return
        self._send(404, "{}")
    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-DayRead") != "1": return self._send(403, "{}")
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(min(n, 65536)) if n else b"{}"
        try: req = json.loads(raw or b"{}")
        except ValueError: req = {}
        if not isinstance(req, dict): req = {}
        if self.path == "/api/pair/redeem":
            key, dev = devices.redeem(req.get("code", ""), req.get("name", ""))
            if not key: return self._send(200, json.dumps({"error": dev}))
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store")
            self.send_header("Set-Cookie", f"{COOKIE}={key}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict"); b = json.dumps({"ok": True, "device": dev}).encode()
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        if self.path in ("/api/accounts/add", "/api/accounts/remove"):
            if not self._local(): return self._send(403, json.dumps({"error": "Accounts can only be added or removed on the computer running Day Read."}))
            try:
                if self.path == "/api/accounts/add": return self._send(200, json.dumps({"ok": True, "account": broker.add_account(req)}))
                broker.remove_account(str(req.get("id", ""))); return self._send(200, json.dumps({"ok": True}))
            except ValueError as e: return self._send(200, json.dumps({"error": str(e)}))
        if self.path in ("/api/pair/new", "/api/pair/cancel", "/api/devices/revoke"):
            if not self._local(): return self._send(403, json.dumps({"error": "Devices can only be managed on the computer running Day Read."}))
            if self.path == "/api/pair/new": return self._send(200, json.dumps(devices.new_code()))
            if self.path == "/api/pair/cancel": devices.cancel_code(); return self._send(200, json.dumps({"ok": True}))
            return self._send(200, json.dumps({"ok": devices.revoke(str(req.get("id", "")))}))
        if not self._who(): return self._send(401, json.dumps({"error": "This device is not paired."}))
        if self.path == "/api/quit":
            if not self._local(): return self._send(403, "{}")
            stop(); threading.Thread(target=self.server.shutdown, daemon=True).start(); return self._send(200, json.dumps({"ok": True}))
        if self.path == "/api/run": return self._send(200, json.dumps(start(req)))
        if self.path == "/api/stop": return self._send(200, json.dumps(stop()))
        if self.path == "/api/kill": broker.set_kill(bool(req.get("on"))); return self._send(200, json.dumps({"ok": True, "kill": broker.kill_on()}))
        if self.path == "/api/mode":
            try: broker.set_mode(req.get("account", ""), req.get("mode", "")); return self._send(200, json.dumps({"ok": True}))
            except ValueError as e: return self._send(200, json.dumps({"error": str(e)}))
        if self.path == "/api/send":
            sig = RUN["signals"].get(req.get("signal", ""))
            if not sig: return self._send(200, json.dumps({"error": "That signal is no longer available."}))
            r = broker.send(req.get("account", ""), sig, "confirm"); _event({"t": "order", "signal": sig["id"], "account": req.get("account"), **({"order": r["order"]} if r.get("order") else {}), "ok": r["ok"], "error": r.get("error")})
            return self._send(200, json.dumps(r))
        self._send(404, "{}")

def _running_version(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as r: return json.loads(r.read()).get("version") or "old"
    except Exception: return None

def _stop_old(port):
    """Ask an older Day Read on this port to quit; if it is too old to understand, stop its process (macOS/Linux)."""
    try: urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", data=b"{}", headers={"X-DayRead": "1"}), timeout=2)
    except Exception: pass
    for _ in range(20):
        if _running_version(port) is None: return
        time.sleep(0.15)
    if os.name != "nt":
        try: pids = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=5).stdout.split()
        except Exception: pids = []
        for pid in pids:
            try:
                cmd = subprocess.run(["ps", "-p", pid, "-o", "command="], capture_output=True, text=True, timeout=5).stdout
                if "dayread_app.py" in cmd: os.kill(int(pid), 15)
            except Exception: pass
    for _ in range(30):
        if _running_version(port) is None: return
        time.sleep(0.2)

def _bind(a):
    """The server, or None when this same version is already running here."""
    addr = ("0.0.0.0" if a.lan else "127.0.0.1", a.port)
    try: return ThreadingHTTPServer(addr, H)
    except OSError: pass
    v = _running_version(a.port)
    if v == VERSION: return None
    if v is not None:
        print("An older Day Read was still running — replacing it with this version…"); _stop_old(a.port)
    for _ in range(10):
        try: return ThreadingHTTPServer(addr, H)
        except OSError: time.sleep(0.3)
    print(f"Port {a.port} is busy and I could not free it. Quit the other Day Read (close its Terminal window) and start again."); sys.exit(1)

def main():
    global FULL, LAN
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765); ap.add_argument("--lan", action="store_true", help="let paired devices reach it (this Wi-Fi, or anywhere through Tailscale)")
    ap.add_argument("--no-browser", action="store_true"); ap.add_argument("--full", action="store_true")
    a = ap.parse_args(); FULL, LAN = a.full, a.lan; broker.accounts()
    srv = _bind(a)
    if srv is None:
        print(f"Day Read is already running at http://127.0.0.1:{a.port}")
        if not a.no_browser: webbrowser.open(f"http://127.0.0.1:{a.port}")
        return
    srv.daemon_threads = True
    print(f"Day Read is running at http://127.0.0.1:{a.port}   (Ctrl+C to quit)")
    if a.lan: print("Paired devices can reach it at " + (", ".join(addresses(a.port)) or "this computer's network address") + ". Pair one in Accounts → Devices; anything not paired sees only the pairing screen.")
    if not a.no_browser: threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{a.port}")).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally: stop(); srv.server_close()

if __name__ == "__main__":
    main()
