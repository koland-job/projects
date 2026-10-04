"""A small synthetic spreadsheet in the real layout, and helpers to run the package on it."""
import copy
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fleet_ledger.accruals import REPORT_COLUMNS  # noqa: E402
from fleet_ledger.bikes import BIKE_COLUMNS, HISTORY_COLUMNS  # noqa: E402
from fleet_ledger.demo import apply_plan  # noqa: E402
from fleet_ledger.operations import OPERATION_COLUMNS  # noqa: E402
from fleet_ledger.parse import serial  # noqa: E402
from fleet_ledger.plan import Snapshot  # noqa: E402

RUN_AT = pd.Timestamp("2026-09-30 12:00")


def day(text: str) -> float:
    return serial(pd.Timestamp(text))


DIRECTORY = [["Name", "Type"], ["Rent payment", "Income"], ["Rent from deposit", "Income"],
             ["Damage charge", "Income"], ["Deposit received", "Income"], ["Deposit refund", "Expense"],
             ["Maintenance & consumables", "Expense"], ["Repair", "Expense"], ["Salary", "Expense"],
             ["Other expenses", "Expense"], ["Investor payout", "Expense"], ["Fuel", "Income/Expense"],
             ["Transfer", "Transfer"]]


def bike_row(bike_id, model, owner="Alex", owner_id=1, share=0.5, repair_share=0.5, price=60000,
             status="Rented", purchase=""):
    row = {name: "" for name in BIKE_COLUMNS}
    row.update(id=bike_id, brand="Honda", model=model, owner=owner, owner_id=owner_id, owner_name_snapshot=owner,
               owner_share=share, repair_share=repair_share, purchase_price=price, status=status,
               purchase_date=purchase)
    return row


def operation(date, name, wallet_from="", wallet_to="", thb="", cur="", bike="", bike_id=""):
    row = {column: "" for column in OPERATION_COLUMNS}
    row.update(date=day(date), operation=name, wallet_from=wallet_from, wallet_to=wallet_to,
               amount_thb=thb, amount_cur=cur, bike=bike, bike_id=bike_id)
    return row


def table_rows(columns: list, records: list) -> list:
    return [list(columns)] + [[record.get(column, "") for column in columns] for record in records]


def workbook(operations=None, bikes=None, history=None, wallets=None, investors=None, month="September 2026"):
    """Every sheet of the recalculation. wallets: [(name, start, balance, deposits)]."""
    wallets = wallets or [("Cash ฿", "2026-09-01", 10000, 0), ("Cash $", "2026-09-01", 0, 0)]
    bike_columns = BIKE_COLUMNS + ["status", "purchase_date"]
    bikes = bikes if bikes is not None else [bike_row(1, "Click", purchase=day("2026-08-01"))]
    if operations is None:
        operations = [operation("2026-09-10", "Rent payment", wallet_to="Cash ฿", thb=3000, bike="Honda Click   "),
                      operation("2026-09-12", "Maintenance & consumables", wallet_from="Cash ฿", thb=400,
                                bike="Honda Click   "),
                      operation("2026-09-15", "Rent payment", wallet_to="Cash $", thb=1650, cur=50,
                                bike="Honda Click   "),
                      operation("2026-09-20", "Salary", wallet_from="Cash ฿", thb=2000)]
    foreign = any("$" in name or "€" in name or "₽" in name or "₮" in name for name, *_ in wallets)
    variable = [["", "-Maintenance & consumables", 0, ""], ["", "-Repair", 0, ""], ["", "-Fuel", 0, ""],
                ["", "-Accrued to investors", 0, ""]]
    admin = [["", "-Other expenses", 0, ""]] + ([["", "-FX difference", 0, ""]] if foreign else [])
    pnl = ([["", "", "Amount", "% of revenue", month],
            ["Revenue", "", 0, ""], ["Rent payment", "", 0, ""],
            ["Variable costs", "", 0, ""]] + variable +
           [["Contribution margin", "", 0, ""], ["Fixed costs", "", 0, ""], ["", "-Salary", 0, ""],
            ["", "-Depreciation", "", ""], ["Gross profit", "", 0, ""], ["Administrative expenses", "", 0, ""]] +
           admin + [["Operating profit", "", 0, ""], ["Tax", "", "", ""], ["", "", "", ""],
                    ["Net profit", "", 0, ""]])
    cash_lines = ["Rent payment", "Deposit received", "Deposit refund", "Maintenance & consumables", "Repair", "Salary",
                  "Other expenses", "Investor payout", "Fuel"] + (["FX difference"] if foreign else [])
    cash = ([["Month", month]] + [[name, 0] for name, *_ in wallets] + [["Cash at start of month", 0]] +
            [[name, 0] for name in cash_lines] +
            [["Operating cash flow", 0], ["Investing cash flow", 0], ["Financing cash flow", 0],
             ["Net change in cash", 0], ["Period end", day("2026-09-30")], ["Cash at end of month", 0]])
    free = ([["Item", "Amount"]] + [[name, 0] for name, *_ in wallets] +
            [["TOTAL NOW:", 0], ["Unreturned deposits", 0], ["Accrued but unpaid to investors", 0],
             ["Upcoming fixed payments", 0], ["Operating reserve", 0], ["TOTAL FREE:", 0]])
    values = {
        "Bikes": table_rows(bike_columns, bikes),
        "Investors": investors or [["owner_id", "Name"], [1, "Alex"], [2, "Other"]],
        "Operations": table_rows(OPERATION_COLUMNS, operations),
        "Operation types": copy.deepcopy(DIRECTORY),
        "Wallets": [["Wallet"]] + [[name] for name, *_ in wallets],
        "Opening balances": [["Wallet", "Start date", "Amount", "Deposits"]] +
                            [[name, day(start), balance, deposits] for name, start, balance, deposits in wallets],
        "Bike history": [HISTORY_COLUMNS] + (history or []),
        "Staff": [["Employee", "Start date", "End date", "Monthly salary"],
                  ["Kim", day("2026-01-01"), "", 10000]],
        "Fixed costs": [["Operation", "Monthly amount", "Schedule"], ["Salary", 0, "5th and 20th"]],
        "Free cash today": free,
        "Investor settlement": [REPORT_COLUMNS + ["comment"]],
        "Month start days": [["Month start", "Name"]],
        "P&L": pnl,
        "Cash flow": cash,
    }
    return values


def snapshot(values: dict, run_at=RUN_AT) -> Snapshot:
    return Snapshot(copy.deepcopy(values), copy.deepcopy(values), pd.Timestamp(run_at))


def written(values: dict, result) -> dict:
    """The sheet as Google would hold it after the plan was applied."""
    return apply_plan(values, result)
