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
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(APP_DIR, "public")

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
        print(f"  Created new empty data file: {data_file()}")


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
        # rotating daily backup (keep last 14)
        stamp = datetime.now().strftime("%Y-%m-%d")
        bak = os.path.join(backups_dir(), "finances-%s.json" % stamp)
        if os.path.exists(data_file()) and not os.path.exists(bak):
            shutil.copy2(data_file(), bak)
            baks = sorted(os.listdir(backups_dir()))
            for old in baks[:-14]:
                os.remove(os.path.join(backups_dir(), old))
        # atomic write
        tmp = data_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, data_file())
        self._send(200, {"ok": True, "lastUpdated": data["settings"]["lastUpdated"]})

    def do_POST(self):
        parsed = urlparse(self.path)
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
        if not path.startswith("/api/files/"):
            return self._err(404, "Not found")
        fp = os.path.join(files_dir(), safe_name(path[len("/api/files/"):]))
        if not os.path.isfile(fp):
            return self._err(404, "File not found")
        os.remove(fp)
        self._send(200, {"ok": True})


def main():
    global DATA_DIR
    ap = argparse.ArgumentParser(description="Family Finance local server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", default=os.path.join(APP_DIR, "data"))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    DATA_DIR = os.path.abspath(args.data_dir)
    ensure_data()

    port = args.port
    httpd = None
    for _ in range(20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
    if httpd is None:
        print("Could not find a free port.")
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
