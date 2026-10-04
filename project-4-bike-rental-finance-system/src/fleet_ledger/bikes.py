"""Bikes with permanent ids, and the history of their owners and shares."""
from dataclasses import dataclass, field

import pandas as pd

from .investors import InvestorDirectory
from .parse import as_date, as_datetime, number, table, text
from .writes import Writes

BIKE_COLUMNS = ["id", "full_name", "brand", "model", "color", "capacity", "licence_plate",
                "owner", "owner_id", "owner_name_snapshot", "owner_share", "repair_share", "purchase_price", "roi"]
HISTORY_COLUMNS = ["bike_id", "owner", "owner_id", "owner_share", "active_from"]
SHARE_FORMAT = {"type": "PERCENT", "pattern": "0%"}


@dataclass
class Bikes:
    frame: pd.DataFrame
    columns: dict

    def label(self, bike_id) -> str:
        # For error messages: "name (id)"; just the id if it is no longer valid.
        names = dict(zip(self.frame["id"], self.frame["full_name"]))
        name = names.get(bike_id)
        return f"{name.strip()} ({bike_id})" if name else str(bike_id)


def load_bikes(values: dict, investors: InvestorDirectory, writes: Writes) -> Bikes:
    source = table(values["Bikes"], BIKE_COLUMNS, "Bikes")
    bikes, columns = source.frame, source.columns
    # Same as the sheet formula, including spaces between empty parts.
    bikes["old_name"] = bikes["full_name"]
    bikes["full_name"] = [" ".join(text(bike[name]) for name in ["brand", "model", "color", "capacity", "licence_plate"])
                          for _, bike in bikes.iterrows()]
    bikes = bikes.loc[bikes["full_name"].str.strip() != ""].copy()
    bikes["owner_id"] = pd.Series(
        [investors.resolve_record(row, "Bikes", editable=True) for _, row in bikes.iterrows()],
        index=bikes.index, dtype=object)
    bikes["owner"] = bikes["owner_id"].map(investors.name)
    for _, bike in bikes.iterrows():
        investors.put(writes, "Bikes", bike["sheet_row"], columns, bike["owner_id"])
    for name in ["owner_share", "repair_share", "purchase_price"]:
        bikes[name] = bikes[name].map(number)
    if ((bikes["owner_share"] < 0) | (bikes["owner_share"] > 1)).any():
        raise ValueError("owner_share must be between 0 and 1.")
    if ((bikes["repair_share"] < 0) | (bikes["repair_share"] > 1)).any():
        raise ValueError("repair_share must be between 0 and 1.")
    if (bikes["purchase_price"] < 0).any():
        raise ValueError("A bike's purchase price cannot be negative.")

    # id is the bike's permanent number: unlike full_name it survives renames, a sale
    # (status "Sold"/"Retired") and row reordering. Operations refer to bikes by id.
    # Existing ids are never changed — only empty ones are filled.
    raw_ids = bikes["id"].map(lambda v: text(v).strip())
    existing_ids = raw_ids.loc[raw_ids != ""].map(int)
    if existing_ids.duplicated().any():
        raise ValueError("Bikes: an id is repeated. This column must not be edited by hand.")
    # A deleted bike's id is never reused: operations and history still point at it and
    # would silently move to the new bike. So the maximum is over every sheet with bike_id.
    used_ids = {int(value) for value in existing_ids}
    for sheet in ["Operations", "Bike history"]:
        for value in table(values[sheet], ["bike_id"], sheet).frame["bike_id"]:
            try:
                linked_id = number(value)
            except ValueError:
                continue  # a bad bike_id is reported by the history and operations checks
            if linked_id is not None and linked_id.is_integer():
                used_ids.add(int(linked_id))
    next_id = max(used_ids, default=0) + 1
    bike_ids = []
    for raw in raw_ids:
        if raw == "":
            bike_ids.append(next_id)
            next_id += 1
        else:
            bike_ids.append(int(raw))
    bikes["id"] = bike_ids
    for _, bike in bikes.iterrows():
        writes.put("Bikes", bike["sheet_row"], columns["id"], bike["id"])
    return Bikes(bikes, columns)


def same_value(old, new) -> bool:
    if pd.isna(old) or pd.isna(new):
        return pd.isna(old) and pd.isna(new)
    return abs(old - new) < 0.000000001


