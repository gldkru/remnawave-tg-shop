from datetime import datetime, timedelta
from typing import Optional

# Russian needs the genitive after a day number ("12 декабря"), not the nominative ("декабрь").
_RU_MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_EN_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def format_long_date(dt: datetime, lang: Optional[str]) -> str:
    """Human date for user-facing text: '12 декабря 2026' (ru), 'December 12, 2026' (other)."""
    if (lang or "").lower().startswith("ru"):
        return f"{dt.day} {_RU_MONTHS_GENITIVE[dt.month - 1]} {dt.year}"
    return f"{_EN_MONTHS[dt.month - 1]} {dt.day}, {dt.year}"


def format_days(days: int, lang: Optional[str]) -> str:
    """Day count with the right plural: '1 день', '3 дня', '10 дней' (ru), '1 day', '5 days' (other)."""
    if (lang or "").lower().startswith("ru"):
        n = abs(days)
        if n % 10 == 1 and n % 100 != 11:
            word = "день"
        elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            word = "дня"
        else:
            word = "дней"
        return f"{days} {word}"
    return f"{days} day" if abs(days) == 1 else f"{days} days"


def add_months(base_dt: datetime, months_to_add: int) -> datetime:
    """Add calendar months to a datetime, clamping the day to the month's length.

    Preserves tzinfo from base_dt.
    """
    year = base_dt.year
    month = base_dt.month + months_to_add
    day = base_dt.day

    # Normalize year and month
    year += (month - 1) // 12
    month = ((month - 1) % 12) + 1

    # Determine last day of target month by rolling to next month's first day and subtracting 1 day
    if month == 12:
        next_month_first = datetime(year + 1, 1, 1, tzinfo=base_dt.tzinfo)
    else:
        next_month_first = datetime(year, month + 1, 1, tzinfo=base_dt.tzinfo)
    last_day = (next_month_first - timedelta(days=1)).day

    clamped_day = min(day, last_day)
    return base_dt.replace(year=year, month=month, day=clamped_day)


