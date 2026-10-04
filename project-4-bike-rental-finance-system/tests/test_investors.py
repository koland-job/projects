"""Stable identities, editable names, history, accruals, settlements and the investor report."""
import copy
import unittest

import pandas as pd
from support import bike_row, day, snapshot, workbook, written

from fleet_ledger.accruals import Accruals, bike_roi, investor_report, write_investor_report
from fleet_ledger.bikes import Bikes, BikeHistory, load_bikes, load_history
from fleet_ledger.investors import InvestorDirectory, assign_investor_ids, read_settlements
from fleet_ledger.parse import table
from fleet_ledger.plan import plan
from fleet_ledger.writes import Writes


def history(rows, repair_share=.5):
    return BikeHistory(pd.DataFrame([{"repair_share": repair_share, **row} for row in rows]), {})


def bikes(*records):
    defaults = dict(full_name="Bike", repair_share=.5, purchase_price=1000., sheet_row=2)
    return Bikes(pd.DataFrame([{**defaults, **record} for record in records]), {"full_name": 1, "roi": 2})


def ops(*rows):
    return pd.DataFrame([dict(zip(["date", "bike_id", "owner_id", "operation", "total", "amount_thb"],
                                  (pd.Timestamp(date), *rest))) for date, *rest in rows])


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = InvestorDirectory([{"owner_id": 1, "Name": "Alex New"}, {"owner_id": 2, "Name": "Other"}])

    def test_rename_follows_id(self):
        self.assertEqual(self.directory.resolve("Alex", 1, "Alex", editable=True), 1)
        self.assertEqual(self.directory.resolve("Alex", 1), 1)
        self.assertEqual(self.directory.name(1), "Alex New")

    def test_explicit_selection_changes_investor(self):
        self.assertEqual(self.directory.resolve("Other", 1, "Alex", editable=True), 2)

    def test_old_name_reused_or_names_swapped_do_not_rebind(self):
        directory = InvestorDirectory([{"owner_id": 1, "Name": "Other"}, {"owner_id": 2, "Name": "Alex"}])
        self.assertEqual(directory.resolve("Alex", 1, "Alex", editable=True), 1)

    def test_deleted_id_never_falls_back_to_another_person(self):
        with self.assertRaisesRegex(ValueError, "Restore the row"):
            self.directory.resolve("Other", 99, "Old", editable=True)

    def test_duplicates_and_missing_ids_fail(self):
        for records in [[{"owner_id": 1, "Name": "A"}, {"owner_id": 1, "Name": "B"}],
                        [{"owner_id": 1, "Name": "A"}, {"owner_id": 2, "Name": " a "}],
                        [{"owner_id": "", "Name": "A"}], [{"owner_id": 1.5, "Name": "A"}]]:
            with self.subTest(records=records), self.assertRaises(ValueError):
                InvestorDirectory(records)

    def test_whitespace_and_new_selection(self):
        self.assertEqual(self.directory.resolve(" Other "), 2)
        self.assertIsNone(self.directory.resolve("", 1, "Alex", editable=True))

    def test_unknown_manual_name_is_readable_error(self):
        with self.assertRaisesRegex(ValueError, "rename investors"):
            self.directory.resolve("Typo", 1, "Alex", editable=True)


class HistoryTests(unittest.TestCase):
    def run_history(self, identity=1, share=.5):
        values = {"Bike history": [["bike_id", "owner", "owner_id", "owner_share", "active_from", "repair_share"],
                                     [10, "Alex", 1, .5, 45292, .5]],
                  "Operations": [["date", "operation", "bike", "bike_id"]]}
        directory = InvestorDirectory([{"owner_id": 1, "Name": "Alex New"}, {"owner_id": 2, "Name": "Other"}])
        frame = pd.DataFrame([{"id": 10, "full_name": "Bike", "old_name": "Bike", "owner": directory.name(identity),
                               "owner_id": identity, "owner_share": share, "repair_share": .5}])
        writes = Writes(copy.deepcopy(values))
        result = load_history(values, Bikes(frame, {}), directory, pd.Timestamp("2026-09-30 18:00"), writes)
        return result, writes

    def test_rename_does_not_append_history_or_change_dates(self):
        result, writes = self.run_history()
        self.assertEqual(result.appends, [])
        self.assertEqual(result.frame.iloc[0].owner, "Alex New")
        self.assertEqual(result.frame.iloc[0].active_from, pd.Timestamp("2024-01-01"))
        self.assertEqual(set(writes.changes), {("Bike history", 2, 2)})

    def test_real_transfer_and_share_change_are_recorded(self):
        for identity, share in [(2, .5), (1, .7)]:
            result, _ = self.run_history(identity, share)
            self.assertEqual(len(result.appends), 1)
            self.assertEqual(result.appends[0]["owner_id"], identity)

    def test_existing_history_shares_and_dates_are_never_rewritten(self):
        values = workbook(history=[[1, "Alex", 1, .5, day("2026-08-01")]])
        values["Bike history"][1][3] = .4   # someone edited the hidden sheet by hand
        result = plan(snapshot(values))
        self.assertNotIn(("Bike history", 2, 4), result.changes)
        self.assertEqual(len(result.history_appends), 1)  # the current share is recorded as a change


