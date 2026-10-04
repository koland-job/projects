"""Directories: operation types, wallets with their currencies, opening balances and deposits."""
from dataclasses import dataclass

import pandas as pd

from .names import (BASE_CURRENCY, CURRENCY_SYMBOLS, DAMAGE_WITHHOLDING, DEPOSIT_RECEIVED, DEPOSIT_RENT,
                    DEPOSIT_RETURNED, TWO_WAY_TYPE, TYPE_SIGN)
from .parse import as_date, find_label, number, table, text


@dataclass
class Catalog:
    signs: dict          # operation -> +1 income, -1 expense, 0 transfer
    two_way: set         # "Income/Expense": the wallet decides the direction
    from_deposit: set    # income taken from a bike's deposit: no wallet moves

    def row_sign(self, operation, wallet_from, wallet_to):
        # An "Income/Expense" row is income when only wallet_to is filled.
        if operation in self.two_way and wallet_to and not wallet_from:
            return 1
        return self.signs.get(operation)


def load_catalog(rows: list[list]) -> Catalog:
    directory = table(rows, ["Name", "Type"], "Operation types").frame
    directory["Name"] = directory["Name"].map(lambda v: text(v).strip())
    directory["Type"] = directory["Type"].map(lambda v: text(v).strip().casefold())
    directory = directory.loc[directory["Name"] != ""].copy()
    if directory["Name"].duplicated().any():
        raise ValueError("Operation types: an operation is listed twice.")
    directory["sign"] = directory["Type"].map(TYPE_SIGN)
    if directory["sign"].isna().any():
        raise ValueError("Operation type must be Income, Expense, Income/Expense or Transfer.")
    signs = dict(zip(directory["Name"], directory["sign"]))

    from_deposit = {DEPOSIT_RENT, DAMAGE_WITHHOLDING} & set(signs)
    for name in from_deposit:
        if signs[name] != 1:
            raise ValueError(f'Operation types: "{name}" must have type Income.')
    for name, sign in ((DEPOSIT_RECEIVED, 1), (DEPOSIT_RETURNED, -1)):
        if signs.get(name) != sign:
            raise ValueError(f'Operation types: an operation "{name}" of type '
                             f"{'Income' if sign == 1 else 'Expense'} is required.")
    # "Income/Expense": wallet_from is an expense, wallet_to an income. Fuel, for example:
    # we filled up — expense; a client returned the bike without a full tank and paid — income.
    two_way = set(directory.loc[directory["Type"] == TWO_WAY_TYPE, "Name"])
    return Catalog(signs, two_way, from_deposit)


def currency_from_name(wallet: str):
    found = {code for symbol, code in CURRENCY_SYMBOLS.items() if symbol in wallet}
    return found.pop() if len(found) == 1 else None


@dataclass
class Wallets:
    currency: dict             # wallet -> currency code
    balances: pd.DataFrame     # wallet, start_date, balance, currency
    deposits: pd.DataFrame     # opening deposits: wallet, start_date, balance, currency

    @property
    def names(self) -> set:
        return set(self.currency)

    @property
    def foreign_currencies(self) -> list:
        return sorted(set(self.currency.values()) - {BASE_CURRENCY})

    @property
    def start(self) -> dict:
        return dict(zip(self.balances["wallet"], self.balances["start_date"]))

    @property
    def deposit_start(self) -> dict:
        return dict(zip(self.deposits["wallet"], self.deposits["start_date"]))

    @property
    def deposit_start_date(self) -> pd.Timestamp:
        return self.deposits["start_date"].min()


