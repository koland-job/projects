"""The demo: a deterministic synthetic workbook, its saved snapshot, and the dashboard in demo mode."""
import json
import os
import subprocess
import sys
import unittest
from datetime import date, timedelta

from support import ROOT

from fleet_ledger import demo
from fleet_ledger.cli import load_snapshot
from fleet_ledger.plan import plan

SNAPSHOT = ROOT / "demo" / "snapshot.json"


def same(a, b, path="") -> list:
    """Differences between two JSON values; numbers may differ by float noise only."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return [] if abs(a - b) <= 1e-6 * max(1, abs(a)) else [f"{path}: {a} != {b}"]
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} rows != {len(b)}"]
        return [diff for i, (x, y) in enumerate(zip(a, b)) for diff in same(x, y, f"{path}[{i}]")]
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return [f"{path}: keys differ"]
        return [diff for key in a for diff in same(a[key], b[key], f"{path}/{key}")]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


class DemoDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values, cls.result = demo.build()

    def test_generator_is_deterministic(self):
        self.assertEqual(demo.workbook(), demo.workbook())
        self.assertNotEqual(demo.workbook(demo.SEED + 1)["Operations"], demo.workbook()["Operations"])

    def test_second_run_on_the_snapshot_plans_nothing(self):
        again = plan(load_snapshot(SNAPSHOT))
        self.assertEqual(again.changes, {})
        self.assertEqual(again.history_appends, [])

    def test_saved_snapshot_matches_the_generator(self):
        saved = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(saved["taken_at"], demo.RUN_AT.isoformat())
        differences = same(saved["values"], json.loads(demo.to_json(self.values))["values"])
        self.assertEqual(differences[:5], [], "demo/snapshot.json is stale: run python -m fleet_ledger.demo")

    def test_the_story_the_dashboard_shows(self):
        bikes = self.values["Bikes"]
        status = bikes[0].index("status")
        statuses = [row[status] for row in bikes[1:]]
        self.assertEqual(len(statuses), 30)
        self.assertGreaterEqual(statuses.count("Rented"), 12)
        for name in ["Available", "In repair", "Reserved", "Sold"]:
            self.assertIn(name, statuses)
        free = self.result.reports["Free cash"]
        self.assertGreater(free["TOTAL FREE:"], 0)
        self.assertGreater(free["Unreturned deposits"], 0)
        investors = self.result.reports["Investors"].set_index("owner")["to_be_paid"]
        self.assertTrue((investors > 0).sum() >= 4 and (investors < 0).sum() == 1)
        # Two rent payments without an end date: data errors for the dashboard badge.
        operations = self.values["Operations"]
        column = operations[0].index
        rent = [row for row in operations[1:] if row[column("operation")] == "Rent payment"]
        self.assertEqual(sum(1 for row in rent if row[column("end_date")] == ""), 2)

    def test_two_years_of_history(self):
        operations = self.values["Operations"]
        column = operations[0].index
        days = [row[column("date")] for row in operations[1:]]
        self.assertEqual(min(days), demo.sheet_day(date(2024, 10, 1)))
        self.assertLessEqual(max(days), demo.sheet_day(demo.TODAY))
        self.assertGreater(max(days), demo.sheet_day(demo.TODAY) - 3)
        self.assertGreater(len(operations) - 1, 3000)
        # Every calendar month of the two years has operations.
        months = {(date(1899, 12, 30) + timedelta(days=int(day))).strftime("%Y-%m") for day in days}
        self.assertEqual(len(months), 24)

    def test_fleet_grows_from_ten_to_thirty_bikes(self):
        bikes = self.values["Bikes"]
        column = bikes[0].index
        purchased = [row[column("purchase_date")] for row in bikes[1:]]
        start = demo.sheet_day(demo.FIRST_DAY)
        self.assertEqual(sum(1 for day in purchased if day < start), 10)
        purchases = [row for row in self.values["Operations"][1:]
                     if row[self.values["Operations"][0].index("operation")] == "Purchase of bikes & equipment"]
        self.assertEqual(len(purchases), 20)
        types = [row[column("model")] for row in bikes[1:]]
        scooters = {"Click", "PCX", "NMAX", "Aerox", "XMAX"}
        share = sum(1 for model in types if model in scooters) / len(types)
        self.assertTrue(0.35 <= share <= 0.5, share)
        sold = [row for row in self.values["Operations"][1:]
                if row[self.values["Operations"][0].index("operation")] == "Sale of bikes & equipment"]
        self.assertTrue(1 <= len(sold) <= 2)

    def test_bike_names_carry_a_fleet_number_not_a_plate(self):
        bikes = self.values["Bikes"]
        column = bikes[0].index
        for row in bikes[1:]:
            self.assertEqual(row[column("licence_plate")], f"#{row[column('id')]:02d}")
            self.assertRegex(row[column("full_name")], r" #\d\d$")

    def test_wallets_and_seasonality(self):
        self.assertEqual([row[0] for row in self.values["Wallets"][1:]], ["Bank account ฿", "Cash ฿", "Card ฿", "Crypto ₮"])
        operations = self.values["Operations"]
        column = operations[0].index
        by_month = {}
        for row in operations[1:]:
            if row[column("operation")] == "Rent payment":
                month = (date(1899, 12, 30) + timedelta(days=int(row[column("date")]))).month
                by_month[month] = by_month.get(month, 0) + (row[column("amount_thb")] or 0)
        self.assertGreater(by_month[3], 1.8 * by_month[9])   # high season against the low one
        self.assertGreater(by_month[1], by_month[10])


DASHBOARD_CHECK = r"""
import json
from fastapi.testclient import TestClient
from app import main
client = TestClient(main.app)
dashboard = client.get("/api/dashboard", params={"range": "last_30_days"})
yoy = client.get("/api/dashboard", params={"range": "last_30_days", "compare": "yoy"}).json()
everything = client.get("/api/dashboard", params={"range": "all_time"}).json()
print(json.dumps({
    "dashboard": dashboard.status_code,
    "body": dashboard.json(),
    "yoy": yoy,
    "all_time": everything,
    "ranges": client.get("/api/ranges").status_code,
    "recalc": [client.post("/api/recalc").status_code, client.post("/api/recalc").json()["detail"]],
    "me": client.get("/api/me").json(),
    "login": client.get("/login", follow_redirects=False).status_code,
}))
"""


class DemoDashboardTests(unittest.TestCase):
    """The real app in a child process: other tests stub its config and sheets client."""

    @classmethod
    def setUpClass(cls):
        env = {key: value for key, value in os.environ.items()
               if key not in ("SPREADSHEET_URL", "CREDENTIALS_DIR", "DASHBOARD_USER")}
        env.update(DATA_SOURCE="demo", PYTHONDONTWRITEBYTECODE="1")
        done = subprocess.run([sys.executable, "-c", DASHBOARD_CHECK], cwd=ROOT / "dashboard" / "backend", env=env,
                              capture_output=True, text=True, timeout=120)
        if done.returncode:
            raise AssertionError(done.stderr)
        cls.answer = json.loads(done.stdout.strip().splitlines()[-1])

    def test_dashboard_answers_without_google_or_login(self):
        self.assertEqual(self.answer["dashboard"], 200)
        self.assertEqual(self.answer["ranges"], 200)
        self.assertEqual(self.answer["me"], {"auth": False, "user": None, "demo": True})
        self.assertEqual(self.answer["login"], 303)

    def test_today_is_frozen_at_the_snapshot(self):
        body = self.answer["body"]
        self.assertTrue(body["demo"])
        self.assertEqual(body["payment_as_of"], demo.TODAY.isoformat())
        self.assertEqual(body["data_as_of"], demo.TODAY.isoformat())

    def test_every_block_has_data(self):
        body = self.answer["body"]
        now = body["now"]
        self.assertGreater(now["free_money"], 0)
        self.assertEqual(now["fleet"]["total"], 30)
        self.assertGreater(now["investor_dues"]["owed"], 0)
        self.assertGreater(now["investor_dues"]["overpaid"], 0)
        self.assertGreater(body["period"]["revenue"]["value"], 0)
        self.assertIsNotNone(body["period"]["revenue"]["delta_pct"])
        self.assertEqual(len(body["daily"]["current"]), 30)
        states = [bike["payment_state"] for bike in body["bikes_revenue"]["bikes"]]
        self.assertEqual(states.count("overdue"), len(demo.OVERDUE))
        self.assertEqual(states.count("no_term"), 1)
        self.assertEqual(len(body["data_issues"]["rent_without_end_date"]), 2)

    def test_year_over_year_and_all_time_have_data(self):
        yoy = self.answer["yoy"]["period"]["revenue"]
        self.assertTrue(yoy["has_previous_data"])
        self.assertGreater(yoy["previous_value"], 0)
        self.assertIsNotNone(yoy["delta_pct"])
        self.assertTrue(any(point["revenue"] > 0 for point in self.answer["yoy"]["daily"]["previous"]))
        everything = self.answer["all_time"]
        self.assertEqual(everything["range"]["start"], "2024-10-01")
        self.assertEqual(everything["daily"]["bucket"], "month")
        self.assertEqual(len(everything["daily"]["current"]), 24)

    def test_recalculation_is_disabled(self):
        code, detail = self.answer["recalc"]
        self.assertEqual(code, 403)
        self.assertIn("disabled in the demo", detail)


if __name__ == "__main__":
    unittest.main()
