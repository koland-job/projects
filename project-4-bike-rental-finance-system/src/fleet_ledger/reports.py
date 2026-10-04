"""Monthly reports (Cash flow, P&L) and "Free cash today"; their rows are found by label."""
from dataclasses import dataclass, field

import pandas as pd

from .accruals import Accruals
from .bikes import Bikes
from .catalog import Catalog, Wallets
from .investors import InvestorDirectory
from .ledger import ONE_DAY, Ledger
from .names import (ASSET_OPERATIONS, DAMAGE_WITHHOLDING, DATE_FORMAT, DEPOSIT_RECEIVED, DEPOSIT_RENT,
                    DEPOSIT_RETURNED, FINANCE_OPERATIONS, FX_LABEL, INVESTOR_PAYOUT, OTHER_EXPENSES, RENT, RENTED, REPAIR,
                    RESERVED, SALARY)
from .parse import as_date, find_label, number, table, text
from .staff import Staff
from .writes import Writes

# Fixed English names, independent of the machine's locale.
MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]
PNL_AMOUNT, PNL_RATIO = "Amount", "% of revenue"
INVESTOR_LABEL, OLD_INVESTOR_LABEL = "-Accrued to investors", "-Investor payout"
CASH_SUBTOTALS = {"Operating cash flow", "Investing cash flow", "Financing cash flow", "Net change in cash"}
# Deposits, contributions, real investor payouts and assets are not profit.
OUTSIDE_PNL = {DEPOSIT_RECEIVED, DEPOSIT_RETURNED} | FINANCE_OPERATIONS | ASSET_OPERATIONS


def month_title(month: pd.Timestamp) -> str:
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def label_at(values: list, column: int) -> str:
    return text(values[column - 1]).strip() if len(values) >= column else ""


# --- 6. months and the selected period ------------------------------------------------------

def write_months(all_dates: list, rows: list[list], writes: Writes) -> dict:
    """Writes the month list (newest first) and returns {title: first day}."""
    month_starts = sorted({day.replace(day=1) for day in all_dates}, reverse=True)
    if not month_starts:
        raise ValueError("Operations has no dates.")
    # Both titles of the month-list header are accepted.
    name_header = "Month name" if any("Month name" in row for row in rows) else "Name"
    months = table(rows, ["Month start", name_header], "Month start days")
    old_count = len(rows) - months.header
    for offset in range(max(len(month_starts), old_count)):
        row = months.header + 1 + offset
        day = month_starts[offset] if offset < len(month_starts) else None
        writes.put("Month start days", row, months.columns["Month start"],
                   (day - pd.Timestamp("1899-12-30")).days if day is not None else "", number_format=DATE_FORMAT)
        writes.put("Month start days", row, months.columns[name_header], month_title(day) if day is not None else "")
    # Month selection rules are set up by hand in Google Sheets and are left alone.
    return {month_title(day): day for day in month_starts}


@dataclass
class Layout:
    """Where the numbers of Cash flow and P&L go; found from labels, never fixed addresses."""
    pnl_amount_column: int
    pnl_ratio_column: int
    cash_label_column: int
    cash_amount_column: int
    selectors: dict           # report -> (row, column) of the selected month


