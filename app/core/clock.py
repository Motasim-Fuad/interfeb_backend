from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings

_ZONE = ZoneInfo(settings.TIMEZONE)


def factory_today() -> date:
    """The factory's calendar day. The server runs on UTC, so date.today() is a day behind
    for anything saved between local midnight and 6 AM in Asia/Dhaka."""
    return datetime.now(_ZONE).date()