@dataclass
class BikeHistory:
    """Who owned each bike and on which terms (owner_share, repair_share), from when.

    The sheet is hidden and only appended to. Existing rows may get only the investor's
    current name and id, and a repair_share where the cell is still empty.
    """
    frame: pd.DataFrame            # sheet_row, bike_id, owner, owner_id, owner_share, repair_share, active_from
    columns: dict
    appends: list = field(default_factory=list)          # new rows, appended by the write step
    allowed_writes: dict = field(default_factory=dict)   # (row, column) -> the only value it may get
    warnings: list = field(default_factory=list)

    def ownership_at(self, bike_id, day, bikes: Bikes):
        # Operations carry only a date: the last terms recorded on that day apply to all of it.
        history = self.frame
        next_day = as_date(day) + pd.Timedelta(days=1)
        found = history.loc[(history["bike_id"] == bike_id) & (history["active_from"] < next_day)]
        if found.empty:
            own = history.loc[history["bike_id"] == bike_id]
            if own.empty:
                raise ValueError(f"Bike {bikes.label(bike_id)} has no owner in its history on {day:%d.%m.%Y}.")
            # History exists but starts after the operation: usually a typo in the operation date.
            first = own["active_from"].min()
            bought = ""
            if "purchase_date" in bikes.frame:
                # tolist() gives plain Python numbers: as_date does not take numpy numbers.
                purchase = bikes.frame.loc[bikes.frame["id"] == bike_id, "purchase_date"].tolist()
                try:
                    bought = f"purchased {as_date(purchase[0]):%d.%m.%Y}, "
                except (IndexError, ValueError):
                    pass
            raise ValueError(
                f"Bike {bikes.label(bike_id)}: an operation on {day:%d.%m.%Y} is before its ownership history starts "
                f"({bought}history from {first:%d.%m.%Y}). Check the operation date. If you had the bike earlier, "
                f'enter the real purchase date in "Bikes" → purchase_date.'
            )
        return found.sort_values(["active_from", "sheet_row"]).iloc[-1]


