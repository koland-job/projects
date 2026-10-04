"""The whole recalculation on a synthetic spreadsheet, and the command line."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from support import RUN_AT, day, snapshot, workbook, written

from fleet_ledger.cli import main, save_snapshot
from fleet_ledger.parse import find_label
from fleet_ledger.plan import plan


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.values = workbook()
        self.result = plan(snapshot(self.values))

    def test_second_run_on_the_written_sheet_plans_nothing(self):
        again = plan(snapshot(written(self.values, self.result)))
        self.assertEqual(again.changes, {})
        self.assertEqual(again.history_appends, [])

    def test_money_adds_up(self):
        cash, wallets = self.result.reports["Cash flow"], self.result.reports["Wallets"]
        self.assertAlmostEqual(cash["Cash at end of month"], wallets["End of period"].sum())
        free = self.result.reports["Free cash"]
        self.assertAlmostEqual(free["TOTAL NOW:"], 10000 + 3000 - 400 - 2000 + 1650)

    def test_investor_accrual(self):
        # (3000 + 1650) × 0.5 − 400 × 0.5
        self.assertAlmostEqual(self.result.reports["P&L"]["-Accrued to investors"], 2125)
        self.assertAlmostEqual(self.result.reports["Investors"].set_index("owner_id").loc[1, "to_be_paid"], 2125)

    def test_first_history_row_starts_at_purchase(self):
        self.assertEqual(len(self.result.history_appends), 1)
        self.assertEqual(self.result.history_appends[0]["active_from"].strftime("%d.%m.%Y"), "01.08.2026")

    def test_end_of_period_goes_right_above_net_profit(self):
        sheet = written(self.values, self.result)["P&L"]
        row, column = find_label(sheet, "Period end", "P&L")
        self.assertEqual(row + 1, find_label(sheet, "Net profit", "P&L")[0])
        # The current month ends at its last operation, not at the end of the calendar month.
        self.assertEqual(sheet[row - 1][2], day("2026-09-20"))

    def test_unknown_operation_stops_everything(self):
        self.values["Operations"][1][1] = "Unknown"
        with self.assertRaisesRegex(ValueError, "Add these operations to Operation types"):
            plan(snapshot(self.values))

    def test_draft_with_input_but_no_operation_is_rejected(self):
        self.values["Operations"][1][1] = ""
        with self.assertRaisesRegex(ValueError, "no operation selected"):
            plan(snapshot(self.values))

    def test_month_without_operations_is_refused(self):
        self.values["P&L"][0][4] = day("2026-03-01")  # the selector may hold a date
        with self.assertRaisesRegex(ValueError, "P&L: no operations in"):
            plan(snapshot(self.values))


class CommandLineTests(unittest.TestCase):
    def test_snapshot_file_prints_the_plan_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            save_snapshot(snapshot(workbook(), RUN_AT), path)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--snapshot", str(path), "--project-dir", folder])
        self.assertEqual(code, 0)
        self.assertIn("Write plan", out.getvalue())
        self.assertIn("nothing was written", out.getvalue())

    def test_error_ends_with_a_message_for_people(self):
        with tempfile.TemporaryDirectory() as folder:
            values = workbook()
            values["Operations"][1][1] = "Unknown"
            path = Path(folder) / "snapshot.json"
            save_snapshot(snapshot(values), path)
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = main(["--snapshot", str(path), "--project-dir", folder])
        self.assertEqual(code, 1)
        self.assertTrue(err.getvalue().strip().splitlines()[-1].startswith("ERROR: Add these operations"))


if __name__ == "__main__":
    unittest.main()