class RepairShareHistoryTests(unittest.TestCase):
    """repair_share got its history: a change applies from the day it was recorded, not to the past."""

    def old_layout(self):
        # History rows from before the repair_share column existed.
        return workbook(history=[[1, "Alex", 1, .5, day("2026-08-01")]],
                        bikes=[bike_row(1, "Click", repair_share=.5, purchase=day("2026-08-01"))])

    def repair_column(self, values):
        return values["Bike history"][0].index("repair_share") + 1

    def test_column_is_added_and_old_rows_filled_once(self):
        values = self.old_layout()
        values["Bike history"][0] = values["Bike history"][0][:5]
        result = plan(snapshot(values))
        column = len(values["Bike history"][0]) + 1
        self.assertEqual(result.changes[("Bike history", 1, column)]["userEnteredValue"]["stringValue"], "repair_share")
        self.assertEqual(result.changes[("Bike history", 2, column)]["userEnteredValue"]["numberValue"], .5)
        self.assertEqual(result.history_appends, [])
        again = plan(snapshot(written(values, result)))
        self.assertEqual((again.changes, again.history_appends), ({}, []))

    def test_change_is_recorded_and_earlier_costs_keep_the_old_share(self):
        values = written(self.old_layout(), plan(snapshot(self.old_layout())))
        before = plan(snapshot(values)).reports["P&L"]["-Accrued to investors"]
        header = values["Bikes"][0]
        values["Bikes"][1][header.index("repair_share")] = 1
        result = plan(snapshot(values))
        self.assertEqual(len(result.history_appends), 1)
        self.assertEqual(result.history_appends[0]["repair_share"], 1)
        self.assertEqual(result.history_appends[0]["active_from"], snapshot(values).run_at)
        # Maintenance on 12.09 was before the change: still split at 0.5.
        self.assertEqual(result.reports["P&L"]["-Accrued to investors"], before)
        self.assertNotIn(("Bike history", 2, self.repair_column(values)), result.changes)

    def test_filled_repair_share_in_history_is_never_rewritten(self):
        values = written(self.old_layout(), plan(snapshot(self.old_layout())))
        values["Bike history"][1][self.repair_column(values) - 1] = .3   # edited by hand
        result = plan(snapshot(values))
        self.assertNotIn(("Bike history", 2, self.repair_column(values)), result.changes)
        self.assertEqual(len(result.history_appends), 1)   # the bike's 0.5 is recorded as a change


class BikeIdTests(unittest.TestCase):
    def test_deleted_bike_id_is_not_reused(self):
        # Bike 7 was deleted; its operation and history still point at it.
        values = workbook(bikes=[bike_row(1, "One"), bike_row("", "New")])
        header = values["Operations"][0]
        values["Operations"].append([""] * len(header))
        values["Operations"][-1][header.index("bike_id")] = 7
        values["Bike history"].append([7, "Alex", 1, .5, 46000])
        directory = InvestorDirectory([{"owner_id": 1, "Name": "Alex"}])
        result = load_bikes(values, directory, Writes(copy.deepcopy(values)))
        self.assertEqual(result.frame["id"].tolist(), [1, 8])


class InvestorIdTests(unittest.TestCase):
    def test_new_investor_gets_id_after_every_used_one(self):
        # Investor 5 was deleted from the directory, but history still points at it.
        values = {"Investors": [["owner_id", "Name"], [1, "Alex"], ["", "New"], ["", "Next"]],
                  "Bikes": [["owner_id"], [1]], "Operations": [["owner_id"], [3]],
                  "Bike history": [["owner_id"], [5]], "Investor settlement": [["owner_id"], [""]]}
        writes = Writes(copy.deepcopy(values))
        rows = table(values["Investors"], ["owner_id", "Name"], "Investors")
        assign_investor_ids(rows.frame, rows.columns["owner_id"], values, writes)
        self.assertEqual(rows.frame["owner_id"].tolist(), [1, 6, 7])
        self.assertEqual({key: value["userEnteredValue"]["numberValue"] for key, value in writes.changes.items()},
                         {("Investors", 3, 1): 6, ("Investors", 4, 1): 7})

    def test_new_investor_can_be_picked_for_a_bike_in_the_same_run(self):
        values = workbook(investors=[["owner_id", "Name"], [1, "Alex"], ["", "Newcomer"]],
                          bikes=[bike_row(1, "Click", owner="Newcomer", owner_id="", purchase=day("2026-08-01"))])
        values["Bikes"][1][values["Bikes"][0].index("owner_name_snapshot")] = ""
        result = plan(snapshot(values))
        self.assertEqual(result.changes[("Investors", 3, 1)]["userEnteredValue"]["numberValue"], 2)
        self.assertEqual(result.history_appends[0]["owner_id"], 2)


