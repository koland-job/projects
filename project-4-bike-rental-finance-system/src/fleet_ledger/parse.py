"""Reading sheet cells: text, numbers, dates and tables found by their header row."""
from dataclasses import dataclass

import pandas as pd

SHEET_EPOCH = pd.Timestamp("1899-12-30")  # day 0 of Google Sheets date serials


def text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        if float(value).is_integer():
            return str(int(value))
    return str(value)


def has(value) -> bool:
    return value is not None and not pd.isna(value)


def number(value):
    if value is None or value == "":
        return None
    # Comma as the decimal separator, no thousands separator. A format like
    # "1,234.56" in the sheet would need this function revisited.
    cleaned = text(value).replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        result = float(cleaned)
    except ValueError:
        raise ValueError(f"Invalid number: {value!r} (after cleaning: {cleaned!r})")
    if pd.isna(result) or abs(result) == float("inf"):
        raise ValueError(f"Invalid number: {value!r}")
    return result


def as_datetime(value) -> pd.Timestamp:
    if isinstance(value, bool) or not text(value).strip():
        raise ValueError(f"A valid date is required: {value!r}")
    if isinstance(value, (int, float)):
        result = pd.to_datetime(value, unit="D", origin=SHEET_EPOCH)
    elif text(value).strip()[:4].isdigit() and text(value).strip()[4:5] == "-":
        # Year first ("2026-10-04"): with dayfirst pandas would swap month and day.
        result = pd.to_datetime(text(value).strip(), format="ISO8601", errors="raise")
    else:
        result = pd.to_datetime(value, dayfirst=True, errors="raise")
    if pd.isna(result) or result.tzinfo is not None:
        raise ValueError(f"A date without a time zone is required: {value!r}")
    return result


def as_date(value) -> pd.Timestamp:
    return as_datetime(value).normalize()


def serial(day: pd.Timestamp) -> float:
    """Google Sheets date serial: whole days, time as a fraction."""
    return (day - SHEET_EPOCH).total_seconds() / 86400


def same_label(value, label: str) -> bool:
    return " ".join(text(value).strip().casefold().split()) == " ".join(label.casefold().split())


@dataclass
class Table:
    frame: pd.DataFrame   # one row per non-empty sheet row, plus "sheet_row"
    header: int           # 1-based row of the header
    columns: dict         # title -> 1-based column


def table(rows: list[list], required: list[str], sheet: str) -> Table:
    found = []
    for row, values in enumerate(rows, start=1):
        names = [text(value).strip() for value in values]
        if set(required).issubset(names):
            found.append((row, names))
    if len(found) != 1:
        raise ValueError(f"{sheet}: exactly one header row {required} is required")
    header, names = found[0]
    named = [name for name in names if name]
    if len(named) != len(set(named)):
        raise ValueError(f"{sheet}: column titles are repeated")
    columns = {name: column for column, name in enumerate(names, start=1) if name}
    records = []
    for row in range(header + 1, len(rows) + 1):
        record = {"sheet_row": row}
        for title, column in columns.items():
            record[title] = rows[row - 1][column - 1] if column <= len(rows[row - 1]) else ""
        if any(text(record[title]).strip() for title in columns):
            records.append(record)
    return Table(pd.DataFrame(records, columns=["sheet_row"] + list(columns)), header, columns)


def find_label(rows: list[list], label: str, sheet: str) -> tuple[int, int]:
    found = [(row, column)
             for row, values in enumerate(rows, start=1)
             for column, value in enumerate(values, start=1) if same_label(value, label)]
    if len(found) != 1:
        raise ValueError(f"{sheet}: label {label!r} found {len(found)} times")
    return found[0]


def cell(rows: list[list], row: int, column: int):
    """Value at a 1-based address, "" outside the read rectangle."""
    values = rows[row - 1] if 0 < row <= len(rows) else []
    return values[column - 1] if 0 < column <= len(values) else ""
