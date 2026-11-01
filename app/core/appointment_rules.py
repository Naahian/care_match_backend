from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings

ACTIVE_STATUSES = {"requested", "confirmed"}


def appointment_day_start(now: datetime | None = None) -> datetime:
    local = (now or datetime.now(timezone.utc)).astimezone(
        ZoneInfo(settings.appointment_timezone)
    )
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def is_expired(record: dict, now: datetime | None = None) -> bool:
    scheduled = record.get("scheduled_for")
    if isinstance(scheduled, str):
        scheduled = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
    if not isinstance(scheduled, datetime):
        return False
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=timezone.utc)
    return record.get("status") in ACTIVE_STATUSES and scheduled < appointment_day_start(now)