class AccrualTests(unittest.TestCase):
    def test_rent_times_owner_share_minus_net_costs_times_repair_share(self):
        accruals = Accruals(bikes(dict(id=10, owner_id=1)),
                            history([dict(bike_id=10, owner_id=1, owner_share=.5,
                                          active_from=pd.Timestamp("2026-01-01"), sheet_row=2)], repair_share=.4), {})
        rows = ops(*[("2026-09-01", 10, 1, name, total, abs(total)) for name, total in
                     [("Rent payment", 1000.), ("Maintenance & consumables", -300.), ("Repair", -200.),
                      ("Damage charge", 50.)]])
        # 1000 × 0.5 − (200 + 300 − 50) × 0.4
        self.assertAlmostEqual(accruals.by_owner(rows)[1], 320)

    def test_accrual_uses_id_and_day_of_transfer_consistently(self):
        accruals = Accruals(bikes(dict(id=10, owner_id=2)), history([
            dict(bike_id=10, owner="Old label", owner_id=1, owner_share=.5, active_from=pd.Timestamp("2024-01-01"), sheet_row=2),
            dict(bike_id=10, owner="Other", owner_id=2, owner_share=.6, active_from=pd.Timestamp("2026-09-30 18:00"), sheet_row=3)]), {})
        rows = ops(("2026-09-30", 10, 2, "Rent payment", 100, 100))
        self.assertEqual(accruals.by_owner(rows).to_dict(), {2: 60})

    def test_missing_repair_share_with_costs_is_an_error(self):
        accruals = Accruals(bikes(dict(id=10, owner_id=1)), history([
            dict(bike_id=10, owner_id=1, owner_share=.5, active_from=pd.Timestamp("2026-01-01"), sheet_row=2)],
            repair_share=float("nan")), {})
        with self.assertRaisesRegex(ValueError, "repair_share"):
            accruals.by_owner(ops(("2026-09-01", 10, 1, "Maintenance & consumables", -100, 100)))


class RoiAndReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = InvestorDirectory([{"owner_id": 1, "Name": "Alex"}, {"owner_id": 2, "Name": "Other"}])

    def test_future_rent_counts_neither_in_bike_nor_investor_roi(self):
        fleet = bikes(dict(id=10, owner_id=1))
        accruals = Accruals(fleet, history([dict(bike_id=10, owner_id=1, owner_share=.5,
                                                 active_from=pd.Timestamp("2026-01-01"), sheet_row=2)]), {})
        all_rows = ops(("2026-10-01", 10, 1, "Rent payment", 100., 100.),
                       ("2026-10-20", 10, 1, "Rent payment", 900., 900.))
        current = all_rows.loc[all_rows["date"] <= pd.Timestamp("2026-10-04")]
        bike_roi(fleet, current, Writes({"Bikes": [["full_name", "roi"], ["Bike", ""]]}))
        report, _ = investor_report(accruals, self.directory, [1], current)
        self.assertAlmostEqual(fleet.frame.iloc[0]["roi"], .1)
        self.assertAlmostEqual(report.iloc[0]["roi"], .05)  # the investor's half of the rent

    def test_future_rent_is_left_out_of_the_whole_plan(self):
        values = workbook()
        future = [""] * len(values["Operations"][0])
        header = values["Operations"][0]
        for name, value in dict(date=day("2026-10-20"), operation="Rent payment", wallet_to="Cash ฿",
                                amount_thb=9000, bike="Honda Click   ").items():
            future[header.index(name)] = value
        before = plan(snapshot(workbook()))
        values["Operations"].append(future)
        after = plan(snapshot(values))
        for report in ["Free cash", "P&L"]:
            self.assertEqual(before.reports[report], after.reports[report])
        self.assertTrue(before.reports["Investors"].equals(after.reports["Investors"]))

    def test_former_bike_counts_in_earned_but_not_in_roi(self):
        # Investor 1 owned bike 10 until 15.09 (then investor 2), and still owns bike 11.
        fleet = bikes(dict(id=10, owner_id=2, full_name="Ten"),
                      dict(id=11, owner_id=1, full_name="Eleven", purchase_price=2000.))
        accruals = Accruals(fleet, history([
            dict(bike_id=10, owner_id=1, owner_share=.5, active_from=pd.Timestamp("2026-01-01"), sheet_row=2),
            dict(bike_id=11, owner_id=1, owner_share=.5, active_from=pd.Timestamp("2026-01-01"), sheet_row=3),
            dict(bike_id=10, owner_id=2, owner_share=.5, active_from=pd.Timestamp("2026-09-15"), sheet_row=4)]), {})
        rows = ops(("2026-09-01", 10, 1, "Rent payment", 600., 600.),
                   ("2026-09-20", 10, 2, "Rent payment", 400., 400.),
                   ("2026-09-20", 11, 1, "Rent payment", 200., 200.))
        report, _ = investor_report(accruals, self.directory, [1, 2], rows)
        alex = report.set_index("owner_id").loc[1]
        self.assertAlmostEqual(alex["total_earned"], 400)   # 600 × 0.5 + 200 × 0.5
        self.assertAlmostEqual(alex["roi"], 100 / 2000)     # only bike 11 he still owns

    def test_renamed_report_preserves_comments_by_id(self):
        rows = [["owner", "owner_id", "total_bike_worth", "total_earned", "to_be_paid", "roi", "comment"],
                ["Alex", 1, 100, 20, 10, .2, "Keep this comment"]]
        writes = Writes({"Investor settlement": copy.deepcopy(rows)})
        report = pd.DataFrame([dict(owner="Alex New", owner_id=1, total_bike_worth=100, total_earned=20,
                                    to_be_paid=10, roi=.2)])
        write_investor_report(report, rows, InvestorDirectory([{"owner_id": 1, "Name": "Alex New"}]), writes)
        self.assertNotIn(("Investor settlement", 2, 7), writes.changes)
        self.assertEqual(writes.changes[("Investor settlement", 2, 1)]["userEnteredValue"]["stringValue"], "Alex New")


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.rows = ops(("2026-09-01", 10, 1, "Rent payment", 1000., 1000.),
                        ("2026-09-13", 10, 1, "Investor payout", -10500., 10500.),
                        ("2026-09-20", 10, 1, "Rent payment", 4000., 4000.),
                        ("2026-09-25", 10, 1, "Investor payout", -500., 500.))

    def due(self, settlements):
        accruals = Accruals(bikes(dict(id=10, owner_id=1)), history([
            dict(bike_id=10, owner_id=1, owner_share=.5, active_from=pd.Timestamp("2026-01-01"), sheet_row=2)]),
            settlements)
        accrued, paid = accruals.due(1, self.rows)
        return accrued - paid

    def test_without_settlement_counts_full_history(self):
        self.assertEqual(self.due({}), 2500 - 11000)

    def test_settlement_day_is_closed_and_later_operations_count(self):
        self.assertEqual(self.due({1: dict(date=pd.Timestamp("2026-09-13"), due=0.)}), 2000 - 500)
        self.assertEqual(self.due({1: dict(date=pd.Timestamp("2026-09-13"), due=300.)}), 300 + 2000 - 500)
        self.assertEqual(self.due({2: dict(date=pd.Timestamp("2026-09-30"), due=0.)}), 2500 - 11000)

    def test_read_settlements(self):
        today = pd.Timestamp("2026-10-02")
        record = dict(sheet_row=3, owner_id=1, **{"Name": "A", "Settled on": 46278, "Due at settlement": ""})
        self.assertEqual(read_settlements([record], today), {1: dict(date=pd.Timestamp("2026-09-13"), due=0.)})
        self.assertEqual(read_settlements([dict(sheet_row=3, owner_id=1, **{"Name": "A"})], today), {})
        for extra, message in [({"Due at settlement": 5}, "without a date"), ({"Settled on": "03.10.2026"}, "after today"),
                               ({"Settled on": "not a date"}, "row 3")]:
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, message):
                read_settlements([dict(sheet_row=3, owner_id=1, **{"Name": "A"}, **extra)], today)


class RenameTests(unittest.TestCase):
    def test_rename_in_directory_updates_every_linked_sheet(self):
        values = written(workbook(), plan(snapshot(workbook())))
        values["Investors"][1][1] = "Alex Renamed"
        result = plan(snapshot(values))
        renamed = {sheet for (sheet, *_), fields in result.changes.items()
                   if fields["userEnteredValue"].get("stringValue") == "Alex Renamed"}
        self.assertEqual(renamed, {"Bikes", "Operations", "Bike history", "Investor settlement"})
        self.assertEqual(result.history_appends, [])


if __name__ == "__main__":
    unittest.main()