def find_layout(values: dict, month_lookup: dict) -> Layout:
    pnl = table(values["P&L"], [PNL_AMOUNT, PNL_RATIO], "P&L")
    amount_column, ratio_column = pnl.columns[PNL_AMOUNT], pnl.columns[PNL_RATIO]
    # The P&L month selector is in its header row. A1 is not used.
    pnl_candidates = [(pnl.header, column) for column, value in enumerate(values["P&L"][pnl.header - 1], start=1)
                      if column not in (amount_column, ratio_column) and text(value).strip() not in ("", "Month")]
    if len(pnl_candidates) != 1:
        raise ValueError("P&L: month selector not found in the header row.")

    cash_rows = values["Cash flow"]
    opening_row, cash_label_column = find_label(cash_rows, "Cash at start of month", "Cash flow")
    # The month selector is above the table and may move around in that area.
    cash_candidates = [(row, column) for row, cells in enumerate(cash_rows[:opening_row - 1], start=1)
                       for column, value in enumerate(cells, start=1) if text(value).strip() in month_lookup]
    if len(cash_candidates) != 1:
        raise ValueError(f"Cash flow: {len(cash_candidates)} month values found above the opening balance; exactly one is needed.")
    amount_candidates = [column for column, value in enumerate(cash_rows[opening_row - 1], start=1)
                         if column != cash_label_column and text(value).strip()]
    if len(amount_candidates) != 1:
        raise ValueError('Cash flow: amount column not found in the "Cash at start of month" row.')
    return Layout(amount_column, ratio_column, cash_label_column, amount_candidates[0],
                  {"P&L": pnl_candidates[0], "Cash flow": cash_candidates[0]})


def selected_periods(values: dict, layout: Layout, month_lookup: dict, all_dates: list,
                     operations: pd.DataFrame, today: pd.Timestamp):
    """{report: {"month", "end"}} and the operations of each selected month. Only reads the selectors."""
    periods, monthly = {}, {}
    for name, (row, column) in layout.selectors.items():
        value = values[name][row - 1][column - 1]
        month = month_lookup[text(value)] if text(value) in month_lookup else as_date(value).replace(day=1)
        if month_title(month) not in month_lookup:
            raise ValueError(f"{name}: no operations in {month_title(month)}")
        next_month = month + pd.DateOffset(months=1)
        last_day = next_month - pd.Timedelta(days=1)
        dates_in_month = [day for day in all_dates if month <= day < next_month]
        periods[name] = {"month": month, "end": last_day if last_day < today else max(dates_in_month)}
        monthly[name] = operations.loc[(operations["date"] >= month) & (operations["date"] < next_month)]
    return periods, monthly


# --- 7. Cash flow ------------------------------------------------------------------------

def wallet_report(ledger: Ledger, wallets: Wallets, period: dict) -> pd.DataFrame:
    month, end = period["month"], period["end"]
    month_eve = month - ONE_DAY
    records = []
    for _, start in wallets.balances.iterrows():
        wallet, currency = start["wallet"], start["currency"]
        if start["start_date"] > month + pd.offsets.MonthEnd(0):
            raise ValueError(f'{wallet}: start date {start["start_date"]:%d.%m.%Y} in "Opening balances" is after the month '
                             f"selected in Cash flow ({month:%m.%Y}). Select a month no earlier than the wallet's start.")
        # A wallet started inside the month enters Cash flow with its opening amount: "start of
        # month" is the "Opening balances" amount, movement counts from the start date. Balances
        # are in the wallet currency, in baht at the rate of the day before and of the end;
        # movement is in baht at the operations' rates; the difference is FX.
        opening_day = max(month_eve, start["start_date"] - ONE_DAY)
        opening_native = ledger.wallet_native_at(wallet, opening_day)
        closing_native = ledger.wallet_native_at(wallet, end)
        opening = ledger.value_thb(opening_native, currency, opening_day)
        closing = ledger.value_thb(closing_native, currency, end)
        change = ledger.wallet_flow_thb(wallet, month, end)
        records.append({"Wallet": wallet, "Currency": currency, "Start of month": opening,
                        "Movement": change, FX_LABEL: closing - opening - change,
                        "End of period": closing, "End of period, native": closing_native})
    return pd.DataFrame(records)


