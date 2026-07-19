"""Scheduled brief: refresh NSE subscription numbers, push phone alerts via
ntfy.sh. Run by Windows Task Scheduler (morning + early afternoon); silent
when nothing is actionable.

Two independent pushes, both deliberately free of portfolio figures
(user's choice 2026-07-07 re: IPOs, extended the same way for the Wint
reminder): IPO alerts carry public NSE data only (names, subscription
multiples, apply/skip call); the Wint reminder carries only "it's been N
days" — never a holdings value. Portfolio holdings and signals never leave
the machine; those live on the dashboard at the /invest page of the dashboard.

ntfy: subscribe once on the phone to https://ntfy.sh/<NTFY_TOPIC> (app or
browser). The topic name is effectively the password — keep it random.
"""

import os
import sys
from datetime import date

try:
    import requests  # optional — only needed to push ntfy alerts (requirements-invest.txt)
except ImportError:
    requests = None

import config  # noqa: F401  (loads .env)
from investlib import analysis, ipo, ipo_fetch, store

WINT_REMIND_EVERY_DAYS = 7  # don't re-nag more than once a week even if still stale


def build_message() -> str | None:
    # Prefer the Upstox IPO API (reliable, carries symbols); fall back to NSE
    # scraping if it yields nothing (e.g. token/Static-IP not set up).
    try:
        if ipo_fetch.refresh_from_upstox().get("count"):
            pass
        else:
            ipo_fetch.refresh()
    except Exception as e:
        print(f"Upstox IPO refresh failed ({e}); falling back to NSE", file=sys.stderr)
        try:
            ipo_fetch.refresh()
        except Exception as e2:  # both sources flaking shouldn't kill the brief
            print(f"NSE refresh failed: {e2}", file=sys.stderr)

    lines = []
    for item in ipo.reminders():
        if item["call"] in ("APPLY", "CAUTION"):
            when = "closes TODAY" if item["days_to_close"] == 0 else f"closes in {item['days_to_close']}d"
            lines.append(f"[{item['call']}] {item['name']} {when} — {item['reason']}")
    return "\n".join(lines) if lines else None


def push(message: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC not set; printing only")
        return
    if requests is None:
        print("requests not installed (pip install -r requirements-invest.txt); printing only")
        return
    requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                  headers={"Title": "IPO alert", "Tags": "chart_with_upwards_trend"},
                  timeout=30)


def build_allotment_reminder() -> str | None:
    """Nudge on the allotment day of any IPO you applied for. Carries no
    holdings data — just "check it"; the allotted/not answer + listing-day P&L
    stay on the local dashboard (same privacy line as the other pushes)."""
    due = ipo.allotment_reminders()
    if not due:
        return None
    names = "\n".join(f"{d['name']} — allotment finalizes today" for d in due)
    return "Check your IPO allotment on the dashboard:\n" + names


def push_allotment_reminder(message: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC not set; printing only")
        return
    if requests is None:
        print("requests not installed (pip install -r requirements-invest.txt); printing only")
        return
    requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                  headers={"Title": "IPO allotment day", "Tags": "tickets"},
                  timeout=30)


def build_wint_reminder() -> str | None:
    """Nudge to re-download + re-import a Wint Wealth statement once an
    account's data goes stale, at most once every WINT_REMIND_EVERY_DAYS
    per account (so twice-daily runs don't spam)."""
    stale = analysis.bonds_needing_refresh()
    if not stale:
        return None
    today = date.today()
    last_sent = store.load("wint_reminder_state", default={})
    due = []
    for item in stale:
        last = last_sent.get(item["account"])
        if last is None or (today - date.fromisoformat(last)).days >= WINT_REMIND_EVERY_DAYS:
            due.append(item)
            last_sent[item["account"]] = today.isoformat()
    if not due:
        return None
    store.save("wint_reminder_state", last_sent)
    lines = [f"{item['label']} ({item['account']}) — last updated {item['as_of']}, {item['days_stale']}d ago"
             for item in due]
    return "Download the Holding + Cashflow statements and drop them in:\n" + "\n".join(lines)


def push_wint_reminder(message: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC not set; printing only")
        return
    if requests is None:
        print("requests not installed (pip install -r requirements-invest.txt); printing only")
        return
    requests.post(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                  headers={"Title": "Wint Wealth: time to refresh", "Tags": "receipt"},
                  timeout=30)


if __name__ == "__main__":
    msg = build_message()
    if msg:
        print(msg)
        push(msg)
    else:
        print("nothing actionable today")

    allot_msg = build_allotment_reminder()
    if allot_msg:
        print(allot_msg)
        push_allotment_reminder(allot_msg)

    wint_msg = build_wint_reminder()
    if wint_msg:
        print(wint_msg)
        push_wint_reminder(wint_msg)
