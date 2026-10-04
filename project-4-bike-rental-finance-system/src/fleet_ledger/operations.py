"""Operations: validated rows linked to a bike and an investor."""
from dataclasses import dataclass

import pandas as pd

from .bikes import Bikes, BikeHistory
from .catalog import Catalog, Wallets
from .investors import InvestorDirectory
from .names import DEPOSIT_RECEIVED, DEPOSIT_RETURNED, RENT_OPERATIONS
from .parse import as_date, has, number, table, text
from .writes import Writes

OPERATION_COLUMNS = ["date", "operation", "wallet_from", "wallet_to", "amount_thb", "amount_cur",
                     "bike", "owner", "owner_id", "owner_name_snapshot", "bike_id", "total"]
# Fields a person types into a row; a row without an operation must leave them empty.
INPUT_FIELDS = ["wallet_from", "wallet_to", "amount_thb", "amount_cur", "bike", "bike_id"]


@dataclass
class Operations:
    frame: pd.DataFrame
    header: int
    columns: dict
    all_dates: list      # dates of every row with a date, drafts included — they define the months


def resolve_bike_link(bike_text, existing_id_text, bikes: pd.DataFrame):
    # bike — what the dropdown shows now (a human-readable name).
    # existing_id_text — the bike_id written by the last run (may be empty).
    # No id yet — find the bike by name (or by its previous name: only rows from before ids
    # need that). An id already set changes only when the name clearly points to ANOTHER
    # existing bike (somebody picked another bike in the dropdown). If no current bike has
    # that name (usually the bike was renamed), the id stays: it, not the name, is the truth.
    # The name is NOT stripped: full_name may end with a space when a bike has no plate.
    bike_text = text(bike_text)
    bike_specified = bool(bike_text.strip())
    existing_id = int(existing_id_text) if text(existing_id_text).strip() else None

    if existing_id is None:
        if not bike_specified:
            return None, False
        matching = bikes.loc[(bikes["full_name"] == bike_text) | (bikes["old_name"] == bike_text)]
        if len(matching) != 1:
            raise ValueError(f"bike {bike_text!r} found {len(matching)} times")
        return int(matching.iloc[0]["id"]), False

    if bikes.loc[bikes["id"] == existing_id].empty:
        return existing_id, True  # no bike with this id in "Bikes" any more
    if not bike_specified:
        return None, False  # the bike cell was cleared — the bike was unlinked on purpose
    by_name = bikes.loc[bikes["full_name"] == bike_text]
    if len(by_name) == 1 and int(by_name.iloc[0]["id"]) != existing_id:
        return int(by_name.iloc[0]["id"]), False  # another bike picked in the dropdown
    return existing_id, False