def cash_flow(rows: list[list], month_operations: pd.DataFrame, wallets_report: pd.DataFrame, catalog: Catalog,
              wallets: Wallets, layout: Layout, writes: Writes) -> dict:
    # "From deposit" moves no money: the deposit came in as "Deposit received".
    operations = month_operations.loc[~month_operations["from_deposit"]]
    # Operations before a wallet's start are inside its opening amount — not this month's movement.
    start = wallets.start
    operations = operations.loc[[
        all(operation["date"] >= start[operation[side]] for side in ("wallet_from", "wallet_to") if operation[side])
        for _, operation in operations.iterrows()
    ]]
    totals = operations.groupby("operation")["total"].sum()

    # Detail rows come from the report itself, between two labelled bounds; their names
    # must match the operations directory exactly.
    opening_row, _ = find_label(rows, "Cash at start of month", "Cash flow")
    end_row, _ = find_label(rows, "Period end", "Cash flow")
    values = {}
    for cells in rows[opening_row:end_row - 1]:
        label = label_at(cells, layout.cash_label_column)
        if label and label not in CASH_SUBTOTALS:
            if label in values:
                raise ValueError(f"Cash flow: row {label!r} is repeated.")
            values[label] = 0.0

    # The whole directory is checked, even operations absent from the selected month.
    expected = {name for name, sign in catalog.signs.items() if sign != 0 and name not in catalog.from_deposit}
    missing = sorted(expected - set(values))
    # "From deposit" rows may be in Cash flow but are always 0: no money moves.
    unknown = sorted(set(values) - expected - ASSET_OPERATIONS - {FX_LABEL} - catalog.from_deposit)
    if wallets.foreign_currencies and FX_LABEL not in values:
        missing.append(FX_LABEL)
    if missing or unknown:
        raise ValueError("Cash flow: row names must match Operation types one to one "
                         f'(plus "{FX_LABEL}" if there are non-baht wallets). '
                         f"Missing rows: {missing}; extra rows: {unknown}. Nothing was written.")
    for category, amount in totals.items():
        if catalog.signs[category] != 0:
            values[category] += amount

    fx_flow = wallets_report[FX_LABEL].sum()
    flow_lines = set(values) - {FX_LABEL}
    work = sum(values[label] for label in flow_lines if label not in FINANCE_OPERATIONS | ASSET_OPERATIONS)
    finance = sum(values.get(label, 0) for label in FINANCE_OPERATIONS)
    assets = sum(values.get(label, 0) for label in ASSET_OPERATIONS)
    opening_cash = wallets_report["Start of month"].sum()
    values["Cash at start of month"] = opening_cash
    if FX_LABEL in values:
        values[FX_LABEL] = fx_flow
    values["Operating cash flow"] = work
    values["Investing cash flow"] = assets
    values["Financing cash flow"] = finance
    values["Net change in cash"] = work + assets + finance + fx_flow
    values["Cash at end of month"] = opening_cash + values["Net change in cash"]
    # Wallet rows above the total show money at the start of the month, in baht.
    for _, wallet in wallets_report.iterrows():
        row, _ = find_label(rows, wallet["Wallet"], "Cash flow")
        if row >= opening_row:
            raise ValueError(f"Cash flow: wallet {wallet['Wallet']} must be above the opening balance row.")
        writes.put("Cash flow", row, layout.cash_amount_column, wallet["Start of month"])
    if abs(values["Cash at end of month"] - wallets_report["End of period"].sum()) > 0.01:
        raise ValueError("Cash flow: the total balance does not match the sum of the wallets.")
    return values


# --- 8. P&L --------------------------------------------------------------------------------

@dataclass
class Pnl:
    values: dict
    ratios: dict
    detail_column: int
    investor_expense: float
    rows: list = field(repr=False)   # the P&L rows with the investor label already renamed


