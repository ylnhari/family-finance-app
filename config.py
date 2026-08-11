"""Central config for the investments module (investlib + /api/invest routes).

Reads the app port from the shared ports.json registry when one exists
(falling back to the app default so open-source checkouts work standalone)
and secrets/overrides from a gitignored .env — never hardcode either.
"""

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PROJECTS_ROOT = REPO_ROOT.parent
PORTS_JSON = PROJECTS_ROOT / "ports.json"
PROJECT_KEY = "family-finance-app"
DEFAULT_PORT = 8765

# Invest collections (accounts/holdings/ipos/tokens) live inside the app's
# gitignored data dir. server.py re-points this at startup so --data-dir and
# --demo isolate investment data exactly like finances.json.
DATA_DIR = REPO_ROOT / "data" / "invest"
IMPORTS_DIR = REPO_ROOT / "imports"

HOST = "127.0.0.1"  # never widen; remote access goes through rover/tailscale

RUNTIME_PORT = None  # set by server.py once the real bound port is known
NOTIFICATION_TOPIC_ENV = "INVESTMENTS_NTFY_TOPIC"


def _load_env() -> None:
    """Minimal .env loader (repo .env, then the parent dir's .env for shared keys).

    FF_NO_DOTENV=1 skips loading entirely — tests use it so a developer's real
    .env can't leak keys into subprocesses that assert key-less behavior."""
    if os.environ.get("FF_NO_DOTENV"):
        return
    for env_path in (REPO_ROOT / ".env", PROJECTS_ROOT / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()


def notification_topic() -> str:
    """Return the investments-only ntfy topic, never a shared generic topic."""
    return os.environ.get(NOTIFICATION_TOPIC_ENV, "").strip()


def port() -> int:
    """The port the app serves on — used to build broker OAuth redirect URLs."""
    if RUNTIME_PORT:
        return int(RUNTIME_PORT)
    try:
        registry = json.loads(PORTS_JSON.read_text(encoding="utf-8"))["registry"]
        return int(registry[PROJECT_KEY]["port"])
    except (OSError, KeyError, ValueError):
        return DEFAULT_PORT