def load_history(values: dict, bikes: Bikes, investors: InvestorDirectory, run_at: pd.Timestamp,
                 writes: Writes) -> BikeHistory:
    sheet = "Bike history"
    source = table(values[sheet], HISTORY_COLUMNS, sheet)
    columns, allowed = dict(source.columns), {}
    if "repair_share" not in columns:
        # repair_share got its history on 04.10.2026: the column is added once, to the right.
        columns["repair_share"] = max(columns.values()) + 1
        allowed[(source.header, columns["repair_share"])] = "repair_share"
        writes.put(sheet, source.header, columns["repair_share"], "repair_share")
    current_repair = dict(zip(bikes.frame["id"], bikes.frame["repair_share"]))
    rows = []
    for _, record in source.frame.iterrows():
        bike_id = number(record["bike_id"])
        if bike_id is None or bike_id <= 0 or not bike_id.is_integer():
            raise ValueError(f"Bike history, row {record['sheet_row']}: bike_id must be a whole number.")
        if not text(record["active_from"]).strip():
            raise ValueError(f"Bike history, row {record['sheet_row']}: active_from date is required.")
        share = number(record["owner_share"])
        if share is not None and not 0 <= share <= 1:
            raise ValueError(f"Bike history, row {record['sheet_row']}: owner_share must be between 0 and 1.")
        repair = number(record.get("repair_share", ""))
        if repair is not None and not 0 <= repair <= 1:
            raise ValueError(f"Bike history, row {record['sheet_row']}: repair_share must be between 0 and 1.")
        if repair is None and not pd.isna(current_repair.get(int(bike_id), float("nan"))):
            # Rows from before repair_share had history: the bike's current value is the one
            # every calculation used so far. Filled once; a filled cell never changes.
            repair = float(current_repair[int(bike_id)])
            allowed[(record["sheet_row"], columns["repair_share"])] = repair
            writes.put(sheet, record["sheet_row"], columns["repair_share"], repair, number_format=SHARE_FORMAT)
        active_from = as_datetime(record["active_from"])
        if active_from > run_at:
            raise ValueError(f"Bike history, row {record['sheet_row']}: date and time are after the run.")
        identity = investors.resolve_record(record, "Bike history")
        canonical_name = investors.name(identity)
        for name, value in (("owner", canonical_name), ("owner_id", identity)):
            allowed[(record["sheet_row"], columns[name])] = "" if value is None else value
            writes.put(sheet, record["sheet_row"], columns[name], value)
        rows.append({"sheet_row": record["sheet_row"], "bike_id": int(bike_id),
                     "owner": canonical_name, "owner_id": identity, "owner_share": share,
                     "repair_share": repair, "active_from": active_from})
    frame = pd.DataFrame(rows, columns=["sheet_row"] + HISTORY_COLUMNS + ["repair_share"])
    history = BikeHistory(frame, columns, allowed_writes=allowed)

    first_operation = _first_operations(values["Operations"], bikes)
    frame_bikes = bikes.frame
    earlier_than_purchase = []
    for _, bike in frame_bikes.iterrows():
        bike_id = int(bike["id"])
        current_share, current_repair_share = bike["owner_share"], bike["repair_share"]
        previous = frame.loc[frame["bike_id"] == bike_id].sort_values(["active_from", "sheet_row"])
        if previous.empty:
            purchase = bike.get("purchase_date", "")
            start = as_date(purchase) if text(purchase).strip() else pd.Timestamp("1900-01-01")
            if bike_id in first_operation and first_operation[bike_id] < start:
                earlier_than_purchase.append(bike_id)
                start = first_operation[bike_id]
            reason = "first record"
        else:
            latest = previous.iloc[-1]
            same_owner = (pd.isna(latest["owner_id"]) and pd.isna(bike["owner_id"])) or latest["owner_id"] == bike["owner_id"]
            same_terms = (same_value(latest["owner_share"], current_share)
                          and same_value(latest["repair_share"], current_repair_share))
            if same_owner and same_terms:
                continue
            start = run_at
            reason = "change"
        history.appends.append({
            "sheet_row": len(values["Bike history"]) + len(history.appends) + 1,
            "bike_id": bike_id, "owner": bike["owner"], "owner_id": bike["owner_id"],
            "owner_share": None if pd.isna(current_share) else current_share,
            "repair_share": None if pd.isna(current_repair_share) else current_repair_share,
            "active_from": start, "reason": reason,
        })

    # Calculations already see the rows to append; Google gets them only at the write step.
    if history.appends:
        history.frame = pd.concat([frame, pd.DataFrame(history.appends)], ignore_index=True)
    if earlier_than_purchase:
        history.warnings.append(f"Purchase date is after existing operations for bike_id: {earlier_than_purchase}. "
                                "The first history row starts at the earliest operation instead.")

    # "Bike history" is hidden and only appended to, so an earlier purchase date comes from
    # "Bikes": the first history row starts no later than purchase_date. The old row in Google
    # stays as it is — the shift applies to the calculation.
    if "purchase_date" in frame_bikes:
        for bike_id, purchase in zip(frame_bikes["id"].tolist(), frame_bikes["purchase_date"].tolist()):
            if not text(purchase).strip():
                continue
            own = history.frame.loc[history.frame["bike_id"] == bike_id].sort_values(["active_from", "sheet_row"])
            if not own.empty and as_date(purchase) < own.iloc[0]["active_from"]:
                history.frame.at[own.index[0], "active_from"] = as_date(purchase)
    return history


def _first_operations(rows: list[list], bikes: Bikes) -> dict:
    # Earliest operation of each bike, in case its purchase date was filled in after a rent
    # had already been recorded. By bike_id, and by name for rows from before ids.
    first_operation = {}
    frame = bikes.frame
    for _, operation in table(rows, ["date", "operation", "bike", "bike_id"], "Operations").frame.iterrows():
        if not text(operation["operation"]).strip() or not text(operation["date"]).strip():
            continue
        if text(operation["bike_id"]).strip():
            bike_id = int(text(operation["bike_id"]))
        else:
            bike_name = text(operation["bike"])
            matching = frame.loc[(frame["full_name"] == bike_name) | (frame["old_name"] == bike_name)]
            if len(matching) != 1:
                continue  # the operations check reports a wrong bike name separately
            bike_id = int(matching.iloc[0]["id"])
        try:
            operation_date = as_date(operation["date"])
        except ValueError as error:
            raise ValueError(f"Operations, row {operation['sheet_row']}: {error}") from error
        if bike_id not in first_operation or operation_date < first_operation[bike_id]:
            first_operation[bike_id] = operation_date
    return first_operation
