#!/usr/bin/env python3
"""
Family Finance - zero-dependency local server.

Usage:
    python server.py [--port 8765] [--data-dir ./data] [--no-browser]

Everything personal lives in the data directory (finances.json + files/).
The app code contains no personal information - share the folder freely,
just keep (or delete) your own data directory.
"""
import argparse
import json
import os
import re
import shutil
import socket
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(APP_DIR, "public")

# ── port configuration ────────────────────────────────────────────────────────
PORTS_FILE_NAME  = "ports.json"
REGISTRY_KEY     = "family-finance-app"
DEFAULT_PORT     = 8765
MAX_PORT_TRIES   = 50
MAX_SEARCH_DEPTH = 3

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

EMPTY_DATA = {
    "schemaVersion": 1,
    "settings": {
        "appName": "Family Finance",
        "currency": "INR",
        "locale": "en-IN",
        "persons": [],
        "locations": [],
        "lastUpdated": None,
    },
    "income": {"persons": []},
    "expenses": [],
    "monthlyInvestments": [],
    "portfolio": [],
    "gold": [],
    "loans": [],
    "goals": [],
    "cards": [],
    "documents": [],
}

DATA_DIR = None  # set in main()


def data_file():
    return os.path.join(DATA_DIR, "finances.json")


def files_dir():
    return os.path.join(DATA_DIR, "files")


def backups_dir():
    return os.path.join(DATA_DIR, "backups")


def ensure_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(files_dir(), exist_ok=True)
    os.makedirs(backups_dir(), exist_ok=True)
    if not os.path.exists(data_file()):
        with open(data_file(), "w", encoding="utf-8") as f:
            json.dump(EMPTY_DATA, f, indent=2)
        print("  Created new empty data file: " + data_file())


AUTO_BAK_RE = re.compile(r"^finances-\d{4}-\d{2}-\d{2}\.json$")  # daily auto backups (rotated)
BAK_NAME_RE = re.compile(r"^finances-[\w.\-]+\.json$")           # any valid backup file name


def rotate_auto_backups(keep=14):
    """Prune only automatic daily backups; manual/pre-restore ones are kept."""
    autos = sorted(n for n in os.listdir(backups_dir()) if AUTO_BAK_RE.match(n))
    for old in autos[:-keep]:
        os.remove(os.path.join(backups_dir(), old))


def make_backup(kind=None):
    """Snapshot the current data file into backups/. kind=None -> daily auto."""
    if not os.path.exists(data_file()):
        return None
    if kind:
        name = "finances-%s-%s.json" % (kind, datetime.now().strftime("%Y-%m-%d-%H%M%S"))
    else:
        name = "finances-%s.json" % datetime.now().strftime("%Y-%m-%d")
        if os.path.exists(os.path.join(backups_dir(), name)):
            return None  # today's auto backup already exists
    shutil.copy2(data_file(), os.path.join(backups_dir(), name))
    rotate_auto_backups()
    return name


def safe_name(name):
    """Sanitize a filename: strip paths, keep readable chars."""
    name = os.path.basename(unquote(name or ""))
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name).strip()
    return name or "file"


