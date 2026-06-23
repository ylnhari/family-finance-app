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
import base64
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import urllib.request
import urllib.error
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(APP_DIR, "public")
SAMPLE_FILE = os.path.join(APP_DIR, "samples", "demo-finances.json")  # committed demo dataset
DEFAULT_DATA_DIR = os.path.join(APP_DIR, "data")
DEMO_DATA_DIR = os.path.join(APP_DIR, "demo-data")  # throwaway sandbox for --demo (gitignored)

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

# ── Gemini AI extraction ───────────────────────────────────────────────────────
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_GEN_URL  = GEMINI_BASE_URL + "/models/{model}:generateContent?key={key}"
GEMINI_LIST_URL = GEMINI_BASE_URL + "/models?key={key}&pageSize=100"

# Ordered by preference: fastest/lightest first — each has separate quota buckets
GEMINI_PREFERRED = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-2.0-flash-exp",
    "gemini-2.5-pro",
    "gemini-pro-latest",
]

PAYSLIP_MIME = {
    ".pdf": "application/pdf", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
}

EXTRACTION_PROMPT = (
    "You are a payslip and compensation document parser for Indian salary documents.\n"
    "Return ONLY valid JSON (no markdown, no code blocks, no explanation):\n"
    "{\n"
    '  "year": <fiscal year as integer e.g. 2025>,\n'
    '  "components": [{"component":"<name>","amount":<monthly_INR_number>,"scope":"<gross|ctc>"}],\n'
    '  "deductions":  [{"component":"<name>","amount":<monthly_INR_number>}],\n'
    '  "variablePctEligible": <percentage number or null>,\n'
    '  "oneTimeBonus": <annual amount number or null>\n'
    "}\n"
    "Rules:\n"
    "- All amounts must be monthly in INR (divide annual/yearly figures by 12)\n"
    "- scope='gross' for components paid in take-home: Basic, HRA, Special Allowance, LTA, Bonus, Flexible Pay\n"
    "- scope='ctc' for employer-only costs not received in hand: Employer PF, Employer NPS, Gratuity\n"
    "- variablePctEligible: extract only if document mentions variable/performance pay as a % of CTC\n"
    "- oneTimeBonus: joining bonus, retention bonus, or similar one-time annual payments\n"
    "- If a field is not found in the document, use null\n"
    "- Return ONLY the JSON object, nothing else"
)

_gemini_available_models = None  # cached after first successful list call


# ── cross-process file lock ─────────────────────────────────────────────────────
try:
    import fcntl
    def _os_lock(fh):   fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    def _os_unlock(fh): fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
except ImportError:
    import msvcrt
    def _os_lock(fh):   fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    def _os_unlock(fh): fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


class file_lock:
    """Exclusive cross-process (and cross-thread) lock held via a `.lock` sidecar.
    While held, no other process or instance can edit the guarded file."""
    def __init__(self, target, timeout=10.0):
        self.path = str(target) + ".lock"
        self.timeout = timeout
        self._fh = None

    def __enter__(self):
        self._fh = open(self.path, "a+")
        deadline = time.time() + self.timeout
        while True:
            try:
                _os_lock(self._fh)
                return self
            except OSError:
                if time.time() >= deadline:
                    self._fh.close(); self._fh = None
                    raise TimeoutError("could not acquire lock: " + self.path)
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            _os_unlock(self._fh)
        finally:
            self._fh.close(); self._fh = None


def _list_gemini_models():
    """Return set of model IDs that support generateContent. Cached per process."""
    global _gemini_available_models
    if _gemini_available_models is not None:
        return _gemini_available_models
    url = GEMINI_LIST_URL.format(key=GEMINI_API_KEY)
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        available = set()
        for m in data.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                name = m["name"]
                if name.startswith("models/"):
                    name = name[len("models/"):]
                available.add(name)
        _gemini_available_models = available
        print("  Gemini models available: " + ", ".join(sorted(available)))
    except Exception as e:
        print("  Warning: could not list Gemini models (%s) — using defaults" % e)
        _gemini_available_models = set()
    return _gemini_available_models


def _pick_gemini_models():
    """Return ordered list to try: preferred models that are available, then rest as fallback."""
    available = _list_gemini_models()
    if not available:
        return GEMINI_PREFERRED  # listing failed — try all in order
    ordered = [m for m in GEMINI_PREFERRED if m in available]
    # append any available multimodal models not in preferred list
    extras = sorted(m for m in available if m not in GEMINI_PREFERRED
                    and ("flash" in m or "pro" in m or "vision" in m))
    return ordered + extras or GEMINI_PREFERRED


