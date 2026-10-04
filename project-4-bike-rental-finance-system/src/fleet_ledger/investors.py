"""Investors: stable owner_id, editable names, settlements. Shared by the recalculation and the dashboard."""
from decimal import Decimal, InvalidOperation

import pandas as pd

from .parse import as_date, number, table, text
from .writes import Writes

SETTLED_ON, SETTLED_DUE = "Settled on", "Due at settlement"
NAME = "Name"
# Sheets that refer to investors by owner_id; a new id must follow every id ever used there.
LINKED_SHEETS = ["Investors", "Bikes", "Operations", "Bike history", "Investor settlement"]


def label(value):
    if value is None or str(value) in ("nan", "<NA>"):
        return ""
    return str(value).strip()


def investor_id(value):
    if not label(value):
        return None
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite() or parsed <= 0 or parsed != parsed.to_integral_value():
            raise InvalidOperation
        return int(parsed)
    except (InvalidOperation, ValueError):
        raise ValueError(f"An investor ID must be a positive whole number: {value!r}.") from None


class InvestorDirectory:
    def __init__(self, records):
        self.names = {}
        self.by_name = {}
        for record in records:
            name = label(record.get(NAME))
            raw_id = record.get("owner_id")
            if not name and not label(raw_id):
                continue
            identity = investor_id(raw_id)
            if identity is None or not name:
                raise ValueError("Investors: every investor needs a permanent owner_id and a Name.")
            if identity in self.names:
                raise ValueError(f"Investors: owner_id {identity} is repeated.")
            key = name.casefold()
            if key in self.by_name:
                raise ValueError(f'Investors: the name "{name}" is repeated. Names to pick from must differ.')
            self.names[identity] = name
            self.by_name[key] = identity
        if not self.names:
            raise ValueError('Fill in "Investors": owner_id and Name.')

    def name(self, identity):
        identity = investor_id(identity)
        if identity is None:
            return ""
        if identity not in self.names:
            raise ValueError(f'Investor owner_id {identity} is not in "Investors". Restore the row with that owner_id; the name may change.')
        return self.names[identity]

    def resolve(self, name, identity=None, previous_name=None, editable=False):
        """Editable labels distinguish a selection from a directory rename.

        An unchanged saved label follows its ID even if that label has since
        been assigned to somebody else. Historical records always follow ID.
        """
        name = label(name)
        identity = investor_id(identity)
        if identity is not None:
            current = self.name(identity)  # removed/reused IDs must not be guessed
            if not editable:
                return identity
            if not name:
                return None
            if name == label(previous_name) or name == current:
                return identity
        elif not name:
            return None
        selected = self.by_name.get(name.casefold())
        if selected is None:
            raise ValueError(f'Investor "{name}" is not in "Investors". Pick a name from the list; rename investors in "Investors" itself.')
        return selected

    def resolve_record(self, record, sheet: str, editable: bool = False):
        try:
            return self.resolve(record.get("owner", ""), record.get("owner_id"),
                                record.get("owner_name_snapshot"), editable=editable)
        except ValueError as error:
            raise ValueError(f"{sheet}, row {record['sheet_row']}: {error}") from error

    def put(self, writes: Writes, sheet: str, row: int, columns: dict, identity) -> None:
        """Writes the canonical name and id of an investor into a row."""
        name = self.name(identity)
        writes.put(sheet, row, columns["owner"], name)
        writes.put(sheet, row, columns["owner_id"], identity)
        if "owner_name_snapshot" in columns:
            writes.put(sheet, row, columns["owner_name_snapshot"], name)


def assign_investor_ids(directory_rows: pd.DataFrame, id_column: int, values: dict, writes: Writes) -> None:
    # A new investor (Name set, owner_id empty) gets the next id after every id written in the
    # sheet. A deleted investor's id is never reused: their bikes, history and payouts would
    # silently move to another person.
    pending = [index for index, record in directory_rows.iterrows()
               if text(record[NAME]).strip() and not text(record["owner_id"]).strip()]
    if not pending:
        return
    used_ids = set()
    for sheet in LINKED_SHEETS:
        for value in table(values[sheet], ["owner_id"], sheet).frame["owner_id"]:
            try:
                identity = investor_id(value)
            except ValueError:
                continue  # a bad owner_id stops the calculation later, with its row address
            if identity is not None:
                used_ids.add(identity)
    next_id = max(used_ids, default=0) + 1
    for index in pending:
        directory_rows.at[index, "owner_id"] = next_id
        writes.put("Investors", directory_rows.at[index, "sheet_row"], id_column, next_id)
        next_id += 1


def read_settlements(records, today: pd.Timestamp) -> dict:
    # A settlement closes an investor's account on a date, for when early history is
    # incomplete: operations up to that date are replaced by "Due at settlement" (empty — 0).
    settlements = {}
    for record in records:
        settled_text = text(record.get(SETTLED_ON, "")).strip()
        due_text = text(record.get(SETTLED_DUE, "")).strip()
        if not settled_text and not due_text:
            continue
        where = f"Investors, row {record['sheet_row']}"
        if not settled_text:
            raise ValueError(f'{where}: "{SETTLED_DUE}" is filled without a date in "{SETTLED_ON}".')
        identity = investor_id(record.get("owner_id"))
        if identity is None:
            raise ValueError(f"{where}: a settlement without the investor's owner_id.")
        try:
            settled_on = as_date(record[SETTLED_ON])
            settled_due = number(record.get(SETTLED_DUE)) or 0.0
        except ValueError as error:
            raise ValueError(f"{where}: {error}") from error
        if settled_on > today:
            raise ValueError(f"{where}: settlement date {settled_on:%d.%m.%Y} is after today.")
        settlements[identity] = {"date": settled_on, "due": settled_due}
    return settlements


def load_investors(values: dict, writes: Writes, today: pd.Timestamp):
    """Directory with ids assigned to new investors, and their settlements."""
    rows = table(values["Investors"], ["owner_id", NAME], "Investors")
    assign_investor_ids(rows.frame, rows.columns["owner_id"], values, writes)
    directory = InvestorDirectory(rows.frame.to_dict("records"))
    return directory, read_settlements(rows.frame.to_dict("records"), today)