def pnl(rows: list[list], month_operations: pd.DataFrame, period: dict, catalog: Catalog, wallets: Wallets,
        ledger: Ledger, accruals: Accruals, today: pd.Timestamp, writes: Writes) -> Pnl:
    rows = [list(cells) for cells in rows]
    signs = catalog.signs
    # Expense labels are in one column; numbers to the right are never read as names.
    # The old investor label is accepted and renamed on the next write.
    investor_cells = [(r, c) for r, cells in enumerate(rows, 1)
                      for c, value in enumerate(cells, 1) if text(value).strip() in {OLD_INVESTOR_LABEL, INVESTOR_LABEL}]
    if len(investor_cells) != 1:
        raise ValueError('P&L: exactly one "-Accrued to investors" row (or "-Investor payout") is required.')
    investor_row, detail_column = investor_cells[0]
    writes.put("P&L", investor_row, detail_column, INVESTOR_LABEL)
    rows[investor_row - 1][detail_column - 1] = INVESTOR_LABEL

    depreciation_rows = [(number_, label_at(cells, detail_column)) for number_, cells in enumerate(rows, 1)
                         if label_at(cells, detail_column) in ["-Depreciation", "-Bikes"]]
    if len(depreciation_rows) != 1:
        raise ValueError('P&L: exactly one manual depreciation row ("-Depreciation") is required.')
    depreciation_row, depreciation_label = depreciation_rows[0]

    labels = {}
    for row_number, cells in enumerate(rows, 1):
        label = label_at(cells, detail_column)
        if row_number == depreciation_row or not label:
            continue
        if not label.startswith("-"):
            raise ValueError(f'P&L, row {row_number}: an expense label must start with "-": {label!r}.')
        category = INVESTOR_PAYOUT if label == INVESTOR_LABEL else label[1:].strip()
        if category == FX_LABEL:
            labels[category] = label  # computed by code, not in the operations directory
            continue
        if category not in signs:
            raise ValueError(f"P&L, row {row_number}: item {category!r} is not in Operation types.")
        if signs[category] != -1:
            raise ValueError(f"P&L, row {row_number}: item {category!r} must be an expense.")
        if category in labels:
            raise ValueError(f"P&L: item {category!r} is repeated.")
        labels[category] = label

    # A row's position decides which subtotal an expense belongs to.
    sections = {}
    for section, next_section in [("Variable costs", "Contribution margin"),
                                  ("Fixed costs", "Gross profit"),
                                  ("Administrative expenses", "Operating profit")]:
        first_row, _ = find_label(rows, section, "P&L")
        last_row, _ = find_label(rows, next_section, "P&L")
        sections[section] = [label_at(cells, detail_column) for cells in rows[first_row:last_row - 1]
                             if label_at(cells, detail_column) in labels.values()]
    if sorted(label for section in sections.values() for label in section) != sorted(labels.values()):
        raise ValueError("P&L: every expense item must be in exactly one expense section.")
    fixed_start, _ = find_label(rows, "Fixed costs", "P&L")
    fixed_end, _ = find_label(rows, "Gross profit", "P&L")
    if not fixed_start < depreciation_row < fixed_end:
        raise ValueError("P&L: manual depreciation must be in Fixed costs.")

    revenue_row, revenue_column = find_label(rows, "Revenue", "P&L")
    variable_row, _ = find_label(rows, "Variable costs", "P&L")
    revenue_labels = []
    for cells in rows[revenue_row:variable_row - 1]:
        label = label_at(cells, revenue_column)
        if not label:
            continue
        if label not in signs or signs[label] != 1:
            raise ValueError(f"P&L: revenue item {label!r} is not an income in Operation types.")
        revenue_labels.append(label)
    if len(revenue_labels) != len(set(revenue_labels)):
        raise ValueError("P&L: a revenue item is repeated.")
    if not revenue_labels or OTHER_EXPENSES not in labels or INVESTOR_PAYOUT not in labels:
        raise ValueError(f'P&L: check the revenue rows, "-{OTHER_EXPENSES}" and "{INVESTOR_LABEL}".')

    missing = [name for name, sign in signs.items()
               if sign != 0 and name not in OUTSIDE_PNL and name not in catalog.from_deposit
               and name not in (revenue_labels if sign > 0 else labels)]
    # "From deposit" operations may have their own revenue row. Without one, rent from a
    # deposit goes into "Rent payment" and damage withheld reduces "-Repair".
    if DEPOSIT_RENT in signs and DEPOSIT_RENT not in revenue_labels and RENT not in revenue_labels:
        missing.append(RENT)
    if DAMAGE_WITHHOLDING in signs and DAMAGE_WITHHOLDING not in revenue_labels and REPAIR not in labels:
        missing.append(f"-{REPAIR}")
    if wallets.foreign_currencies and FX_LABEL not in labels:
        missing.append(f"-{FX_LABEL} (in Administrative expenses)")
    if missing:
        raise ValueError(f"P&L: add items named as in Operation types: {missing}.")

    # Future-dated operations have not happened: they enter neither revenue, nor costs, nor
    # investor accruals. Otherwise rent paid ahead would be revenue without the investor's share.
    operations = month_operations.loc[month_operations["date"] <= today]
    values = {label: 0.0 for label in revenue_labels + list(labels.values())}
    for category, amount in operations.groupby("operation")["total"].sum().items():
        if category in OUTSIDE_PNL or signs[category] == 0:
            continue
        if category == DEPOSIT_RENT and category not in revenue_labels:
            values[RENT] += amount
            continue
        if category == DAMAGE_WITHHOLDING and category not in revenue_labels:
            values[labels[REPAIR]] -= amount
            continue
        label = labels.get(category, category)
        if label in values:
            values[label] += amount if signs[category] > 0 else -amount
        elif signs[category] > 0:
            raise ValueError(f"P&L: add an income row {category!r} named as in Operation types.")
        else:
            raise ValueError(f'P&L: add an expense row "-{category}" named as in Operation types.')

    # The investor expense is the accrued share of the month's bike results, over the same
    # operations as revenue. Real payouts stay in Cash flow and repay the debt; they do not reduce
    # profit a second time. A negative accrual reduces the expense, without clipping to zero.
    investor_expense = float(accruals.by_owner(operations).sum())
    values[labels[INVESTOR_PAYOUT]] = investor_expense
    # FX on our money: wallet revaluation minus revaluation of foreign deposits (returned to
    # clients in the same currency). A loss is an expense.
    if FX_LABEL in labels:
        wallets_fx, deposits_fx = ledger.fx_result(period["month"], period["end"])
        values[labels[FX_LABEL]] = -(wallets_fx - deposits_fx)

    # Empty manual fields count as zero and are not filled in Google.
    amount_column = table(rows, [PNL_AMOUNT, PNL_RATIO], "P&L").columns[PNL_AMOUNT]
    tax_row, _ = find_label(rows, "Tax", "P&L")
    depreciation = number(rows[depreciation_row - 1][amount_column - 1]) or 0
    tax = number(rows[tax_row - 1][amount_column - 1]) or 0

    values["Revenue"] = sum(values[label] for label in revenue_labels)
    values["Variable costs"] = sum(values[label] for label in sections["Variable costs"])
    values["Contribution margin"] = values["Revenue"] - values["Variable costs"]
    values["Fixed costs"] = sum(values[label] for label in sections["Fixed costs"]) + depreciation
    values["Gross profit"] = values["Contribution margin"] - values["Fixed costs"]
    values["Administrative expenses"] = sum(values[label] for label in sections["Administrative expenses"])
    values["Operating profit"] = values["Gross profit"] - values["Administrative expenses"]
    values["Net profit"] = values["Operating profit"] - tax

    ratios = _ratios(rows, values, sections, labels, detail_column, depreciation_label, depreciation, tax)
    return Pnl(values, ratios, detail_column, investor_expense, rows)