def _parse_gemini_json(text):
    """Strip optional ```json fences a model may wrap around the payload, then parse."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def _parse_price_text(text):
    """Pull the first numeric value (price in INR) out of a model reply. None if absent."""
    text = (text or "").strip()
    if text.lower() in ("null", "none", ""):
        return None
    nums = re.findall(r"[\d]+(?:\.\d+)?", text.replace(",", ""))
    return float(nums[0]) if nums else None


def _gemini_call(file_bytes, mime_type, model):
    """Single model call. Raises urllib.error.HTTPError on API errors."""
    payload = json.dumps({
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime_type,
                             "data": base64.b64encode(file_bytes).decode("ascii")}},
            {"text": EXTRACTION_PROMPT},
        ]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048},
    }).encode("utf-8")
    url = GEMINI_GEN_URL.format(model=model, key=GEMINI_API_KEY)
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
    return _parse_gemini_json(text), model


def gemini_extract(file_bytes, mime_type):
    """Try models in preference order; fall back on 429/404. Returns (data, model_used)."""
    models = _pick_gemini_models()
    last_err = None
    for model in models:
        try:
            return _gemini_call(file_bytes, mime_type, model)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in (429, 404, 503):
                print("  Gemini %s: %d — trying next model" % (model, e.code))
                last_err = "Model %s returned %d" % (model, e.code)
                continue
            raise RuntimeError("Gemini API error %d (%s): %s" % (e.code, model, body[:300]))
        except Exception as e:
            raise RuntimeError("Gemini call failed (%s): %s" % (model, e))
    raise RuntimeError(
        "All Gemini models returned 429/quota errors. Last: %s. "
        "Wait a minute or check https://aistudio.google.com/plan" % last_err
    )


PRICE_PROMPT = (
    "Search for the current market price or estimated value in Indian Rupees (INR) of: {goal_name}"
    " (category: {goal_type}).\n"
    "Return ONLY a single integer or decimal number representing the price/value in INR. "
    "No currency symbol, no commas, no explanation, no units — just the number.\n"
    "If you cannot find a specific price, return null.\nExamples: 1500000  or  4800000  or  null"
)


GOLD_PRICE_PROMPT = (
    "What is the current price of exactly ONE (1) gram of 24K (999 purity) gold today in India, "
    "in Indian Rupees? Quote the per-1-gram rate, NOT the per-10-gram rate.\n"
    "Return ONLY a single integer or decimal number — no currency symbol, no commas, no units, "
    "no explanation. If you cannot find it, return null.\nExamples: 7250  or  7840.5  or  null"
)


def gemini_price_search(goal_name, goal_type, prompt=None):
    """Try Gemini with Google Search grounding to fetch live market price. Returns (price_float|None, model)."""
    if prompt is None:
        prompt = PRICE_PROMPT.format(goal_name=goal_name, goal_type=goal_type or "general")
    last_err = None
    for model in _pick_gemini_models():
        url = GEMINI_GEN_URL.format(model=model, key=GEMINI_API_KEY)
        # Try with Google Search grounding first
        for use_grounding in (True, False):
            payload_dict = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 64},
            }
            if use_grounding:
                payload_dict["tools"] = [{"google_search": {}}]
            payload = json.dumps(payload_dict).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
                return _parse_price_text(text), model
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code in (429, 503):
                    last_err = "%s:%d" % (model, e.code)
                    break  # quota — try next model
                if e.code in (400, 404):
                    if use_grounding:
                        continue  # grounding not supported on this model — try without
                    last_err = "%s:%d" % (model, e.code)
                    break
                raise RuntimeError("Gemini %d (%s): %s" % (e.code, model, body[:300]))
    if last_err:
        raise RuntimeError("All Gemini models exhausted. Last: %s" % last_err)
    raise RuntimeError("No Gemini models available")


def data_file():
    return os.path.join(DATA_DIR, "finances.json")


def files_dir():
    return os.path.join(DATA_DIR, "files")


def backups_dir():
    return os.path.join(DATA_DIR, "backups")


def ensure_data(seed_file=None):
    """Create the data dir tree. If finances.json is absent, seed it from seed_file
    (the demo dataset) when given, otherwise from the empty scaffold."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(files_dir(), exist_ok=True)
    os.makedirs(backups_dir(), exist_ok=True)
    if os.path.exists(data_file()):
        return
    if seed_file and os.path.isfile(seed_file):
        shutil.copy2(seed_file, data_file())
        print("  Seeded demo data from: " + seed_file)
    else:
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
        if path == "/api/ping":
            self._send(200, {"app": REGISTRY_KEY, "dataDir": os.path.abspath(DATA_DIR)})
            return
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
        elif path == "/api/gemini-status":
            models = _pick_gemini_models() if GEMINI_API_KEY else []
            self._send(200, {"available": bool(GEMINI_API_KEY),
                             "models": models,
                             "preferred": models[0] if models else None})
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
        with file_lock(data_file()):
            make_backup()  # automatic rotating daily backup (first save of the day)
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
            with file_lock(data_file()):
                make_backup("prerestore")            # safety snapshot of current data
                shutil.copy2(src, data_file())
            return self._send(200, {"ok": True, "restored": name})
        if parsed.path == "/api/fetch-goal-price":
            if not GEMINI_API_KEY:
                return self._err(503, "Gemini API key not configured")
            body = json.loads(self._body().decode("utf-8"))
            goal_name = str(body.get("name", "")).strip()
            goal_type = str(body.get("type", "")).strip()
            if not goal_name:
                return self._err(400, "Goal name required")
            try:
                price, model = gemini_price_search(goal_name, goal_type)
                return self._send(200, {"ok": True, "price": price, "model": model})
            except Exception as e:
                return self._err(422, str(e))
        if parsed.path == "/api/fetch-gold-price":
            if not GEMINI_API_KEY:
                return self._err(503, "Gemini API key not configured")
            try:
                price, model = gemini_price_search("", "", prompt=GOLD_PRICE_PROMPT)
                if price and price > 30000:   # model likely quoted per-10-gram — normalise to per-gram
                    price = round(price / 10.0, 2)
                return self._send(200, {"ok": True, "price": price, "model": model})
            except Exception as e:
                return self._err(422, str(e))
        if parsed.path == "/api/extract-payslip":
            if not GEMINI_API_KEY:
                return self._err(503, "Gemini API key not configured. Set GEMINI_API_KEY in your environment.")
            body = json.loads(self._body().decode("utf-8"))
            name = safe_name(body.get("filename", ""))
            fp   = os.path.join(files_dir(), name)
            if not os.path.isfile(fp):
                return self._err(404, "File not found: %s" % name)
            ext  = os.path.splitext(name)[1].lower()
            mime = PAYSLIP_MIME.get(ext)
            if not mime:
                return self._err(400, "Unsupported file type. Upload a PDF, PNG, JPG, or WEBP.")
            with open(fp, "rb") as f:
                file_bytes = f.read()
            try:
                extracted, model_used = gemini_extract(file_bytes, mime)
                return self._send(200, {"ok": True, "extracted": extracted, "model": model_used})
            except Exception as e:
                return self._err(422, str(e))
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


