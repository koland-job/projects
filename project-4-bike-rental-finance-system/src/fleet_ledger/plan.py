"""plan(snapshot): the whole recalculation — sheet rows in, cell writes out, no I/O."""
from dataclasses import dataclass, field

import pandas as pd

from . import accruals as accruals_module
from .accruals import Accruals, bike_roi, investor_ids, investor_report, write_investor_report
from .bikes import load_bikes, load_history
from .catalog import load_catalog, load_wallets
from .investors import load_investors
from .ledger import Ledger
from .names import DATE_FORMAT, PERCENT, SHEET_NAMES
from .operations import load_operations
from .parse import find_label, text
from .reports import (cash_flow, find_layout, free_money, pnl, selected_periods, wallet_report, write_months)
from .staff import load_staff
from .writes import Writes, entered_value

PERIOD_END = "Period end"


@dataclass
class Snapshot:
    """Every sheet the recalculation reads, in two renders, and the moment it was read."""
    values: dict           # unformatted values: what the calculation reads
    formulas: dict         # formulas render: what must not change before writing
    run_at: pd.Timestamp   # in the spreadsheet's time zone, naive

    @property
    def today(self) -> pd.Timestamp:
        return self.run_at.normalize()


@dataclass
class Plan:
    changes: dict                   # (sheet, row, column) -> Sheets API cell fields
    history_appends: list           # new "Bike history" rows
    history_columns: dict
    history_allowed_writes: dict    # existing history cells that may change: (row, column) -> value
    operation_header: int
    operation_columns: dict
    reports: dict = field(default_factory=dict)    # figures for people: free cash, Cash flow, P&L, investors…
    warnings: list = field(default_factory=list)

    def diff(self, snapshot: Snapshot) -> pd.DataFrame:
        """Every planned write with the value it replaces."""
        from gspread.utils import rowcol_to_a1

        def previous(sheet, row, column):
            rows = snapshot.formulas[sheet]
            values = rows[row - 1] if row - 1 < len(rows) else []
            return values[column - 1] if column - 1 < len(values) else ""

        records = [{"Sheet": sheet, "Cell": rowcol_to_a1(row, column), "Before": previous(sheet, row, column),
                    "After": entered_value(fields)}
                   for (sheet, row, column), fields in sorted(self.changes.items())]
        for entry in self.history_appends:
            for name in ["bike_id", "owner", "owner_id", "owner_share", "active_from"]:
                value = entry[name]
                if name == "active_from":
                    value = value.strftime("%d.%m.%Y %H:%M:%S")
                records.append({"Sheet": "Bike history",
                                "Cell": rowcol_to_a1(entry["sheet_row"], self.history_columns[name]),
                                "Before": "(new row)", "After": "" if value is None else value})
        return pd.DataFrame(records, columns=["Sheet", "Cell", "Before", "After"])


def plan(snapshot: Snapshot) -> Plan:
    values, today, run_at = snapshot.values, snapshot.today, snapshot.run_at
    missing = [name for name in SHEET_NAMES if name not in values]
    if missing:
        raise ValueError(f"Required tabs not found: {', '.join(missing)}")
    writes = Writes(values)

    # 2. Directories
    catalog = load_catalog(values["Operation types"])
    wallets = load_wallets(values, today)
    investors, settlements = load_investors(values, writes, today)
    staff = load_staff(values, catalog.signs, today, writes)
    # 3. Bikes and their owners
    bikes = load_bikes(values, investors, writes)
    history = load_history(values, bikes, investors, run_at, writes)
    # 4. Operations and money
    operations = load_operations(values["Operations"], catalog, wallets, bikes, history, investors, writes)
    ledger = Ledger(operations, wallets, writes)
    frame = operations.frame
    # 5. ROI and investors. Nothing dated after today counts: it has not happened yet.
    missing_roi = [name for name in accruals_module.ROI_EXPENSES if catalog.signs.get(name) != -1]
    if missing_roi:
        raise ValueError(f"ROI needs these expense operations in Operation types: {missing_roi}")
    current = frame.loc[frame["date"] <= today].copy()
    accruals = Accruals(bikes, history, settlements)
    bike_roi(bikes, current, writes)
    ids = investor_ids(bikes, history, current)
    investors_table, roi_missing_prices = investor_report(accruals, investors, ids, current)
    write_investor_report(investors_table, values["Investor settlement"], investors, writes)
    # 6. Months and the selected period
    month_lookup = write_months(operations.all_dates, values["Month start days"], writes)
    layout = find_layout(values, month_lookup)
    periods, monthly = selected_periods(values, layout, month_lookup, operations.all_dates, frame, today)
    # 7–9. Reports
    wallets_table = wallet_report(ledger, wallets, periods["Cash flow"])
    cash_values = cash_flow(values["Cash flow"], monthly["Cash flow"], wallets_table, catalog, wallets, layout, writes)
    pnl_result = pnl(values["P&L"], monthly["P&L"], periods["P&L"], catalog, wallets, ledger, accruals, today, writes)
    free = free_money(values["Free cash today"], ledger, wallets, bikes, accruals, investors, ids,
                      staff, current, today, writes)
    # 10. Report numbers into their rows
    _write_reports(values, snapshot.formulas, pnl_result, cash_values, periods, layout, writes)
    _check_history_writes(writes.changes, history.allowed_writes)

    warnings = history.warnings + ledger.warnings + free.warnings
    if roi_missing_prices:
        warnings.append("ROI is empty for owners with bikes that have no purchase price: "
                        + "; ".join(f"{item['Owner']} — {item['Bikes without price']}" for item in roi_missing_prices))
    return Plan(
        changes=writes.changes, history_appends=history.appends, history_columns=history.columns,
        history_allowed_writes=history.allowed_writes,
        operation_header=operations.header, operation_columns=operations.columns,
        reports={"Free cash": free.values, "Upcoming payments": free.payments, "Cash flow": cash_values,
                 "Wallets": wallets_table, "P&L": pnl_result.values, "Investors": investors_table,
                 "Periods": periods, "Bike ROI": bikes.frame[["id", "full_name", "roi"]]},
        warnings=warnings,
    )


