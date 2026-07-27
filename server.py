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
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import config as invest_config  # side effect: loads .env (GEMINI_API_KEY, broker keys)
import invest_api
from investlib import bridge as invest_bridge

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(APP_DIR, "public")
DOCS_DIR = os.path.join(APP_DIR, "docs")  # BROKER-SETUP.md / NOTIFICATIONS.md, served read-only
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
    ".md": "text/plain; charset=utf-8",
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


# ── finances.json access for invest_api (owner -> settings.persons) ─────────
# invest_api.py has no direct knowledge of finances.json; these two callbacks
# are handed to it via invest_api.set_finances_hooks() in main() so the owner
# endpoint can read/append settings.persons through the same locked write
# path do_PUT uses, while keeping invest_api importable/testable standalone.

def _finances_persons():
    try:
        with open(data_file(), "r", encoding="utf-8") as f:
            doc = json.load(f)
        return list(doc.get("settings", {}).get("persons", []))
    except Exception:
        return []


def _finances_add_person(name):
    if not name:
        return
    with file_lock(data_file()):
        try:
            with open(data_file(), "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            return
        persons = doc.setdefault("settings", {}).setdefault("persons", [])
        if name in persons:
            return
        persons.append(name)
        tmp = data_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
        os.replace(tmp, data_file())


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


# The complete set of owner names the demo seeder may use. 100% fake — NEVER a
# real account owner. Arjun/Priya are the demo finances persons (from
# samples/demo-finances.json); Rohan is an extra so the demo shows three owners.
# The demo-owner guard test asserts every seeded owner is in this allowlist.
FAKE_DEMO_OWNERS = ("Arjun", "Priya", "Rohan")


def ensure_demo_invest_data():
    """Seed a small, entirely fake investment dataset for --demo: three fake
    accounts (two brokers + a bond account) owned by a fake family, so the demo
    shows a populated multi-account view. Goes through investlib itself so it is
    always schema-correct; the real data/ is never touched, and no real account
    owner ever appears (see FAKE_DEMO_OWNERS)."""
    from investlib import ipo as invest_ipo, portfolio as invest_portfolio, store as invest_store
    if (invest_config.DATA_DIR / "holdings.json").exists():
        return
    invest_store.create_account("kite-1", "kite", label="Kite — demo", owner="Arjun")
    invest_store.create_account("upstox-1", "upstox", label="Upstox — demo", owner="Rohan")
    invest_store.create_account("wint-1", "wint", label="Wint Wealth — demo", owner="Priya")
    invest_portfolio.set_manual_holdings("kite-1", [
        {"symbol": "DEMOSTEEL", "quantity": 40, "avg_price": 510, "last_price": 585},
        {"symbol": "DEMOBANK", "quantity": 120, "avg_price": 92, "last_price": 88},
    ])
    invest_portfolio.set_manual_holdings("upstox-1", [
        {"symbol": "DEMOTECH", "quantity": 15, "avg_price": 1200, "last_price": 1450},
    ])
    invest_portfolio.set_manual_holdings("wint-1", [
        {"symbol": "DemoInfra NCD 11.0% 2027", "isin": "INE000DEMO01",
         "quantity": 10, "avg_price": 10000, "last_price": 10000},
    ])
    # Close date is seeded relative to now (~2 weeks out) so the demo IPO always
    # reads as a realistic "14d left", never a fixed far-future "26461d left".
    demo_ipo_close = (date.today() + timedelta(days=14)).isoformat()
    invest_ipo.upsert(name="Demo Foods", close_date=demo_ipo_close,
                      sub_total=12.4, sub_retail=3.1, gmp=25)
    print("  Seeded demo investment data under: " + str(invest_config.DATA_DIR))


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


def _md_escape(text):
    """HTML-escape ahead of any markdown-to-HTML conversion (XSS-safe: every angle
    bracket / ampersand / quote in the source becomes an entity BEFORE we add our
    own trusted tags)."""
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def _md_inline(text):
    """Inline markdown on an already-escaped line: `code`, **bold**, [text](url).
    Deliberately does NOT touch underscores — the broker docs are full of env-var
    names like KITE_1_API_KEY that must not turn into italics."""
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    # links: [label](url) — url restricted to http(s) or a site-relative path so an
    # escaped source can't smuggle a javascript: URL into the href we generate.
    text = re.sub(r"\[([^\]]+)\]\((https?:[^)\s]+|/[^)\s]*)\)",
                  lambda m: '<a href="%s" target="_blank" rel="noopener">%s</a>'
                            % (m.group(2), m.group(1)), text)
    return text


def render_markdown(md_text):
    """Minimal, safe Markdown -> HTML for the local docs viewer (stdlib only).
    Escapes first, then converts fenced code blocks, headings, ordered/unordered
    lists, blockquotes, bold/inline-code/links, and paragraphs. Not a full CommonMark
    implementation — just enough to render docs/*.md as a readable page rather than
    raw source."""
    lines = _md_escape(md_text).split("\n")
    out, in_code, in_ul, in_ol = [], False, False, False
    para_buf = []  # consecutive non-blank plain-text lines belong to ONE paragraph
    # (soft-wrapped source lines, no blank line between them) — flushed as a single
    # <p> so prose reads as normal paragraphs instead of one <p> per source line.

    def flush_para():
        if para_buf:
            out.append("<p>%s</p>" % _md_inline(" ".join(para_buf)))
            del para_buf[:]

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>"); in_ul = False
        if in_ol:
            out.append("</ol>"); in_ol = False

    # ---- GFM-style pipe tables: "| a | b |" header, "|---|---|" separator,
    # then data rows. Operates on already-`_md_escape`d lines (see call above),
    # and runs each cell through `_md_inline` — same escape-then-tag ordering
    # as every other block below, so a `<script>` cell stays inert text.
    _TABLE_SEP_CELL = re.compile(r"^:?-{1,}:?$")

    def _split_row(line):
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|") and not line.endswith("\\|"):
            line = line[:-1]
        return [c.strip() for c in re.split(r"(?<!\\)\|", line)]

    def _is_separator_row(line):
        cells = _split_row(line)
        return bool(cells) and all(_TABLE_SEP_CELL.match(c) for c in cells)

    def _cell_align(spec):
        left, right = spec.startswith(":"), spec.endswith(":")
        if left and right:
            return "center"
        if right:
            return "right"
        if left:
            return "left"
        return None

    def _table_cell(tag, text, align):
        attr = ' style="text-align:%s"' % align if align else ""
        return "<%s%s>%s</%s>" % (tag, attr, _md_inline(text), tag)

    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                out.append("</code></pre>"); in_code = False
            else:
                flush_para(); close_lists(); out.append("<pre><code>"); in_code = True
            i += 1
            continue
        if in_code:
            out.append(raw)
            i += 1
            continue
        if not line.strip():
            flush_para(); close_lists()
            i += 1
            continue
        if "|" in line and i + 1 < n and "|" in lines[i + 1] and _is_separator_row(lines[i + 1]):
            flush_para(); close_lists()
            aligns = [_cell_align(c) for c in _split_row(lines[i + 1])]
            out.append("<table><thead><tr>" + "".join(
                _table_cell("th", c, aligns[idx] if idx < len(aligns) else None)
                for idx, c in enumerate(_split_row(line))) + "</tr></thead><tbody>")
            i += 2
            while i < n and lines[i].strip() and "|" in lines[i]:
                out.append("<tr>" + "".join(
                    _table_cell("td", c, aligns[idx] if idx < len(aligns) else None)
                    for idx, c in enumerate(_split_row(lines[i]))) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        h = re.match(r"(#{1,6})\s+(.*)$", line)
        if h:
            flush_para(); close_lists()
            level = len(h.group(1))
            out.append("<h%d>%s</h%d>" % (level, _md_inline(h.group(2)), level))
            i += 1
            continue
        m = re.match(r"[-*]\s+(.*)$", line.lstrip())
        if m:
            flush_para()
            if in_ol:
                out.append("</ol>"); in_ol = False
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append("<li>%s</li>" % _md_inline(m.group(1)))
            i += 1
            continue
        m = re.match(r"\d+\.\s+(.*)$", line.lstrip())
        if m:
            flush_para()
            if in_ul:
                out.append("</ul>"); in_ul = False
            if not in_ol:
                out.append("<ol>"); in_ol = True
            out.append("<li>%s</li>" % _md_inline(m.group(1)))
            i += 1
            continue
        if line.lstrip().startswith("&gt;"):  # blockquote (">" was escaped)
            flush_para(); close_lists()
            out.append("<blockquote>%s</blockquote>" % _md_inline(line.lstrip()[4:].lstrip()))
            i += 1
            continue
        # A plain line while a list is open is a lazy continuation of the current
        # item (the blank-line branch above already closed the list otherwise), so
        # it belongs INSIDE that <li> — not as a new un-indented paragraph beneath
        # the bullet, which is how soft-wrapped list items used to render.
        if (in_ul or in_ol) and out and out[-1].startswith("<li>") and out[-1].endswith("</li>"):
            out[-1] = "%s %s</li>" % (out[-1][: -len("</li>")], _md_inline(line))
            i += 1
            continue
        # plain text line: accumulate into the paragraph buffer rather than
        # emitting immediately — a soft-wrapped paragraph (multiple source
        # lines, no blank line between them) must render as ONE <p>, not one
        # <p> per line (that was inflating perceived line spacing).
        close_lists()
        para_buf.append(line)
        i += 1

    if in_code:
        out.append("</code></pre>")
    flush_para()
    close_lists()
    body = "\n".join(out)
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Docs</title><style>"
        "body{max-width:820px;margin:2rem auto;padding:0 1.2rem;line-height:1.6;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;"
        "color:#0f172a;background:#f8fafc}"
        "h1,h2,h3{line-height:1.25;letter-spacing:-.01em;margin:1.3em 0 .5em}"
        "h1:first-child,h2:first-child,h3:first-child{margin-top:0}"
        "p{margin:.75em 0}"
        "ul,ol{margin:.6em 0;padding-left:1.4em}"
        "li{margin:.2em 0}"
        # inline code keeps line-height:1 so it never stretches the paragraph line box
        "code{background:#eef1f7;padding:.15em .4em;border-radius:5px;font-size:.9em;"
        "line-height:1}"
        "pre{background:#0f172a;color:#e2e8f0;padding:1rem;border-radius:10px;overflow:auto}"
        "pre code{background:none;color:inherit;padding:0;line-height:inherit}"
        "a{color:#4f46e5}blockquote{border-left:3px solid #c7d2fe;margin:.6rem 0;"
        "padding:.2rem .9rem;color:#475569}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.95em}"
        "th,td{border:1px solid #dde3ee;padding:.4rem .7rem;text-align:left}"
        "th{background:#eef1f7;font-weight:600}"
        "@media(prefers-color-scheme:dark){body{color:#e8ecf5;background:#0a0e1a}"
        "code{background:#1e293b}a{color:#a5b4fc}blockquote{color:#94a3b8;border-color:#334155}"
        "th,td{border-color:#2c3648}th{background:#1e293b}}"
        "</style></head><body>\n" + body + "\n</body></html>"
    )


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

    def _invest(self, result):
        """Send an invest_api handler result: ('json', status, payload) | ('redirect', loc)."""
        if result is None:
            return self._err(404, "Not found")
        if result[0] == "redirect":
            self.send_response(302)
            self.send_header("Location", result[1])
            self.end_headers()
            return
        _, status, payload = result
        self._send(status, payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/invest":
            return self._serve_file(os.path.join(PUBLIC_DIR, "invest.html"))
        if path.startswith("/api/invest/") or path.startswith("/auth/"):
            return self._invest(invest_api.handle_get(path, parse_qs(parsed.query)))
        if path == "/api/ping":
            self._send(200, {"app": REGISTRY_KEY, "dataDir": os.path.abspath(DATA_DIR)})
            return
        if path == "/api/data":
            with open(data_file(), "r", encoding="utf-8") as f:
                doc = json.load(f)
            try:
                doc = invest_bridge.inject_live_rows(doc)
            except Exception as e:
                # never let a broken invest store take down the finance app —
                # degrade to no live rows, but surface it so the UI can toast it.
                doc["liveRowsError"] = str(e)
            self._send(200, doc)
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
        elif path.startswith("/docs/"):
            self._serve_doc(path[len("/docs/"):])
        else:
            if path == "/":
                path = "/index.html"
            fp = os.path.normpath(os.path.join(PUBLIC_DIR, path.lstrip("/")))
            if not fp.startswith(PUBLIC_DIR):
                return self._err(403, "Forbidden")
            self._serve_file(fp)

    def _serve_doc(self, name):
        """Serve docs/<name>.md read-only (BROKER-SETUP.md, NOTIFICATIONS.md — linked
        from the /invest onboarding panel so those links work offline). Same
        normpath + startswith guard the public/ catch-all below uses against
        path traversal, plus an extension allowlist since docs/ only holds .md."""
        name = unquote(name or "")
        if not name.lower().endswith(".md"):
            return self._err(404, "Not found")
        fp = os.path.normpath(os.path.join(DOCS_DIR, name))
        if not fp.startswith(DOCS_DIR):
            return self._err(403, "Forbidden")
        if not os.path.isfile(fp):
            return self._err(404, "Not found")
        # Render Markdown -> HTML so the docs read as a page, not raw source
        # (escape-first renderer, stdlib only — see render_markdown()).
        with open(fp, "r", encoding="utf-8") as f:
            html = render_markdown(f.read())
        self._send(200, html, "text/html; charset=utf-8")

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
        try:
            # Live rows are server-owned: replace whatever the client sent
            # (possibly stale/tampered) with freshly computed ones before
            # this is ever written to disk.
            data = invest_bridge.inject_live_rows(data)
        except Exception:
            # invest store unreadable — degrade to no live rows rather than
            # fail the save, but never persist a client-sent liveSync row
            # we couldn't verify.
            data["portfolio"] = [r for r in (data.get("portfolio") or []) if not r.get("liveSync")]
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
        if parsed.path.startswith("/api/invest/"):
            return self._invest(invest_api.handle_post(parsed.path, self._body()))
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
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/invest/"):
            return self._invest(invest_api.handle_delete(path, parse_qs(parsed.query)))
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

    def handle_error(self, request, client_address):
        """A client that navigates away / closes the tab mid-response aborts the
        socket, which surfaces here as a ConnectionAbortedError (WinError 10053) /
        BrokenPipeError / ConnectionResetError. That's an ordinary, expected event —
        collapse it to a one-line note instead of a full traceback, but let every
        other (genuine) error print its traceback as before."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, BrokenPipeError, ConnectionResetError)):
            print("  (client %s disconnected before the response finished)" % (client_address[0]
                  if client_address else "?"))
            return
        super().handle_error(request, client_address)


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


def _lan_ipv4s():
    """Best-effort list of this machine's non-loopback IPv4 addresses, for the
    startup banner when bound to all interfaces. Computed at runtime — never a
    hardcoded/personal IP in the source."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1: no packets sent for a UDP connect
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def main():
    global DATA_DIR
    # Windows consoles default to cp1252, which can't encode characters like
    # "⚠" used in startup prints below — reconfigure to UTF-8 (with a
    # replace fallback) so startup never crashes; if the stream doesn't
    # support reconfigure (e.g. redirected/mocked), just carry on.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
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
    invest_api.set_finances_hooks(_finances_persons, _finances_add_person)

    # Investment collections follow the same data root, so --demo / --data-dir
    # isolate them exactly like finances.json.
    invest_config.DATA_DIR = Path(DATA_DIR) / "invest"
    # Import drops (imports/) isolate with the data dir too — but only when the
    # user redirected the data dir (--demo or an explicit --data-dir). A plain
    # `python server.py` keeps reading the repo-root imports/ it always has, so
    # existing installs are unaffected.
    if args.demo or os.path.abspath(args.data_dir) != os.path.abspath(DEFAULT_DATA_DIR):
        invest_config.IMPORTS_DIR = Path(DATA_DIR) / "imports"
    if args.demo:
        ensure_demo_invest_data()

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

    invest_config.RUNTIME_PORT = port  # broker OAuth redirect URLs follow the real port

    try:
        httpd = FFServer((args.host, port), Handler)
    except OSError as e:
        print("Port %d is unavailable — another application is using it (%s)." % (port, e))
        sys.exit(1)

    # When bound to all interfaces, the loopback URL still works locally; the
    # actual reachable LAN addresses are listed below the warning.
    browse_host = "127.0.0.1" if args.host in ("0.0.0.0", "::", "") else args.host
    url = "http://%s:%d" % (browse_host, port)
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
        for ip in _lan_ipv4s():
            print("     • http://%s:%d" % (ip, port))
    print("=" * 52)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
