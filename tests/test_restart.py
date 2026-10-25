"""Starting Day Read while an older copy is still running replaces the old copy."""
import json, os, shutil, socket, subprocess, sys, time, urllib.request
from pathlib import Path

import pytest

PUB = Path(__file__).resolve().parents[1] / "app"

OLD_APP = '''
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):            # an older Day Read: no version, no quit
    def log_message(self, *a): pass
    def do_GET(self):
        b = json.dumps({"running": False}).encode(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
import sys; ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def state(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as r: return json.loads(r.read())


def wait_up(port):
    for _ in range(60):
        try: socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); return
        except OSError: time.sleep(0.1)


@pytest.mark.skipif(os.name == "nt" or not shutil.which("lsof"), reason="stopping an old process needs lsof (macOS/Linux)")
def test_older_copy_is_replaced(tmp_path):
    port = free_port(); old_dir = tmp_path / "old" / "app"; old_dir.mkdir(parents=True)
    (old_dir / "dayread_app.py").write_text(OLD_APP)                         # same file name the real old app had
    old = subprocess.Popen([sys.executable, str(old_dir / "dayread_app.py"), str(port)]); wait_up(port)
    assert "version" not in state(port)
    env = dict(os.environ, DAYREAD_HOME=str(tmp_path / "h"))
    new = subprocess.Popen([sys.executable, str(PUB / "dayread_app.py"), "--no-browser", "--port", str(port)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(60):
            try:
                if state(port).get("version"): break
            except Exception: pass
            time.sleep(0.2)
        assert state(port)["version"] and old.wait(5) is not None             # the old process has ended
        again = subprocess.run([sys.executable, str(PUB / "dayread_app.py"), "--no-browser", "--port", str(port)], env=env, capture_output=True, text=True, timeout=20)
        assert "already running" in again.stdout                                # same version: just reuse it
    finally:
        new.terminate(); new.wait(5)
        if old.poll() is None: old.kill()