def _write_reports(values, formulas, pnl_result, cash_values, periods, layout, writes: Writes) -> None:
    # Only rows that already exist in the reports are written. P&L uses its rows with the
    # investor label already renamed.
    rows = {"Cash flow": values["Cash flow"], "P&L": pnl_result.rows}
    pnl_label_column = find_label(rows["P&L"], "Revenue", "P&L")[1]
    for name, numbers, column in [("Cash flow", cash_values, layout.cash_amount_column),
                                  ("P&L", pnl_result.values, layout.pnl_amount_column)]:
        for label, value in numbers.items():
            row, _ = find_label(rows[name], label, name)
            writes.put(name, row, column, value)
    for label, value in pnl_result.ratios.items():
        row, _ = find_label(rows["P&L"], label, "P&L")
        writes.put("P&L", row, layout.pnl_ratio_column, value, number_format=PERCENT)

    # The end of the period is found by its label; its value takes no part in profit.
    for name in ["Cash flow", "P&L"]:
        matches = [(r, c) for r, cells in enumerate(rows[name], 1)
                   for c, value in enumerate(cells, 1) if text(value).strip() == PERIOD_END]
        if len(matches) > 1:
            raise ValueError(f'{name}: several "{PERIOD_END}" rows')
        if matches:
            row, label_column = matches[0]
            candidates = [c for c, value in enumerate(rows[name][row - 1], 1) if c != label_column and text(value).strip()]
            if len(candidates) != 1:
                raise ValueError(f"{name}: period end cell not found")
            column = candidates[0]
        else:
            # An old unlabelled EDATE formula is removed by content, not by address.
            legacy = [(r, c) for r, cells in enumerate(formulas[name], 1)
                      for c, value in enumerate(cells, 1) if text(value).upper().startswith("=EDATE(")]
            if len(legacy) > 1:
                raise ValueError(f"{name}: several unlabelled EDATE dates — a period end label is required")
            for old_row, old_column in legacy:
                writes.put(name, old_row, old_column, "")
            row = len(rows[name]) + 1
            label_column = pnl_label_column if name == "P&L" else layout.cash_label_column
            column = layout.pnl_amount_column if name == "P&L" else layout.cash_amount_column
            writes.put(name, row, label_column, PERIOD_END)
        if name == "P&L":
            # The empty row just above net profit. On later runs the date stays there.
            target_row = find_label(rows["P&L"], "Net profit", "P&L")[0] - 1
            old_cells = {(row, label_column), (row, column)}
            new_cells = {(target_row, pnl_label_column), (target_row, layout.pnl_amount_column)}
            for target_r, target_c in new_cells - old_cells:
                cells = rows[name][target_r - 1]
                if target_c <= len(cells) and text(cells[target_c - 1]).strip():
                    raise ValueError("P&L: free the row above Net profit for the period end.")
            for old_r, old_c in old_cells - new_cells:
                writes.put(name, old_r, old_c, "")
            row, column = target_row, layout.pnl_amount_column
            writes.put(name, row, pnl_label_column, PERIOD_END)
        end_serial = (periods[name]["end"] - pd.Timestamp("1899-12-30")).days
        writes.put(name, row, column, end_serial, number_format=DATE_FORMAT)


def _check_history_writes(changes: dict, allowed: dict) -> None:
    # In existing history rows only the investor's name and id may change, and an empty
    # repair_share may be filled once; shares and dates never.
    for (sheet, row, column), fields in changes.items():
        if sheet == "Bike history" and ((row, column) not in allowed or entered_value(fields) != allowed[(row, column)]):
            raise ValueError("Existing history rows may only get the investor's name and ID; shares and dates never change.")
