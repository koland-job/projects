"""Names the code depends on: tab names and operation names that decide where a row lands in reports."""
import json
import os


# Tabs the recalculation reads and writes. A tab is found by its title; to survive a rename,
# set the environment variable SHEET_IDS to a JSON object {"tab name": numeric sheet id}
# for the tabs that should be looked up by id instead.
SHEET_NAMES = [
    "Bikes",
    "Investors",
    "Operations",
    "Operation types",
    "Wallets",
    "Opening balances",
    "Bike history",
    "Staff",
    "Fixed costs",
    "Free cash today",
    "Investor settlement",
    "Month start days",
    "P&L",
    "Cash flow",
]


def sheet_id_overrides(raw: str | None = None) -> dict[str, int]:
    """Optional tab-id overrides from SHEET_IDS (a JSON object); empty when the variable is unset."""
    raw = os.environ.get("SHEET_IDS", "") if raw is None else raw
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"SHEET_IDS is not valid JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ValueError('SHEET_IDS must be a JSON object like {"Tab name": 123}.')
    unknown = [name for name in parsed if name not in SHEET_NAMES]
    if unknown:
        raise ValueError(f"SHEET_IDS names unknown tabs: {', '.join(unknown)}")
    bad = [name for name, value in parsed.items() if not isinstance(value, int) or isinstance(value, bool)]
    if bad:
        raise ValueError(f"SHEET_IDS values must be integers: {', '.join(bad)}")
    return parsed


def resolve_sheets(properties: list[dict], names: list[str] = SHEET_NAMES, overrides: dict[str, int] | None = None) -> dict[str, dict]:
    """Maps each wanted tab name to its sheet properties (sheetId, title, gridProperties).

    A tab listed in `overrides` is looked up by id, every other one by title (surrounding spaces ignored).
    """
    overrides = sheet_id_overrides() if overrides is None else overrides
    by_id = {sheet["sheetId"]: sheet for sheet in properties}
    by_title = {sheet["title"].strip(): sheet for sheet in properties}
    found, missing = {}, []
    for name in names:
        sheet = by_id.get(overrides[name]) if name in overrides else by_title.get(name)
        if sheet is None:
            missing.append(name)
        else:
            found[name] = sheet
    if missing:
        raise ValueError(f"Required tabs not found: {', '.join(missing)}")
    return found


BASE_CURRENCY = "THB"
# Wallet currency is the symbol in its name: "Card ฿", "Cash $", "Crypto ₮".
CURRENCY_SYMBOLS = {"฿": "THB", "₽": "RUB", "$": "USD", "€": "EUR", "₮": "USDT"}

# Only operation types are fixed in code; the operations themselves come from the sheet.
# "Income/Expense": the wallet decides the direction of a row (Catalog.row_sign); expense by default.
TWO_WAY_TYPE = "income/expense"
TYPE_SIGN = {"income": 1, "expense": -1, "transfer": 0, TWO_WAY_TYPE: -1}

DEPOSIT_RECEIVED = "Deposit received"
DEPOSIT_RETURNED = "Deposit refund"
DEPOSIT_RENT = "Rent from deposit"
DAMAGE_WITHHOLDING = "Damage charge"
RENT = "Rent payment"
RENT_OPERATIONS = [RENT, DEPOSIT_RENT]
REPAIR = "Repair"
MAINTENANCE = "Maintenance & consumables"
INVESTOR_PAYOUT = "Investor payout"
SALARY = "Salary"
OTHER_EXPENSES = "Other expenses"
FINANCE_OPERATIONS = {"Investor contribution", INVESTOR_PAYOUT, "Shareholder contribution",
                      "Shareholder payout", "Owner deposit", "Owner withdrawal"}
ASSET_OPERATIONS = {"Purchase of bikes & equipment", "Sale of bikes & equipment"}
FX_LABEL = "FX difference"
# Bike statuses the calculation and the dashboard compare against.
RENTED, RESERVED, AVAILABLE = "Rented", "Reserved", "Available"

PERCENT = {"type": "PERCENT", "pattern": "0.0%"}
DATE_FORMAT = {"type": "DATE", "pattern": "dd.mm.yyyy"}