def app_already_running(port, data_dir):
    """True if *this* app is already serving *the same data dir* on `port`, so we
    open it instead of starting a second writer. A second instance on a different
    data dir (e.g. --demo / --data-dir) is allowed and won't match here."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=0.5) as r:
            info = json.loads(r.read().decode())
        return (info.get("app") == REGISTRY_KEY
                and info.get("dataDir") == os.path.abspath(data_dir))
    except Exception:
        return False


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
    return None   # not registered — caller must supply --port


def main():
    global DATA_DIR
    ap = argparse.ArgumentParser(description="Family Finance local server")
    ap.add_argument("--port", type=int, default=None,
                    help="Port to bind. Defaults to this app's entry in ports.json; "
                         "required if there is no registry entry.")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--host", default="127.0.0.1",
                    help="Host/IP to bind to. Default 127.0.0.1 keeps it on this "
                         "machine only. 0.0.0.0 exposes finances.json to anyone who "
                         "can reach this host on the LAN/VPN — there is no auth, so "
                         "only do this on a network you fully trust.")
    ap.add_argument("--demo", action="store_true",
                    help="Run on a throwaway demo dataset in demo-data/ (seeded from "
                         "samples/), leaving your real data/ folder completely untouched")
    args = ap.parse_args()

    seed = None
    if args.demo:
        # isolate everything (finances.json, files/, backups/) into demo-data/ unless
        # the user explicitly pointed --data-dir elsewhere; real data/ is never written.
        DATA_DIR = os.path.abspath(args.data_dir) if args.data_dir != DEFAULT_DATA_DIR else DEMO_DATA_DIR
        seed = SAMPLE_FILE
    else:
        DATA_DIR = os.path.abspath(args.data_dir)
    ensure_data(seed)

    port = args.port if args.port is not None else get_registered_port()
    if port is None:
        print("No port configured for family-finance-app. Add it to ports.json "
              "or pass --port <N>.")
        sys.exit(1)

    if app_already_running(port, DATA_DIR):
        url = "http://127.0.0.1:%d" % port
        print("Family Finance is already running on this data => " + url)
        print("Opening the existing instance (not starting a second one).")
        if not args.no_browser:
            webbrowser.open(url)
        sys.exit(0)

    try:
        httpd = FFServer((args.host, port), Handler)
    except OSError as e:
        print("Port %d is unavailable — another application is using it (%s)." % (port, e))
        sys.exit(1)

    display_host = "100.90.58.116" if args.host == "0.0.0.0" else args.host
    url = "http://%s:%d" % (display_host, port)
    print("=" * 52)
    print("  Family Finance" + ("  [DEMO MODE]" if args.demo else ""))
    print("  App:  " + url)
    print("  Data: " + data_file())
    if args.demo:
        print("  Demo sandbox — your real data/ folder is untouched.")
        print("  Reset the demo anytime by deleting: " + DATA_DIR)
    print("  Press Ctrl+C to stop.")
    if args.host not in ("127.0.0.1", "localhost"):
        print("  ⚠  Bound to %s — finances.json is reachable by other devices on" % args.host)
        print("     this network (no auth). Use only on a network you trust.")
    print("=" * 52)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