def _ratios(rows, values, sections, labels, detail_column, depreciation_label, depreciation, tax) -> dict:
    amounts = {**values, depreciation_label: depreciation, "Tax": tax}
    # In variable costs a ratio sits on the first item of each subgroup ("Bike", "Repair",
    # "Marketing") and covers every item of that subgroup.
    variable_start, _ = find_label(rows, "Variable costs", "P&L")
    variable_end, _ = find_label(rows, "Contribution margin", "P&L")
    section_column = find_label(rows, "Revenue", "P&L")[1]
    first_label, group_labels, without_ratio = None, [], []
    for cells in rows[variable_start:variable_end - 1]:
        label = label_at(cells, detail_column)
        if label not in sections["Variable costs"]:
            continue
        if label_at(cells, section_column) and first_label is not None:
            amounts[first_label] = sum(values[item] for item in group_labels)
            first_label, group_labels = None, []
        if first_label is None:
            first_label = label
        else:
            without_ratio.append(label)
        group_labels.append(label)
    if first_label is not None:
        amounts[first_label] = sum(values[item] for item in group_labels)

    ratios = {}
    for label, amount in amounts.items():
        denominator = values[RENT] if label in ["Delivery (client)", depreciation_label, labels[OTHER_EXPENSES]] \
            else values["Revenue"]
        ratios[label] = amount / denominator if denominator else ""
    for label in ["Revenue"] + without_ratio:
        ratios[label] = ""
    return ratios