def load_wallets(values: dict, today: pd.Timestamp) -> Wallets:
    frame = table(values["Wallets"], ["Wallet"], "Wallets").frame
    frame["Wallet"] = frame["Wallet"].map(lambda v: text(v).strip())
    frame = frame.loc[frame["Wallet"] != ""].copy()
    if frame["Wallet"].duplicated().any():
        raise ValueError("Wallets: a wallet is listed twice.")
    frame["Currency"] = frame["Wallet"].map(currency_from_name)
    bad_currency = frame.loc[frame["Currency"].isna(), "Wallet"]
    if not bad_currency.empty:
        raise ValueError("Wallets: a wallet name must contain exactly one currency symbol "
                         f"({' '.join(CURRENCY_SYMBOLS)}). None or several in: {list(bad_currency)}.")
    currency = dict(zip(frame["Wallet"], frame["Currency"]))
    balances, deposits = _starting_balances(values["Opening balances"], currency, today)
    return Wallets(currency, balances, deposits)


def _starting_balances(rows: list[list], currency: dict, today: pd.Timestamp):
    # Amounts are at the start of the date; operations of that day already count.
    # A wallet's amount is in its own currency. Its operations before the start date are
    # inside that amount: they stay out of the balance but still go to P&L and revenue.
    header, wallet_column = find_label(rows, "Wallet", "Opening balances")
    header_row = rows[header - 1]

    def start_column(prefix):
        # Nearest column right of "Wallet" whose title starts with prefix.
        found = [column for column, value in enumerate(header_row, start=1)
                 if column > wallet_column and " ".join(text(value).casefold().split()).startswith(prefix)]
        if not found:
            raise ValueError(f'Opening balances: a "{prefix.capitalize()}…" column is needed right of "Wallet".')
        return found[0]

    date_column, balance_column = start_column("start date"), start_column("amount")
    deposit_columns = [column for column, value in enumerate(header_row, start=1)
                       if " ".join(text(value).strip().casefold().split()).startswith("deposits")]
    if len(deposit_columns) != 1:
        raise ValueError('Opening balances: the wallet header row needs exactly one "Deposits" column.')
    deposit_column = deposit_columns[0]

    def value(row, column):
        return row[column - 1] if len(row) >= column else ""

    wallet_records = []
    for row_number in range(header + 1, len(rows) + 1):
        row = rows[row_number - 1]
        wallet = text(value(row, wallet_column)).strip()
        if not wallet:
            continue
        try:
            wallet_records.append({"wallet": wallet, "start_date": as_date(value(row, date_column)),
                                   "balance": number(value(row, balance_column))})
        except ValueError as error:
            raise ValueError(f"Opening balances, row {row_number}: {error}") from error
    balances = pd.DataFrame(wallet_records, columns=["wallet", "start_date", "balance"])
    if balances.empty:
        raise ValueError("Fill in Wallets and Opening balances.")
    if balances["wallet"].duplicated().any() or set(balances["wallet"]) != set(currency):
        raise ValueError("Opening balances: every wallet from Wallets needs exactly one row.")
    if balances[["start_date", "balance"]].isna().any().any():
        raise ValueError("Opening balances: a wallet's date and amount are required; enter zero as the number 0.")
    balances["currency"] = balances["wallet"].map(currency)
    wallet_start = dict(zip(balances["wallet"], balances["start_date"]))

    # Opening deposits — the "Deposits" column, in the wallet's currency and at its start date.
    # They are owed to clients, not extra money in the wallet. Not tied to a bike yet;
    # returns may draw on this balance in the same currency.
    deposit_records = []
    for row_number in range(header + 1, len(rows) + 1):
        row = rows[row_number - 1]
        wallet = text(value(row, wallet_column)).strip()
        if not wallet:
            continue
        start = wallet_start[wallet]
        amount = number(row[deposit_column - 1]) if len(row) >= deposit_column else None
        if start > today:
            raise ValueError(f"Opening balances, row {row_number}: the start date of a wallet with deposits must not be after today.")
        if amount is None or amount < 0:
            raise ValueError(f"Opening balances, row {row_number}: deposits amount must be 0 or more; fill an empty cell with zero.")
        deposit_records.append({"wallet": wallet, "start_date": start, "balance": amount, "currency": currency[wallet]})
    deposits = pd.DataFrame(deposit_records, columns=["wallet", "start_date", "balance", "currency"])
    return balances, deposits
