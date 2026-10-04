import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, demo_data

TZ = ZoneInfo(config.TIMEZONE)

# "all_time" always exists (nominal start = the first day data exists at all);
# whichever of the calendar/rolling options resolve to that same window get
# hidden by all_ranges() below instead of shown as duplicates.
RANGE_DEFS = [
    ("last_7_days", "7 days", 7),
    ("last_30_days", "30 days", 30),
    ("this_month", "This month", None),
    ("this_quarter", "This quarter", None),
    ("this_year", "This year", None),
    ("all_time", "All time", None),
]
_LABELS = dict((key, label) for key, label, _ in RANGE_DEFS)


def today() -> date:
    # The demo is frozen at its snapshot, so it looks the same on any day.
    return demo_data.today() if config.DEMO else datetime.now(TZ).date()


@dataclass
class Range:
    key: str
    label: str
    start: date  # trimmed forward to the first day data exists, if later
    end: date  # nominal calendar end (may be later than data actually goes)
    effective_end: date  # min(end, as_of) - the boundary we trust for sums
    has_current_data: bool  # does [start, effective_end] contain any real days
    prev_start: date
    prev_end: date
    has_previous_data: bool
    bucket: str  # "day" | "week" | "month"
    start_trimmed: bool  # True if `start` was pulled forward past the nominal start


# Calendar periods compare against the previous calendar period, aligned to
# the same number of days in (September 1-22 vs August 1-22, Q-to-date vs
# the previous Q-to-date) instead of against the N days right before them.
CALENDAR_MONTHS = {"this_month": 1, "this_quarter": 3, "this_year": 12}


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def _nominal_start_end(key: str, t: date, earliest: date | None, as_of: date) -> tuple[date, date]:
    # Rolling windows end on the last day that has data, so "7 days" is
    # seven days of entered numbers rather than five plus two empty ones.
    for k, _, n in RANGE_DEFS:
        if k == key and n is not None:
            return as_of - timedelta(days=n - 1), as_of
    if key == "this_month":
        start = t.replace(day=1)
        return start, add_months(start, 1) - timedelta(days=1)
    if key == "this_quarter":
        start = t.replace(month=(t.month - 1) // 3 * 3 + 1, day=1)
        return start, add_months(start, 3) - timedelta(days=1)
    if key == "this_year":
        return t.replace(month=1, day=1), t
    if key == "all_time":
        return earliest or as_of, as_of
    raise ValueError(f"Unknown range: {key}")


def _bucket_for(days: int) -> str:
    # A quarter still reads fine day by day; weeks only past ~3 months.
    if days > 400:
        return "month"
    if days > 100:
        return "week"
    return "day"


def resolve(key: str, as_of: date | None = None, earliest: date | None = None) -> Range:
    if key not in _LABELS:
        raise ValueError(f"Unknown range: {key}")
    t = today()
    if as_of is None:
        as_of = t

    nominal_start, end = _nominal_start_end(key, t, earliest, as_of)

    start = nominal_start
    start_trimmed = False
    if earliest and earliest > start:
        start = earliest
        start_trimmed = True

    effective_end = min(end, as_of)
    has_current_data = effective_end >= start

    if has_current_data:
        length = (effective_end - start).days + 1
    else:
        length = (end - start).days + 1
    if key in CALENDAR_MONTHS:
        prev_nominal_start = add_months(nominal_start, -CALENDAR_MONTHS[key])
        prev_period_end = nominal_start - timedelta(days=1)
        prev_start = prev_nominal_start + (start - nominal_start)
        prev_end = min(prev_start + timedelta(days=length - 1), prev_period_end)
    else:
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=length - 1)
    # The whole previous window must lie inside the data: one that starts
    # before the first entered day would compare against a shorter span.
    has_previous_data = (earliest is None) or (prev_start >= earliest)

    span_days = (end - start).days + 1
    bucket = _bucket_for(span_days)

    return Range(
        key=key,
        label=_LABELS[key],
        start=start,
        end=end,
        effective_end=effective_end,
        has_current_data=has_current_data,
        prev_start=prev_start,
        prev_end=prev_end,
        has_previous_data=has_previous_data,
        bucket=bucket,
        start_trimmed=start_trimmed,
    )


def resolve_custom(start: date, end: date, as_of: date | None = None, earliest: date | None = None) -> Range:
    """A window typed in by hand. Compared with the same number of days
    right before it, like the rolling windows."""
    if end < start:
        raise ValueError("Start date is after end date")
    if as_of is None:
        as_of = today()
    nominal_start = start
    start_trimmed = False
    if earliest and earliest > start:
        start = earliest
        start_trimmed = True
    effective_end = min(end, as_of)
    has_current_data = effective_end >= start
    length = ((effective_end if has_current_data else end) - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)
    # The whole previous window must lie inside the data: one that starts
    # before the first entered day would compare against a shorter span.
    has_previous_data = (earliest is None) or (prev_start >= earliest)
    return Range(
        key="custom",
        label="Custom",
        start=start,
        end=max(end, start),
        effective_end=effective_end,
        has_current_data=has_current_data,
        prev_start=prev_start,
        prev_end=prev_end,
        has_previous_data=has_previous_data,
        bucket=_bucket_for((end - nominal_start).days + 1),
        start_trimmed=start_trimmed,
    )


def all_ranges(as_of: date | None = None, earliest: date | None = None) -> list[dict]:
    """The options to show in the picker. Any option whose resolved window is
    identical to another's (typically because history is shorter than the
    nominal length - 90d/this_year/365d all trimming to the same "all_time"
    window) collapses into a single entry instead of showing duplicates,
    keeping "All time" as that entry's label rather than a misleading
    "90 days" that doesn't actually span 90 days."""
    resolved = {key: resolve(key, as_of=as_of, earliest=earliest) for key, _, _ in RANGE_DEFS}
    windows = {key: (r.start, r.effective_end) for key, r in resolved.items()}
    representative: dict[tuple[date, date], str] = {}
    for key, _, _ in RANGE_DEFS:  # "all_time" is last, so it wins ties within a window group
        if key not in CALENDAR_MONTHS:
            representative[windows[key]] = key
    # Month, quarter and year stay even when they cover the same days as
    # another option: they compare against the previous calendar period, not
    # the days right before.
    kept_keys = set(representative.values()) | set(CALENDAR_MONTHS)
    return [{"key": key, "label": _LABELS[key]} for key, _, _ in RANGE_DEFS if key in kept_keys]