# --- 9. free money today --------------------------------------------------------------------

@dataclass
class FreeMoney:
    values: dict
    payments: pd.DataFrame
    warnings: list


def free_money(rows: list[list], ledger: Ledger, wallets: Wallets, bikes: Bikes, accruals: Accruals,
               investors: InvestorDirectory, ids: list, staff: Staff, current: pd.DataFrame,
               today: pd.Timestamp, writes: Writes) -> FreeMoney:
    """Independent of the months selected in Cash flow and P&L; future-dated operations are not money yet."""
    warnings = []
    # Wallets in their own currency, in baht at the last exchange rate.
    wallet_now, held_native = {}, []
    for _, start in wallets.balances.iterrows():
        wallet = start["wallet"]
        if start["start_date"] > today:
            raise ValueError(f"{wallet}: the opening balance date is after today.")
        native = ledger.wallet_native_at(wallet, today)
        wallet_now[wallet] = ledger.value_thb(native, start["currency"], today)
        held_native.append((start["currency"], native))

    # A deposit is owed to the client per bike, in its currency, in baht at the last rate.
    if wallets.deposit_start_date > today:
        raise ValueError("Deposits: the opening balance date is after today.")
    open_deposits = ledger.deposits_native_at(today)
    deposits_due = ledger.deposits_thb_at(today)

    # The rate foreign money is valued at must be fresh.
    for currency in wallets.foreign_currencies:
        held = any(cur == currency and abs(native) > 0.005 for cur, native in held_native)
        held = held or any(cur == currency for _, cur in open_deposits)
        if not held:
            continue
        history = ledger.rate_history.get(currency)
        last = history.loc[history["date"] <= today] if history is not None else None
        last_date = last.iloc[-1]["date"] if last is not None and not last.empty else None
        if last_date is None or (today - last_date).days > 14:
            warnings.append(f"The {currency} rate is stale (last: {'none' if last_date is None else f'{last_date:%d.%m.%Y}'}): "
                            "foreign money is valued at an old rate. Fill amount_thb in a recent operation in this currency.")

    # A free bike with an open deposit — the return was probably not recorded.
    frame = bikes.frame
    status = dict(zip(frame["id"], frame["status"].map(lambda v: text(v).strip()))) if "status" in frame else {}
    for (bike_id, currency), amount in open_deposits.items():
        if not pd.isna(bike_id) and status.get(bike_id) not in (RENTED, RESERVED):
            warnings.append(f"The bike is not rented but its deposit is still open — check the refund: "
                            f"{frame.loc[frame['id'] == bike_id, 'full_name'].iloc[0]} ({status.get(bike_id)}), "
                            f"{amount:,.2f} {currency}.")

    # Each investor's debt separately: overpaying one does not repay another.
    dues = []
    for identity in ids:
        accrued, paid = accruals.due(identity, current)
        dues.append({"Owner": investors.name(identity), "Remaining": accrued - paid})
    dues = pd.DataFrame(dues, columns=["Owner", "Remaining"])
    investors_due = dues["Remaining"].clip(lower=0).sum()
    for _, overpaid in dues.loc[dues["Remaining"] < -0.01].iterrows():
        warnings.append(f"Investor {overpaid['Owner']} is overpaid by {-overpaid['Remaining']:,.2f}. "
                        "It does not increase free cash.")

    payments = upcoming_payments(staff, current, today)
    upcoming_due = payments["Remaining"].sum()

    sheet = "Free cash today"
    _, amount_column = find_label(rows, "Amount", sheet)
    reserve_row, _ = find_label(rows, "Operating reserve", sheet)
    reserve_cells = rows[reserve_row - 1]
    reserve = number(reserve_cells[amount_column - 1] if len(reserve_cells) >= amount_column else "")
    if reserve is None or reserve < 0:
        raise ValueError('Free cash today: enter the operating reserve as a number (0 is fine).')
    money_now = sum(wallet_now.values())
    free = money_now - deposits_due - investors_due - upcoming_due - reserve
    values = {**wallet_now, "TOTAL NOW:": money_now, "Unreturned deposits": deposits_due,
              "Accrued but unpaid to investors": investors_due,
              "Upcoming fixed payments": upcoming_due, "TOTAL FREE:": free}
    for label, value in values.items():
        row, _ = find_label(rows, label, sheet)
        writes.put(sheet, row, amount_column, value)
    if free < 0:
        warnings.append("Free cash is negative: this is a shortfall, not money to spend.")
    return FreeMoney(values, payments, warnings)