class Handler(BaseHTTPRequestHandler):
    server_version = "FamilyFinance/1.0"

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg):
        self._send(code, {"error": msg})

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 200 * 1024 * 1024:
            raise ValueError("Payload too large (max 200 MB)")
        return self.rfile.read(length)

    def log_message(self, fmt, *args):
        pass  # keep the console quiet

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/data":
            with open(data_file(), "r", encoding="utf-8") as f:
                self._send(200, f.read())
        elif path == "/api/files":
            items = []
            for n in sorted(os.listdir(files_dir())):
                p = os.path.join(files_dir(), n)
                if os.path.isfile(p):
                    items.append({"name": n, "size": os.path.getsize(p),
                                  "modified": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")})
            self._send(200, items)
        elif path == "/api/backups":
            items = []
            for n in sorted(os.listdir(backups_dir()), reverse=True):
                p = os.path.join(backups_dir(), n)
                if os.path.isfile(p) and BAK_NAME_RE.match(n):
                    items.append({"name": n, "size": os.path.getsize(p),
                                  "modified": datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec="seconds")})
            self._send(200, items)
        elif path.startswith("/backups/"):
            n = safe_name(path[len("/backups/"):])
            if not BAK_NAME_RE.match(n):
                return self._err(400, "Invalid backup name")
            self._serve_file(os.path.join(backups_dir(), n), download=True)
        elif path.startswith("/files/"):
            self._serve_file(os.path.join(files_dir(), safe_name(path[len("/files/"):])), download=True)
        else:
            if path == "/":
                path = "/index.html"
            fp = os.path.normpath(os.path.join(PUBLIC_DIR, path.lstrip("/")))
            if not fp.startswith(PUBLIC_DIR):
                return self._err(403, "Forbidden")
            self._serve_file(fp)

    def _serve_file(self, fp, download=False):
        if not os.path.isfile(fp):
            return self._err(404, "Not found")
        ext = os.path.splitext(fp)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        extra = {}
        if download and ctype == "application/octet-stream":
            extra["Content-Disposition"] = 'inline; filename="%s"' % os.path.basename(fp)
        with open(fp, "rb") as f:
            body = f.read()
        self._send(200, body, ctype, extra)

    def do_PUT(self):
        path = urlparse(self.path).path
        if path != "/api/data":
            return self._err(404, "Not found")
        try:
            data = json.loads(self._body().decode("utf-8"))
        except Exception as e:
            return self._err(400, "Invalid JSON: %s" % e)
        data.setdefault("settings", {})["lastUpdated"] = datetime.now().isoformat(timespec="seconds")
        make_backup()  # automatic rotating daily backup (first save of the day)
        # atomic write
        tmp = data_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, data_file())
        self._send(200, {"ok": True, "lastUpdated": data["settings"]["lastUpdated"]})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/backup":
            name = make_backup("manual")
            if not name:
                return self._err(400, "No data file to back up yet")
            return self._send(200, {"ok": True, "name": name})
        if parsed.path == "/api/restore":
            name = safe_name((parse_qs(parsed.query).get("name") or [""])[0])
            src = os.path.join(backups_dir(), name)
            if not BAK_NAME_RE.match(name) or not os.path.isfile(src):
                return self._err(404, "Backup not found")
            try:
                with open(src, "r", encoding="utf-8") as f:
                    json.load(f)                     # refuse to restore corrupt JSON
            except Exception as e:
                return self._err(400, "Backup file is not valid JSON: %s" % e)
            make_backup("prerestore")                # safety snapshot of current data
            shutil.copy2(src, data_file())
            return self._send(200, {"ok": True, "restored": name})
        if parsed.path != "/api/upload":
            return self._err(404, "Not found")
        q = parse_qs(parsed.query)
        name = safe_name((q.get("name") or [""])[0] or self.headers.get("X-Filename", "file"))
        body = self._body()
        if not body:
            return self._err(400, "Empty upload")
        base, ext = os.path.splitext(name)
        final = name
        i = 1
        while os.path.exists(os.path.join(files_dir(), final)):
            final = "%s-%d%s" % (base, i, ext)
            i += 1
        with open(os.path.join(files_dir(), final), "wb") as f:
            f.write(body)
        self._send(200, {"ok": True, "name": final, "size": len(body)})

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/backups/"):
            n = safe_name(path[len("/api/backups/"):])
            fp = os.path.join(backups_dir(), n)
            if not BAK_NAME_RE.match(n) or not os.path.isfile(fp):
                return self._err(404, "Backup not found")
            os.remove(fp)
            return self._send(200, {"ok": True})
        if not path.startswith("/api/files/"):
            return self._err(404, "Not found")
        fp = os.path.join(files_dir(), safe_name(path[len("/api/files/"):]))
        if not os.path.isfile(fp):
            return self._err(404, "File not found")
        os.remove(fp)
        self._send(200, {"ok": True})


class FFServer(ThreadingHTTPServer):
    # Do NOT reuse addresses: on Windows, SO_REUSEADDR lets two servers bind the
    # same port silently, so you'd land on another app's page. Exclusive binding
    # makes a real conflict error we can catch and step past.
    allow_reuse_address = False


def port_in_use(port):
    """True if something is already accepting connections on this port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def _find_ports_file(start_dir):
    d = os.path.abspath(start_dir)
    for _ in range(MAX_SEARCH_DEPTH):
        candidate = os.path.join(d, PORTS_FILE_NAME)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None

def get_registered_port():
    pf = _find_ports_file(APP_DIR)
    if pf:
        try:
            with open(pf) as f:
                return json.load(f)["registry"][REGISTRY_KEY]["port"]
        except Exception:
            pass
    return DEFAULT_PORT


def main():
    global DATA_DIR
    ap = argparse.ArgumentParser(description="Family Finance local server")
    ap.add_argument("--port", type=int, default=get_registered_port())
    ap.add_argument("--data-dir", default=os.path.join(APP_DIR, "data"))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    DATA_DIR = os.path.abspath(args.data_dir)
    ensure_data()

    port = args.port
    httpd = None
    for _ in range(MAX_PORT_TRIES):
        if port_in_use(port):
            print("  Port %d is busy — trying %d" % (port, port + 1))
            port += 1
            continue
        try:
            httpd = FFServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    if httpd is None:
        print("Could not find a free port (tried %d-%d)." % (args.port, port))
        sys.exit(1)

    url = "http://127.0.0.1:%d" % port
    print("=" * 52)
    print("  Family Finance")
    print("  App:  " + url)
    print("  Data: " + data_file())
    print("  Press Ctrl+C to stop.")
    print("=" * 52)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