def load_operations(rows: list[list], catalog: Catalog, wallets: Wallets, bikes: Bikes, history: BikeHistory,
                    investors: InvestorDirectory, writes: Writes) -> Operations:
    # The owner enters only what happened: wallet, amount_thb and, for a non-baht wallet,
    # amount_cur. The rate, baht for an empty amount_thb, deposits and FX are computed later.
    source = table(rows, OPERATION_COLUMNS, "Operations")
    operations, columns = source.frame, source.columns
    operations["operation"] = operations["operation"].map(lambda v: text(v).strip())

    def row_date(record):
        try:
            return as_date(record["date"])
        except ValueError as error:
            raise ValueError(f"Operations, row {record['sheet_row']}: {error}") from error

    all_dates = [row_date(record) for _, record in operations.iterrows() if text(record["date"]).strip()]

    # Drafts (no operation chosen) take no part in the calculation.
    for _, draft in operations.loc[operations["operation"] == ""].iterrows():
        if any(text(draft[name]).strip() for name in INPUT_FIELDS):
            raise ValueError(f"Operations, row {draft['sheet_row']}: no operation selected")
        if text(draft["total"]).strip():
            writes.put("Operations", draft["sheet_row"], columns["total"], "")
        if text(draft["bike_id"]).strip():
            writes.put("Operations", draft["sheet_row"], columns["bike_id"], "")

    operations = operations.loc[operations["operation"] != ""].copy()
    operations["date"] = pd.to_datetime([row_date(record) for _, record in operations.iterrows()])
    for name in ["amount_thb", "amount_cur"]:
        operations[name] = pd.Series([number(v) for v in operations[name]], index=operations.index, dtype=object)
        negative = operations.loc[operations[name].map(lambda v: has(v) and v <= 0), "sheet_row"]
        if not negative.empty:
            raise ValueError(f"Operations, rows {list(negative)}: {name} must be above zero, without a minus.")

    unknown = operations.loc[operations["operation"].map(catalog.signs).isna(), "operation"].unique()
    if len(unknown):
        raise ValueError(f"Add these operations to Operation types: {list(unknown)}")
    operations["from_deposit"] = operations["operation"].isin(catalog.from_deposit)
    operations["owner"] = operations["owner"].map(lambda v: text(v).strip())
    operations["owner_id"] = operations["owner_id"].astype(object)
    operations["wallet_from"] = operations["wallet_from"].map(lambda v: text(v).strip())
    operations["wallet_to"] = operations["wallet_to"].map(lambda v: text(v).strip())
    operations["sign"] = [catalog.row_sign(op["operation"], op["wallet_from"], op["wallet_to"])
                          for _, op in operations.iterrows()]
    # object, not a string dtype: holds either an int id or None.
    operations["bike_id"] = operations["bike_id"].astype(object)

    deposit_operations = {DEPOSIT_RECEIVED, DEPOSIT_RETURNED} | catalog.from_deposit
    missing_bikes = []
    for index, operation in operations.iterrows():
        _check_wallets(operation, catalog, wallets)
        row = operation["sheet_row"]
        try:
            resolved_id, bike_missing = resolve_bike_link(operation["bike"], operation["bike_id"], bikes.frame)
        except ValueError as error:
            raise ValueError(f"Operations, row {row}: {error}")
        if bike_missing:
            missing_bikes.append(row)
            resolved_id = None
        operations.loc[index, "bike_id"] = resolved_id

        if resolved_id is not None:
            bike = bikes.frame.loc[bikes.frame["id"] == resolved_id].iloc[0]
            try:
                terms = history.ownership_at(resolved_id, operation["date"], bikes)
            except ValueError as error:
                raise ValueError(f"Operations, row {row}: {error}")
            operations.loc[index, "owner"] = terms["owner"]
            operations.loc[index, "owner_id"] = terms["owner_id"]
            investors.put(writes, "Operations", row, columns, terms["owner_id"])
            writes.put("Operations", row, columns["bike_id"], resolved_id)
            if operation["operation"] in RENT_OPERATIONS and (not terms["owner"] or pd.isna(terms["owner_share"])):
                raise ValueError(f"Fill owner and owner_share for rent: {bike['full_name']}")
        elif not bike_missing and operation["operation"] in RENT_OPERATIONS:
            raise ValueError(f"Rent without a bike: row {row}")
        elif not bike_missing and operation["operation"] in deposit_operations:
            raise ValueError(f'Operations, row {row}: "{operation["operation"]}" needs a bike — '
                             "deposits are tracked per bike.")

    if missing_bikes:
        raise ValueError(
            f"Operations, rows {', '.join(str(row) for row in missing_bikes)}: the bike with this bike_id "
            'is no longer in "Bikes". Its row seems to have been deleted — restore it, or set the status '
            'to "Sold"/"Retired" instead of deleting the row.'
        )

    # Payouts without a bike keep a permanent link to the investor too.
    for index, operation in operations.loc[operations["bike_id"].isna()].iterrows():
        identity = investors.resolve_record(operation, "Operations", editable=True)
        operations.loc[index, "owner_id"] = identity
        operations.loc[index, "owner"] = investors.name(identity)
        investors.put(writes, "Operations", operation["sheet_row"], columns, identity)
    return Operations(operations, source.header, columns, all_dates)


def _check_wallets(operation, catalog: Catalog, wallets: Wallets) -> None:
    # Income fills wallet_to, an expense comes out of wallet_from, a transfer fills both
    # with a zero total. "From deposit" moves no money: wallet_from only hints the currency.
    source, target = operation["wallet_from"], operation["wallet_to"]
    sign, row, name = operation["sign"], operation["sheet_row"], operation["operation"]
    names = wallets.names
    if operation["from_deposit"]:
        if target or (source and source not in names):
            raise ValueError(f'Operations, row {row}: "{name}" leaves wallet_to empty; '
                             "wallet_from is optional: the wallet holding the deposit.")
    elif name in catalog.two_way and bool(source) == bool(target):
        raise ValueError(f'Operations, row {row}: "{name}" needs exactly one wallet: '
                         "wallet_from for an expense, wallet_to for an income.")
    elif (name == DEPOSIT_RETURNED and source and target
          and wallets.currency.get(source) != wallets.currency.get(target)):
        raise ValueError(f"Operations, row {row}: the deposit is in one currency but was refunded in another — record it "
                         'as two rows on the same day: a "Transfer" from the wallet the money came from to the deposit '
                         f'wallet (both amount_thb and amount_cur), and a "{DEPOSIT_RETURNED}" from the deposit wallet.')
    elif sign == 1 and (source or target not in names):
        raise ValueError(f"Operations, row {row}: an income fills only wallet_to.")
    elif sign == -1 and (target or source not in names):
        raise ValueError(f"Operations, row {row}: an expense fills only wallet_from.")
    elif sign == 0 and (source not in names or target not in names or source == target):
        raise ValueError(f"Operations, row {row}: a transfer needs two different wallets.")