def upcoming_payments(staff: Staff, current: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    # The plan comes only from "Fixed costs"; real payments from "Operations".
    # Investors are subtracted separately, so they are not added here a second time.
    recurring = staff.recurring
    for _, expense in recurring.iterrows():
        category = expense["Operation"]
        if category == INVESTOR_PAYOUT:
            raise ValueError(f'Fixed costs: remove "{INVESTOR_PAYOUT}" — it is already subtracted separately.')
        if category != SALARY:
            amount = number(expense["Monthly amount"])
            if amount is None or amount < 0:
                raise ValueError(f'Fixed costs, row {expense["sheet_row"]}: enter the amount for "{category}" as a number from 0.')

    # Salary on the 5th is for the 16th–end of the previous month, on the 20th for the 1st–15th.
    # Other payments are split evenly between the days of their row.
    horizon = today + pd.Timedelta(days=30)
    planned = []
    for month in pd.date_range(today.replace(day=1), horizon, freq="MS"):
        month_payments = current.loc[current["date"].dt.to_period("M") == month.to_period("M")]
        last_day = (month + pd.offsets.MonthEnd(0)).day
        for _, expense in recurring.iterrows():
            category, days = expense["Operation"], expense["days"]
            if category == SALARY:
                parts = [staff.salary_for_half(month - pd.DateOffset(months=1), False), staff.salary_for_half(month, True)]
            else:
                monthly_amount = number(expense["Monthly amount"])
                part = round(monthly_amount / len(days), 2)
                parts = [part] * (len(days) - 1) + [round(monthly_amount - part * (len(days) - 1), 2)]
            # Payments made this month close the earliest planned ones first.
            paid_left = month_payments.loc[month_payments["operation"] == category, "amount_thb"].sum()
            for day, plan in zip(days, parts):
                due_date = month + pd.Timedelta(days=min(day, last_day) - 1)
                paid = min(plan, paid_left)
                paid_left -= paid
                remaining = round(plan - paid, 2)
                if due_date <= horizon and remaining > 0:
                    planned.append({"Payment": category, "Date": due_date, "Planned": plan,
                                    "Paid": paid, "Remaining": remaining})
    return pd.DataFrame(planned, columns=["Payment", "Date", "Planned", "Paid", "Remaining"])
