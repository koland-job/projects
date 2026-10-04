import threading
from datetime import date
from functools import wraps

import pandas as pd
from fleet_ledger.investors import NAME, InvestorDirectory, label
from fleet_ledger.parse import as_date, number, table, text

from . import config
from .sheets_client import get_sheet_rows

FREE_CASH_TOTAL = "TOTAL FREE:"

_memo_lock = threading.Lock()
_memo: dict[str, tuple[object, object]] = {}


def _parsed_once(sheet_name: str):
    """Parses a sheet once per fetched snapshot instead of on every request:
    the result is reused for as long as the cached raw rows are the same
    object, and recomputed as soon as a fresh snapshot replaces them.
    Callers must treat the returned frame as read-only."""

    def decorator(fn):
        @wraps(fn)
        def wrapper():
            rows = get_sheet_rows(sheet_name)
            with _memo_lock:
                hit = _memo.get(fn.__name__)
                if hit is not None and hit[0] is rows:
                    return hit[1]
            result = fn()
            with _memo_lock:
                _memo[fn.__name__] = (rows, result)
            return result

        return wrapper

    return decorator


def _table(rows: list[list], required: list[str]) -> pd.DataFrame:
    return table(rows, required, "Sheet").frame


def _number_or_none(value):
    try:
        return number(value)
    except ValueError:
        return None


def _dates(values: pd.Series) -> pd.Series:
    """Vectorised as_date(): sheet serial numbers and dd.mm.yyyy strings."""
    is_serial = values.map(lambda v: isinstance(v, (int, float)) and not isinstance(v, bool))
    out = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    if is_serial.any():
        out[is_serial] = pd.to_datetime(values[is_serial].astype(float), unit="D", origin="1899-12-30").dt.normalize()
    if (~is_serial).any():
        # An empty date stays empty here; the recalculation reports it with its row.
        out[~is_serial] = values[~is_serial].map(lambda v: as_date(v) if text(v).strip() else pd.NaT)
    return out


@_parsed_once("Investors")
def load_investor_directory():
    rows = get_sheet_rows("Investors")
    records = _table(rows, ["owner_id", NAME]).to_dict("records")
    # The recalculation gives a new investor an owner_id; until then nothing refers to them.
    return InvestorDirectory([record for record in records if label(record.get("owner_id"))])


@_parsed_once("Bikes")
def load_bikes() -> pd.DataFrame:
    rows = get_sheet_rows("Bikes")
    df = _table(rows, ["id", "full_name", "owner", "status", "purchase_price"])
    df = df.loc[df["id"].map(lambda v: text(v).strip() != "")].copy()
    df["id"] = df["id"].map(lambda v: int(number(v)))
    df["full_name"] = df["full_name"].map(lambda v: text(v).strip())
    df["status"] = df["status"].map(lambda v: text(v).strip())
    directory = load_investor_directory()
    df["owner"] = [directory.name(directory.resolve(
        row["owner"], row.get("owner_id"), row.get("owner_name_snapshot"), editable=True
    )) for _, row in df.iterrows()]
    df["purchase_price"] = df["purchase_price"].map(number)
    # ROI is computed by the recalculation into the sheet; the dashboard only shows it.
    df["roi"] = df["roi"].map(_number_or_none) if "roi" in df else None
    return df


@_parsed_once("Operations")
def load_operations() -> pd.DataFrame:
    rows = get_sheet_rows("Operations")
    df = _table(rows, ["date", "operation", "bike_id", "total"])
    df["operation"] = df["operation"].map(lambda v: text(v).strip())
    df = df.loc[df["operation"] != ""].copy()
    df["date"] = _dates(df["date"])
    df["total"] = df["total"].map(number).fillna(0.0)
    df["bike_id"] = df["bike_id"].map(lambda v: int(number(v)) if text(v).strip() else None)
    return df


@_parsed_once("Free cash today")
def load_free_money() -> dict:
    rows = get_sheet_rows("Free cash today")
    label_col = None
    amount_col = None
    for r in rows:
        stripped = [text(v).strip() for v in r]
        if "Amount" in stripped:
            amount_col = stripped.index("Amount") + 1
            label_col = amount_col - 1
            break
    if amount_col is None:
        raise ValueError('Tab "Free cash today" has no "Amount" header')

    values = {}
    for r in rows:
        label = text(r[label_col - 1]).strip() if label_col - 1 < len(r) else ""
        if not label:
            continue
        raw = r[amount_col - 1] if amount_col - 1 < len(r) else ""
        try:
            values[label] = number(raw)
        except ValueError:
            continue

    target = FREE_CASH_TOTAL
    if target not in values:
        raise ValueError(
            f'Tab "Free cash today" has no "{target}" row — '
            "it seems to have been renamed or deleted."
        )
    return values


@_parsed_once("Investor settlement")
def load_investor_dues() -> list[dict]:
    """What each investor is owed right now (sheet column to_be_paid).
    Negative means they have been paid ahead of what their bikes earned."""
    rows = get_sheet_rows("Investor settlement")
    df = _table(rows, ["owner", "to_be_paid"])
    directory = load_investor_directory()
    out = []
    for _, row in df.iterrows():
        owner = directory.name(directory.resolve(row["owner"], row.get("owner_id")))
        if not owner:
            continue
        out.append({"owner": owner, "to_be_paid": number(row["to_be_paid"]) or 0.0})
    return out


def bike_status_counts() -> dict:
    bikes = load_bikes()
    order = ["Rented", "Reserved", "Available", "Idle", "In repair", "For sale", "Sold/Retired"]
    counts = bikes["status"].value_counts().to_dict()
    ordered = [{"status": s, "count": counts.pop(s)} for s in order if s in counts]
    # Any status not in our known order still shows up, appended at the end.
    ordered += [{"status": s, "count": c} for s, c in counts.items()]
    return {"total": len(bikes), "by_status": ordered}


def data_boundaries(today: date) -> tuple[date | None, date | None]:
    """(earliest, latest<=today) operation dates. None, None if there are no operations yet."""
    ops = load_operations()
    if ops.empty:
        return None, None
    earliest = ops["date"].min().date()
    not_future = ops.loc[ops["date"] <= pd.Timestamp(today), "date"]
    latest = not_future.max().date() if not not_future.empty else None
    return earliest, latest
