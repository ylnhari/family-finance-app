"""Regular NSE Capital Market days used for IPO-bidding reminders.

The IPO reminder is decision support for a live bid, not an order queue.  It
therefore uses regular exchange business days: Saturdays, Sundays, and the
reviewed NSE Capital Market holiday list are not actionable.  Special market
sessions such as Muhurat trading do not turn an ordinary IPO reminder into an
IPO bidding window.

The calendar is intentionally local and stdlib-only.  Unknown years fail
closed so a stale calendar cannot claim that a day is actionable.  Add each
new year's official NSE list, including later exchange amendments, before
that year is used for unattended reminders.
"""

from datetime import date
from typing import Literal


MarketStatus = Literal["open", "closed", "unknown"]


# NSE Capital Market Segment circular NSE/CMTR/71775 (2026), plus the
# subsequent January 15, 2026 amendment NSE/CMTR/72260.
NSE_CAPITAL_MARKET_HOLIDAYS = {
    2026: frozenset({
        date(2026, 1, 15),  # Municipal Corporation Election amendment
        date(2026, 1, 26),  # Republic Day
        date(2026, 3, 3),   # Holi
        date(2026, 3, 26),  # Shri Ram Navami
        date(2026, 3, 31),  # Shri Mahavir Jayanti
        date(2026, 4, 3),   # Good Friday
        date(2026, 4, 14),  # Dr. Baba Saheb Ambedkar Jayanti
        date(2026, 5, 1),   # Maharashtra Day
        date(2026, 5, 28),  # Bakri Id
        date(2026, 6, 26),  # Muharram
        date(2026, 9, 14),  # Ganesh Chaturthi
        date(2026, 10, 2),  # Mahatma Gandhi Jayanti
        date(2026, 10, 20), # Dussehra
        date(2026, 11, 10), # Diwali-Balipratipada
        date(2026, 11, 24), # Prakash Gurpurb Sri Guru Nanak Dev
        date(2026, 12, 25), # Christmas
    }),
}


def market_status(day: date | None = None) -> MarketStatus:
    """Return whether ``day`` is open for a regular IPO bidding reminder."""
    day = day or date.today()
    if day.weekday() >= 5:
        return "closed"
    holidays = NSE_CAPITAL_MARKET_HOLIDAYS.get(day.year)
    if holidays is None:
        return "unknown"
    return "closed" if day in holidays else "open"


def is_ipo_bidding_day(day: date | None = None) -> bool:
    """Return true only when the local calendar verifies a regular open day."""
    return market_status(day) == "open"
