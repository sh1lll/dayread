"""Paired devices: phones or other computers allowed to use Day Read from somewhere other than this computer.

Pairing: this computer shows a one-time code (8 characters, 5 minutes, 5 tries). A device that enters it
gets a long random key, kept in that device's browser; only a hash of the key is stored here.
"""
import hashlib, json, os, secrets, threading
from datetime import datetime, timezone

import broker

DEVICES = broker.HOME / "devices.json"
CODE_TTL_S, CODE_TRIES = 300, 5
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"                     # no 0/O or 1/I to misread
_LOCK = threading.Lock(); _PENDING = {"code": None, "until": 0.0, "tries": 0}
_now = lambda: datetime.now(timezone.utc)

def _hash(key): return hashlib.sha256(key.encode()).hexdigest()
def _load():
    try: return json.loads(DEVICES.read_text()).get("devices", [])
    except (FileNotFoundError, ValueError): return []
def _save(devs):
    DEVICES.parent.mkdir(parents=True, exist_ok=True); tmp = DEVICES.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f: json.dump({"devices": devs}, f, indent=2)
    os.replace(tmp, DEVICES)

def new_code():
    """Start pairing; any earlier unused code stops working."""
    with _LOCK:
        code = "".join(secrets.choice(ALPHABET) for _ in range(8))
        _PENDING.update(code=code, until=_now().timestamp() + CODE_TTL_S, tries=0)
        return {"code": code[:4] + "-" + code[4:], "expires_in": CODE_TTL_S}

def cancel_code():
    with _LOCK: _PENDING.update(code=None, until=0.0, tries=0)

def redeem(code, name):
    """Returns (key, device) for the right code, else (None, reason)."""
    code = "".join(ch for ch in str(code).upper() if ch.isalnum())
    with _LOCK:
        if not _PENDING["code"] or _now().timestamp() > _PENDING["until"]:
            return None, "No pairing code is active. Press Pair a device on the computer running Day Read."
        if not secrets.compare_digest(code, _PENDING["code"]):
            _PENDING["tries"] += 1
            if _PENDING["tries"] >= CODE_TRIES: _PENDING.update(code=None, until=0.0, tries=0); return None, "Too many wrong codes. Start pairing again on the computer."
            return None, "That code is not right."
        _PENDING.update(code=None, until=0.0, tries=0)
        key = secrets.token_urlsafe(32); stamp = _now().isoformat(timespec="seconds")
        dev = {"id": secrets.token_hex(4), "name": (str(name).strip() or "Device")[:40], "hash": _hash(key), "paired": stamp, "seen": stamp}
        devs = _load(); devs.append(dev); _save(devs)
        return key, public(dev)

def verify(key):
    """The paired device for this key, or None."""
    if not key: return None
    h = _hash(key)
    with _LOCK:
        devs = _load(); dev = next((d for d in devs if secrets.compare_digest(d["hash"], h)), None)
        if not dev: return None
        stamp = _now().isoformat(timespec="seconds")
        if dev["seen"][:16] != stamp[:16]: dev["seen"] = stamp; _save(devs)      # at most one write a minute
        return public(dev)

def public(d): return {k: d[k] for k in ("id", "name", "paired", "seen")}
def listing(): return [public(d) for d in _load()]

def revoke(dev_id):
    with _LOCK:
        devs = _load(); keep = [d for d in devs if d["id"] != dev_id]
        if len(keep) == len(devs): return False
        _save(keep); return True
