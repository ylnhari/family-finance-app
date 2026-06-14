# Claude Instructions — family-finance-app

> **Setup:** copy `CLAUDE.local.md.example` → `CLAUDE.local.md` (gitignored, auto-loaded) and fill in your machine-local values. Personal/global preferences live in your user-level `~/.claude/CLAUDE.md`.

## Role
Maintain a self-contained local finance tracker. No cloud, no accounts, no external deps — Python stdlib + browser only.

## Context
A self-contained personal/family finance tracker. Tracks income, expenses, loans, cards. Data lives in `data/finances.json` (gitignored). Server is `server.py` (stdlib only); default port 8765 — see `CLAUDE.local.md`.

**Privacy rule:** `data/` is gitignored. Never read, log, or transmit the contents of `data/finances.json` to any external service.

## Architecture

```
server.py          # Stdlib HTTP server (no Flask, no FastAPI)
public/            # Frontend (HTML/CSS/JS)
data/
  finances.json    # All records — gitignored, never commit
  files/           # Uploaded documents
  backups/         # Auto daily backups (last 14 kept)
start.bat          # Windows launcher
start.sh           # Unix launcher
```

## Rules
1. **No new dependencies.** stdlib only for the server. Frontend may use CDN scripts if absolutely necessary.
2. **Data stays local.** No API calls from `server.py` that transmit financial data anywhere.
3. **Keep `data/` gitignored.** Verify `.gitignore` before any git operation.
4. **Backups are automatic** — don't delete `data/backups/` manually.

## Running
```bash
python server.py          # Opens http://127.0.0.1:8765
python server.py --port 9000 --no-browser
```

## When editing
- Test server changes: restart and verify the browser UI loads correctly.
- Frontend changes: hard-refresh the browser (Ctrl+Shift+R) to clear cache.
- Never add a feature that requires an external service call.
