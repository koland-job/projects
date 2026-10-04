"""Demo data: a synthetic bike-rental spreadsheet, recalculated into a ready snapshot.

    python -m fleet_ledger.demo                    # writes demo/snapshot.json
    python -m fleet_ledger.demo --out FILE

Nothing here is real: investors, bikes, amounts and dates come from a fixed seed, so the
same code always produces the same snapshot. The workbook has the layout of the real one
(see tests/support.py); the package recalculates it once, the planned writes are applied in
memory, and the result is checked: a second run on it must plan nothing.
"""
import argparse
import copy
import json
import math
import random
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .accruals import REPORT_COLUMNS
from .bikes import BIKE_COLUMNS, HISTORY_COLUMNS
from .names import (DAMAGE_WITHHOLDING, DEPOSIT_RECEIVED, DEPOSIT_RENT, DEPOSIT_RETURNED, INVESTOR_PAYOUT,
                    MAINTENANCE, OTHER_EXPENSES, RENT, REPAIR, SALARY)
from .operations import OPERATION_COLUMNS
from .parse import serial
from .plan import Plan, Snapshot, plan
from .reports import MONTH_NAMES
from .staff import Staff
from .writes import entered_value

SEED = 7
RUN_AT = pd.Timestamp("2026-09-30 18:30")   # the moment of the demo recalculation; "today" of the demo
TODAY = RUN_AT.date()
FIRST_DAY = date(2024, 10, 1)                # the books start here: two years of operations
REPORT_MONTH = "August 2026"                 # the month selected in P&L and Cash flow
EPOCH = date(1899, 12, 30)

BANK, CASH, CARD, CRYPTO = "Bank account ฿", "Cash ฿", "Card ฿", "Crypto ₮"
DELIVERY, EXTRA, FUEL, TRANSFER = "Delivery (client)", "Extra services", "Fuel", "Transfer"
WASHING, TAXI, DELIVERY_FUEL = "Washing", "Taxi", "Delivery (fuel)"
PREMISES, ADVERTISING, ADMIN = "Premises rent", "Advertising", "Administrative"
CONTRIBUTION, OWNER_WITHDRAWAL = "Investor contribution", "Owner withdrawal"
SHAREHOLDER_IN, SHAREHOLDER_OUT = "Shareholder contribution", "Shareholder payout"
PURCHASE, SALE = "Purchase of bikes & equipment", "Sale of bikes & equipment"
TOMAS = 6   # the investor who gets an advance payout

DIRECTORY = [
    (RENT, "Income"), (DEPOSIT_RENT, "Income"), (DAMAGE_WITHHOLDING, "Income"), (DEPOSIT_RECEIVED, "Income"),
    (DEPOSIT_RETURNED, "Expense"), (DELIVERY, "Income"), (EXTRA, "Income"), (MAINTENANCE, "Expense"),
    (REPAIR, "Expense"), (WASHING, "Expense"), (TAXI, "Expense"), (DELIVERY_FUEL, "Expense"),
    (FUEL, "Income/Expense"), (SALARY, "Expense"), (PREMISES, "Expense"), (ADVERTISING, "Expense"),
    (ADMIN, "Expense"), (OTHER_EXPENSES, "Expense"), (INVESTOR_PAYOUT, "Expense"), (CONTRIBUTION, "Income"),
    (SHAREHOLDER_IN, "Income"), (SHAREHOLDER_OUT, "Expense"), (OWNER_WITHDRAWAL, "Expense"),
    (PURCHASE, "Expense"), (SALE, "Income"), (TRANSFER, "Transfer"),
]


@dataclass(frozen=True)
class Model:
    brand: str
    model: str
    capacity: int
    daily: int      # THB per day, list price in high season
    price: int      # purchase price, THB
    deposit: int    # THB


MODELS = {
    # scooters
    "click125": Model("Honda", "Click", 125, 230, 58000, 3000),
    "click160": Model("Honda", "Click", 160, 270, 69000, 3000),
    "pcx": Model("Honda", "PCX", 160, 330, 92000, 3000),
    "nmax": Model("Yamaha", "NMAX", 155, 340, 86000, 3000),
    "aerox": Model("Yamaha", "Aerox", 155, 320, 83000, 3000),
    "xmax": Model("Yamaha", "XMAX", 300, 450, 189000, 5000),
    # motorcycles
    "z400": Model("Kawasaki", "Z400", 400, 850, 215000, 5000),
    "ninja400": Model("Kawasaki", "Ninja 400", 400, 950, 225000, 5000),
    "vulcan": Model("Kawasaki", "Vulcan S", 650, 1000, 260000, 6000),
    "mt03": Model("Yamaha", "MT-03", 321, 800, 205000, 5000),
    "rebel": Model("Honda", "Rebel", 500, 900, 270000, 6000),
    "cb500x": Model("Honda", "CB500X", 500, 1100, 245000, 6000),
    "cb650r": Model("Honda", "CB650R", 650, 1350, 330000, 8000),
    "himalayan": Model("Royal Enfield", "Himalayan", 411, 850, 175000, 5000),
}

# owner_id, name, owner_share, repair_share. "Own fleet" is the business itself: no share.
INVESTORS = [
    (1, "Own fleet", 0.0, 0.0),
    (2, "Marco Bellini", 0.5, 0.5),
    (3, "Anna Kowalski", 0.45, 0.5),
    (4, "James Whitfield", 0.4, 0.4),
    (5, "Lena Hoffmann", 0.6, 0.5),
    (6, "Tomas Ruiz", 0.35, 0.3),
    (7, "Priya Nair", 0.5, 1.0),
]
SHARES = {identity: (share, repair) for identity, _, share, repair in INVESTORS}
INVESTOR_NAMES = {identity: name for identity, name, _, _ in INVESTORS}

# id, owner_id, model, colour, purchase date. The first ten are in the fleet when the books start;
# the rest arrive in quarterly batches, each paid either by the business or by the investor who joins.
FLEET = [
    (1, 1, "click125", "White", "2024-06-10"), (2, 1, "click125", "Black", "2024-06-10"),
    (3, 2, "nmax", "White", "2024-07-15"), (4, 1, "pcx", "Grey", "2024-08-05"),
    (5, 1, "rebel", "Black", "2024-08-05"), (6, 2, "z400", "Green", "2024-07-15"),
    (7, 1, "himalayan", "Grey", "2024-09-02"), (8, 2, "mt03", "Blue", "2024-09-02"),
    (9, 1, "ninja400", "Red and black", "2024-09-16"), (10, 1, "cb500x", "Silver", "2024-09-16"),
    (11, 3, "aerox", "Yellow", "2025-01-13"), (12, 3, "vulcan", "Black", "2025-01-13"),
    (13, 1, "z400", "Orange", "2025-01-13"),
    (14, 4, "xmax", "Dark blue", "2025-04-07"), (15, 4, "cb650r", "Graphite", "2025-04-07"),
    (16, 1, "click125", "Beige", "2025-04-07"),
    (17, 5, "pcx", "White", "2025-07-07"), (18, 5, "rebel", "Burgundy", "2025-07-07"),
    (19, 1, "himalayan", "Beige", "2025-07-07"),
    (20, 6, "ninja400", "Purple", "2025-10-06"), (21, 6, "aerox", "Orange", "2025-10-06"),
    (22, 2, "cb500x", "Black matte", "2025-10-06"),
    (23, 3, "click160", "Pink", "2026-01-12"), (24, 1, "z400", "Light blue", "2026-01-12"),
    (25, 4, "vulcan", "Brown", "2026-01-12"),
    (26, 7, "xmax", "White", "2026-04-06"), (27, 7, "mt03", "Dark blue", "2026-04-06"),
    (28, 1, "click125", "Light green", "2026-04-06"),
    (29, 7, "cb650r", "Silver", "2026-07-06"), (30, 1, "pcx", "Red", "2026-07-06"),
]
# Bike 6 changes hands: Marco sells it to Anna; "Bike history" keeps both periods.
TRANSFERRED = {6: (3, date(2026, 1, 1))}
SOLD = {2: (date(2025, 11, 24), 38000), 9: (date(2026, 5, 18), 150000)}
# Not rented at the end: status, and how many days before today the last rental ended.
PARKED = {15: ("In repair", 6), 20: ("In repair", 3), 25: ("Idle", 19), 3: ("For sale", 12), 23: ("Reserved", 4)}
# Rented, but the paid term ran out this many days ago: the dashboard flags them as overdue.
OVERDUE = {10: 3, 12: 6, 21: 11}
# Larger repairs: (bike, day, amount).
BIG_REPAIRS = [(14, date(2025, 6, 10), 11500), (26, date(2026, 7, 15), 9500)]
SHAREHOLDER_TOPUPS = [(date(2025, 8, 12), 30000), (date(2026, 2, 9), 50000)]

STAFF = [("Nina", "2024-06-01", "", 24000), ("Tom", "2024-08-01", "2025-08-31", 23000),
         ("Leo", "2025-09-01", "", 24000)]
PREMISES_RENT = 18000
OPERATING_RESERVE = 30000
DEPRECIATION = 60000
OPENING = {BANK: 400000, CASH: 40000, CARD: 30000, CRYPTO: 800}   # money of our own on FIRST_DAY (crypto in USDT)

# How busy a calendar month is: the number of operations relative to the average month (real pattern
# of a rental business). It drives how long bikes wait between rentals and a little the price.
SEASON = {1: 1.28, 2: 1.38, 3: 1.52, 4: 1.16, 5: 1.03, 6: 0.85, 7: 1.03, 8: 0.90, 9: 0.49, 10: 0.53, 11: 0.72, 12: 1.11}
GAP_BASE = 4.0      # mean idle days between rentals at the average month
SEASON_POWER = 1.8  # idle days follow the pattern squared: demand swings more than the operations count


def gap_days(month: int) -> float:
    return GAP_BASE / SEASON[month] ** SEASON_POWER


def price_factor(month: int) -> float:
    return 0.8 + 0.2 * SEASON[month]


def sheet_day(day: date) -> int:
    return (day - EPOCH).days


def crypto_rate(day: date) -> float:
    """THB per USDT: a slow drift, never a jump the package would flag as a typo."""
    t = (day - FIRST_DAY).days
    return round(34.2 + 0.9 * math.sin(2 * math.pi * t / 200) + 0.15 * math.sin(t / 9), 4)


def round_to(value: float, step: int) -> int:
    return int(round(value / step) * step)


@dataclass
class Rental:
    start: date
    end: date | None          # last day of the rental; None — not returned, the term ran out
    periods: list = field(default_factory=list)   # paid periods (first day, last day)


@dataclass
class Bike:
    id: int
    owner_id: int
    model: Model
    color: str
    purchased: date
    plate: str
    rentals: list = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return " ".join(str(part) for part in [self.model.brand, self.model.model, self.color,
                                                self.model.capacity, self.plate])

    def owner_on(self, day: date) -> int:
        if self.id in TRANSFERRED and day >= TRANSFERRED[self.id][1]:
            return TRANSFERRED[self.id][0]
        return self.owner_id


class Book:
    """Operations as the owner would type them, in date order."""

    def __init__(self):
        self.rows = []

    def add(self, day: date, operation: str, thb=None, cur=None, wallet_from="", wallet_to="",
            bike: Bike | None = None, owner: str = "", end_date: date | None = None, order: int = 5):
        self.rows.append({"day": day, "operation": operation, "amount_thb": thb, "amount_cur": cur,
                          "wallet_from": wallet_from, "wallet_to": wallet_to, "bike": bike, "owner": owner,
                          "end_date": end_date, "order": order, "seq": len(self.rows)})

    def sorted(self) -> list:
        return sorted(self.rows, key=lambda row: (row["day"], row["order"], row["bike"].id if row["bike"] else 0,
                                                  row["seq"]))


# --- rentals ---------------------------------------------------------------------------------

def _length(rng: random.Random) -> int:
    """Rental length in days: mostly a week or less, now and then a month (paid in 30-day periods)."""
    roll = rng.random()
    if roll < 0.22:
        return rng.randint(1, 3)
    if roll < 0.34:
        return rng.randint(4, 6)
    if roll < 0.64:
        return 7
    if roll < 0.78:
        return rng.choice([10, 14, 14])
    if roll < 0.85:
        return rng.randint(15, 29)
    return 30 if rng.random() < 0.85 else 60


def _simulate(rng: random.Random, bike: Bike, start: date, stop: date) -> list:
    """Rentals starting no later than stop. A stop before today also ends every rental by then."""
    rentals, day = [], start
    while True:
        begin = day + timedelta(days=rng.randint(0, round(2 * gap_days(day.month))))
        if begin > stop:
            return rentals
        length = _length(rng)
        end = begin + timedelta(days=length - 1)
        if stop < TODAY:
            end = min(end, stop)
        rentals.append(Rental(begin, end))
        if end >= TODAY:
            return rentals
        day = end + timedelta(days=1)


def _periods(begin: date, end: date) -> list:
    if (end - begin).days + 1 < 30:
        return [(begin, end)]
    periods, day = [], begin
    while day <= end:
        last = min(day + timedelta(days=29), end)
        periods.append((day, last))
        day = last + timedelta(days=1)
    return periods


def _rent_price(model: Model, first: date, last: date, monthly: bool) -> int:
    days = (last - first).days + 1
    discount = 0.67 if monthly else 0.85 if days >= 8 else 1.0
    return max(round_to(model.daily * days * price_factor(first.month) * discount, 10), 100)


def _book_rental(rng: random.Random, book: Book, bike: Bike, rental: Rental) -> None:
    model = bike.model
    pay_wallet = rng.choices([BANK, CASH, CARD, CRYPTO], [45, 30, 15, 10])[0]
    end = rental.end
    last_day = end if end is not None else rental.periods[-1][1]
    monthly = (last_day - rental.start).days + 1 >= 30

    # The deposit is cash; some clients leave a passport instead.
    deposit = model.deposit if rng.random() < 0.62 else 0

    # Late days are sometimes taken from the deposit at return instead of being paid.
    late = 0
    returned = end is not None and end < TODAY
    if returned and deposit and (end - rental.start).days >= 4 and rng.random() < 0.08:
        late = rng.randint(1, 2)
    damage = 0
    if returned and deposit and rng.random() < 0.08:
        damage = round_to(rng.randint(500, min(2500, deposit - late * model.daily - 500)), 50)

    if not rental.periods:
        rental.periods = _periods(rental.start, end - timedelta(days=late))
    if deposit:
        book.add(rental.start, DEPOSIT_RECEIVED, deposit, wallet_to=CASH, bike=bike, order=1)
    for first, last in rental.periods:
        if first > TODAY:
            break
        price = _rent_price(model, first, last, monthly)
        if pay_wallet == CRYPTO:
            rate = crypto_rate(first)
            coins = max(round_to(price / rate, 5), 10)
            book.add(first, RENT, round(coins * rate), coins, wallet_to=CRYPTO, bike=bike, end_date=last, order=2)
        else:
            book.add(first, RENT, price, wallet_to=pay_wallet, bike=bike, end_date=last, order=2)
    if rng.random() < 0.17:
        book.add(rental.start, DELIVERY, rng.choice([200, 300, 300, 400]), wallet_to=CASH, bike=bike, order=3)
    if rng.random() < 0.17:
        book.add(rental.start, DELIVERY_FUEL, round_to(rng.randint(60, 120), 10), wallet_from=CASH, bike=bike, order=3)
    if rng.random() < 0.26:
        book.add(rental.start, TAXI, round_to(rng.randint(100, 250), 10), wallet_from=CASH, bike=bike, order=3)

    if not returned:
        return
    left = deposit
    if late:
        withheld = round_to(model.daily * late * price_factor(end.month), 10)
        book.add(end, DEPOSIT_RENT, withheld, wallet_from=CASH, bike=bike, end_date=end, order=4)
        left -= withheld
    if damage:
        book.add(end, DAMAGE_WITHHOLDING, damage, wallet_from=CASH, bike=bike, order=4)
        book.add(end + timedelta(days=1), REPAIR, round_to(damage * rng.uniform(0.8, 1.2), 50),
                 wallet_from=CASH, bike=bike)
        left -= damage
    if deposit and left > 0:
        book.add(end, DEPOSIT_RETURNED, left, wallet_from=CASH, bike=bike, order=6)
    if rng.random() < 0.58:
        book.add(end, WASHING, round_to(rng.randint(80, 150), 10), wallet_from=CASH, bike=bike, order=7)
    if rng.random() < 0.06:
        book.add(end, FUEL, rng.choice([100, 150, 200]), wallet_to=CASH, order=7)


def _fleet(rng: random.Random) -> list:
    bikes = []
    for identity, owner_id, model, color, purchased in FLEET:
        # The fleet number stands where a licence plate would be: it ends up in the bike's name.
        bike = Bike(identity, owner_id, MODELS[model], color, date.fromisoformat(purchased), f"#{identity:02d}")
        start = max(FIRST_DAY, bike.purchased + timedelta(days=2)) + timedelta(days=rng.randint(0, 3))
        if bike.id in OVERDUE:
            paid_until = TODAY - timedelta(days=OVERDUE[bike.id])
            begin = paid_until - timedelta(days=29)
            bike.rentals = _simulate(rng, bike, start, begin - timedelta(days=2))
            bike.rentals.append(Rental(begin, None, [(begin, paid_until)]))
        else:
            stop = TODAY
            if bike.id in PARKED:
                stop = TODAY - timedelta(days=PARKED[bike.id][1])
            if bike.id in SOLD:
                stop = SOLD[bike.id][0] - timedelta(days=2)
            bike.rentals = _simulate(rng, bike, start, stop)
        bikes.append(bike)
    return bikes


def _status(bike: Bike) -> str:
    if bike.id in SOLD:
        return "Sold"
    if bike.id in PARKED:
        return PARKED[bike.id][0]
    active = [rental for rental in bike.rentals if rental.start <= TODAY and (rental.end is None or rental.end >= TODAY)]
    return "Rented" if active else "Available"


# --- the rest of the business ----------------------------------------------------------------

def _staff_frame() -> Staff:
    employees = pd.DataFrame([{"employee": name, "start_date": pd.Timestamp(start),
                               "end_date": pd.Timestamp(end) if end else pd.NaT, "monthly_salary": salary}
                              for name, start, end, salary in STAFF])
    return Staff(employees, pd.DataFrame())


def _months():
    month = date(FIRST_DAY.year, FIRST_DAY.month, 1)
    while month <= TODAY:
        yield month
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)


def _days():
    day = FIRST_DAY
    while day <= TODAY:
        yield day
        day += timedelta(days=1)


def _log_amount(rng: random.Random, median: float, spread: float, low: int, high: int, step: int) -> int:
    return round_to(min(max(rng.lognormvariate(math.log(median), spread), low), high), step)


def _running_costs(rng: random.Random, book: Book, bikes: list) -> None:
    staff = _staff_frame()
    for month in _months():
        stamp = pd.Timestamp(month)
        book.add(month, PREMISES, PREMISES_RENT, wallet_from=BANK)
        for day, amount in [(5, staff.salary_for_half(stamp - pd.DateOffset(months=1), False)),
                            (20, staff.salary_for_half(stamp, True))]:
            if month.replace(day=day) <= TODAY and amount:
                book.add(month.replace(day=day), SALARY, amount, wallet_from=BANK)
    for day in _days():
        if day.weekday() == 0 and day.day <= 14:
            book.add(day, FUEL, round_to(rng.randint(500, 1000), 10), wallet_from=CASH)
        if rng.random() < 0.10:
            book.add(day, OTHER_EXPENSES, _log_amount(rng, 280, 0.45, 120, 500, 10), wallet_from=CASH)
        if rng.random() < 0.05:
            book.add(day, ADMIN, _log_amount(rng, 1000, 0.8, 350, 2800, 50),
                     wallet_from=rng.choice([BANK, BANK, CARD]))
        if rng.random() < 0.04:
            book.add(day, ADVERTISING, _log_amount(rng, 3000, 0.6, 1500, 6000, 100), wallet_from=CARD)
        if rng.random() < 0.027:
            book.add(day, SHAREHOLDER_OUT, rng.randint(30, 84) * 100, wallet_from=BANK)
        if rng.random() < 0.25 * SEASON[day.month]:
            book.add(day, EXTRA, round_to(rng.randint(150, 600), 10), wallet_to=rng.choice([CASH, CASH, CARD, BANK]))
    for day, amount in SHAREHOLDER_TOPUPS:
        book.add(day, SHAREHOLDER_IN, amount, wallet_to=BANK)

    for bike in bikes:
        last_day = SOLD[bike.id][0] - timedelta(days=1) if bike.id in SOLD else TODAY
        first_day = max(FIRST_DAY, bike.purchased)
        day = first_day + timedelta(days=rng.randint(5, 60))
        while day <= last_day:
            book.add(day, MAINTENANCE, _log_amount(rng, 600, 0.45, 300, 1300, 10),
                     wallet_from=rng.choice([CASH, CASH, CASH, BANK]), bike=bike)
            day += timedelta(days=rng.randint(55, 100))
        for month in _months():
            day = month + timedelta(days=rng.randint(0, 27))
            if rng.random() < 0.07 and first_day <= day <= last_day:
                book.add(day, REPAIR, _log_amount(rng, 1200, 0.8, 650, 6200, 50),
                         wallet_from=rng.choice([BANK, BANK, CASH, CARD]), bike=bike)
        if bike.id in PARKED and PARKED[bike.id][0] == "In repair":
            day = TODAY - timedelta(days=PARKED[bike.id][1] - 1)
            book.add(day, REPAIR, round_to(rng.randint(2500, 4500), 50), wallet_from=CARD, bike=bike)
    by_id = {bike.id: bike for bike in bikes}
    for identity, day, amount in BIG_REPAIRS:
        book.add(day, REPAIR, amount, wallet_from=BANK, bike=by_id[identity])

    # Fleet changes. A batch is paid from the bank account; an investor who joins brings the money
    # for the bikes he or she will own a few days before. Some bikes are sold at the end of their time.
    contributions = {}
    for bike in bikes:
        if bike.purchased < FIRST_DAY:
            continue
        book.add(bike.purchased, PURCHASE, bike.model.price, wallet_from=BANK, bike=bike)
        if bike.owner_id != 1:
            key = (bike.owner_id, bike.purchased)
            contributions[key] = contributions.get(key, 0) + bike.model.price
    for (owner, day), amount in contributions.items():
        book.add(day - timedelta(days=3), CONTRIBUTION, amount, wallet_to=BANK, owner=INVESTOR_NAMES[owner])
    for identity, (day, price) in SOLD.items():
        book.add(day, SALE, price, wallet_to=BANK, bike=by_id[identity])


def _accruals(rows: list, bikes: list) -> dict:
    """{(owner_id, month): accrual} — the same rule as the package, to size the payouts."""
    totals = {}
    for row in rows:
        bike = row["bike"]
        if bike is None:
            continue
        owner = bike.owner_on(row["day"])
        if owner == 1:
            continue
        share, repair_share = SHARES[owner]
        amount = row["amount_thb"] or 0
        if row["operation"] in (RENT, DEPOSIT_RENT):
            value = amount * share
        elif row["operation"] in (REPAIR, MAINTENANCE):
            value = -amount * repair_share
        elif row["operation"] == DAMAGE_WITHHOLDING:
            value = amount * repair_share
        else:
            continue
        key = (owner, row["day"].replace(day=1))
        totals[key] = totals.get(key, 0.0) + value
    return totals


def _cash_management(book: Book) -> dict:
    """Bank deposits, top-ups, currency exchange and the owner's withdrawals, decided on running balances."""
    balance = dict(OPENING)
    lowest = dict(balance)
    rows = book.sorted()
    index = 0
    for day in _days():
        while index < len(rows) and rows[index]["day"] == day:
            row = rows[index]
            index += 1
            if row["operation"] in (DEPOSIT_RENT, DAMAGE_WITHHOLDING):
                continue   # taken from a deposit: no money moves
            for side, sign in (("wallet_to", 1), ("wallet_from", -1)):
                wallet = row[side]
                if wallet:
                    balance[wallet] += sign * (row["amount_cur"] if wallet == CRYPTO else row["amount_thb"])
        moves = []
        if balance[CASH] < 12000:
            moves.append((BANK, CASH, 30000, None))
        elif day.weekday() == 0 and balance[CASH] > 80000:
            moves.append((CASH, BANK, (balance[CASH] - 40000) // 5000 * 5000, None))
        if day.weekday() == 0 and balance[CARD] > 60000:
            moves.append((CARD, BANK, (balance[CARD] - 20000) // 5000 * 5000, None))
        spare = balance[CRYPTO] - 200
        if day.day in (1, 16) and spare >= 100:
            coins = int(spare // 50 * 50)
            moves.append((CRYPTO, BANK, round(coins * crypto_rate(day) * 0.985), coins))
        for source, target, thb, coins in moves:
            if not thb:
                continue
            book.add(day, TRANSFER, thb, coins, wallet_from=source, wallet_to=target, order=9)
            balance[source] -= coins if source == CRYPTO else thb
            balance[target] += thb
        # The owner takes what is left above a working balance.
        floor = {11: 450000, 26: 350000}.get(day.day)
        if floor and balance[BANK] > floor:
            thb = (balance[BANK] - floor) // 2 // 5000 * 5000
            if thb:
                book.add(day, OWNER_WITHDRAWAL, thb, wallet_from=BANK, order=9)
                balance[BANK] -= thb
        for wallet in balance:
            lowest[wallet] = min(lowest[wallet], balance[wallet])
    return lowest


# --- the workbook ----------------------------------------------------------------------------

def _operation_rows(rows: list) -> list:
    columns = OPERATION_COLUMNS + ["end_date"]
    table = [columns]
    for row in rows:
        record = {column: "" for column in columns}
        bike = row["bike"]
        record.update(date=sheet_day(row["day"]), operation=row["operation"], wallet_from=row["wallet_from"],
                      wallet_to=row["wallet_to"], amount_thb="" if row["amount_thb"] is None else row["amount_thb"],
                      amount_cur="" if row["amount_cur"] is None else row["amount_cur"],
                      bike=bike.full_name if bike else "", bike_id=bike.id if bike else "", owner=row["owner"],
                      end_date=sheet_day(row["end_date"]) if row["end_date"] else "")
        table.append([record[column] for column in columns])
    return table


def _pnl() -> list:
    return [["", "", "Amount", "% of revenue", REPORT_MONTH],
            ["Revenue", "", 0, ""], [RENT, "", 0, ""], [DELIVERY, "", 0, ""], [EXTRA, "", 0, ""],
            ["Variable costs", "", 0, ""],
            ["Bike", f"-{MAINTENANCE}", 0, ""], ["", f"-{WASHING}", 0, ""], ["", f"-{TAXI}", 0, ""],
            ["", f"-{DELIVERY_FUEL}", 0, ""], ["", f"-{FUEL}", 0, ""], ["", "-Accrued to investors", 0, ""],
            ["Repair", f"-{REPAIR}", 0, ""],
            ["Contribution margin", "", 0, ""],
            ["Fixed costs", "", 0, ""], ["", f"-{SALARY}", 0, ""], ["", f"-{PREMISES}", 0, ""],
            ["", "-Depreciation", DEPRECIATION, ""],
            ["Gross profit", "", 0, ""],
            ["Administrative expenses", "", 0, ""], ["", f"-{OTHER_EXPENSES}", 0, ""], ["", f"-{ADMIN}", 0, ""],
            ["", f"-{ADVERTISING}", 0, ""], ["", "-FX difference", 0, ""],
            ["Operating profit", "", 0, ""], ["Tax", "", "", ""], ["", "", "", ""], ["Net profit", "", 0, ""]]


def _cash_flow() -> list:
    operating = [RENT, DELIVERY, EXTRA, DEPOSIT_RECEIVED, DEPOSIT_RETURNED, MAINTENANCE, REPAIR, WASHING, TAXI,
                 DELIVERY_FUEL, FUEL, SALARY, PREMISES, ADVERTISING, ADMIN, OTHER_EXPENSES]
    financing = [CONTRIBUTION, SHAREHOLDER_IN, INVESTOR_PAYOUT, SHAREHOLDER_OUT, OWNER_WITHDRAWAL]
    title, year = REPORT_MONTH.split()
    month = [name for name in MONTH_NAMES if name == title][0]
    first = date(int(year), MONTH_NAMES.index(month) + 1, 1)
    last = date(first.year + first.month // 12, first.month % 12 + 1, 1) - timedelta(days=1)
    return ([["Month", REPORT_MONTH]] + [[wallet, 0] for wallet in (BANK, CASH, CARD, CRYPTO)] +
            [["Cash at start of month", 0]] + [[name, 0] for name in operating] + [["Operating cash flow", 0]] +
            [[PURCHASE, 0], [SALE, 0], ["Investing cash flow", 0]] + [[name, 0] for name in financing] +
            [["Financing cash flow", 0]] +
            [["FX difference", 0], ["Net change in cash", 0], ["Period end", sheet_day(last)],
             ["Cash at end of month", 0]])


def workbook(seed: int = SEED) -> dict:
    """Every sheet of the recalculation, as the owner leaves it before a run."""
    rng = random.Random(seed)
    bikes = _fleet(rng)
    book = Book()
    for bike in bikes:
        for rental in bike.rentals:
            _book_rental(rng, book, bike, rental)
    _running_costs(rng, book, bikes)

    # Investors are paid on the 20th for the month before; one gets an agreed advance.
    accruals = _accruals(book.rows, bikes)
    advance = max(round_to(1.6 * accruals.get((TOMAS, date(TODAY.year, TODAY.month, 1)), 0), 1000), 5000)
    for month in _months():
        payday = month.replace(day=20)
        if payday > TODAY:
            continue
        previous = (month - timedelta(days=1)).replace(day=1)
        for identity, name, _, _ in INVESTORS[1:]:
            amount = int(max(accruals.get((identity, previous), 0), 0) // 100 * 100)
            if identity == TOMAS and month == date(TODAY.year, TODAY.month, 1):
                amount += advance
            if amount:
                book.add(payday, INVESTOR_PAYOUT, amount, wallet_from=BANK, owner=name, order=8)

    lowest = _cash_management(book)
    if min(lowest.values()) < 0:
        raise ValueError(f"Demo data: a wallet went below zero: {lowest}")

    rows = book.sorted()
    statuses = {bike.id: _status(bike) for bike in bikes}
    # Two data errors for the dashboard to point at: rent payments without an end date — the
    # latest one of a rented bike, and an older one of another bike.
    rented = [bike.id for bike in bikes if statuses[bike.id] == "Rented" and bike.id not in OVERDUE]
    latest = max((row for row in rows if row["operation"] == RENT and row["bike"] and row["bike"].id == rented[0]),
                 key=lambda row: row["day"])
    latest["end_date"] = None
    older = next(row for row in rows if row["operation"] == RENT and row["day"].month == 7 and row["day"].year == 2026
                 and row["bike"].id != rented[0])
    older["end_date"] = None

    bike_columns = BIKE_COLUMNS + ["status", "purchase_date", "vehicle_type"]
    bike_rows = [bike_columns]
    history = []
    for bike in bikes:
        owner = bike.owner_on(TODAY)
        share, repair = SHARES[owner]
        record = {column: "" for column in bike_columns}
        record.update(id=bike.id, full_name=bike.full_name, brand=bike.model.brand, model=bike.model.model,
                      color=bike.color, capacity=bike.model.capacity, licence_plate=bike.plate,
                      owner=INVESTOR_NAMES[owner], owner_id=owner, owner_name_snapshot=INVESTOR_NAMES[owner],
                      owner_share=share, repair_share=repair, purchase_price=bike.model.price,
                      status=statuses[bike.id], purchase_date=sheet_day(bike.purchased), vehicle_type="Bike")
        bike_rows.append([record[column] for column in bike_columns])
        if bike.id in TRANSFERRED:
            new_owner, since = TRANSFERRED[bike.id]
            for identity, start in [(bike.owner_id, bike.purchased), (new_owner, since)]:
                history.append([bike.id, INVESTOR_NAMES[identity], identity, SHARES[identity][0],
                                sheet_day(start), SHARES[identity][1]])

    investors = [["owner_id", "Name", "Settled on", "Due at settlement"]]
    investors += [[identity, name, "", ""] for identity, name, _, _ in INVESTORS]

    opening = [["Wallet", "Start date", "Amount", "Deposits"]]
    for wallet in (BANK, CASH, CARD, CRYPTO):
        opening.append([wallet, sheet_day(FIRST_DAY), OPENING[wallet], 0])

    return {
        "Bikes": bike_rows,
        "Investors": investors,
        "Operations": _operation_rows(rows),
        "Operation types": [["Name", "Type"]] + [list(entry) for entry in DIRECTORY],
        "Wallets": [["Wallet"], [BANK], [CASH], [CARD], [CRYPTO]],
        "Opening balances": opening,
        "Bike history": [HISTORY_COLUMNS + ["repair_share"]] + history,
        "Staff": [["Employee", "Start date", "End date", "Monthly salary"]] +
                 [[name, sheet_day(date.fromisoformat(start)), sheet_day(date.fromisoformat(end)) if end else "", salary]
                  for name, start, end, salary in STAFF],
        "Fixed costs": [["Operation", "Monthly amount", "Schedule"], [SALARY, 0, "5th and 20th"],
                        [PREMISES, PREMISES_RENT, "1st"]],
        "Free cash today": [["Item", "Amount"], [BANK, 0], [CASH, 0], [CARD, 0], [CRYPTO, 0], ["TOTAL NOW:", 0],
                            ["Unreturned deposits", 0], ["Accrued but unpaid to investors", 0],
                            ["Upcoming fixed payments", 0], ["Operating reserve", OPERATING_RESERVE], ["TOTAL FREE:", 0]],
        "Investor settlement": [REPORT_COLUMNS + ["comment"],
                                [INVESTOR_NAMES[TOMAS], TOMAS, "", "", "", "",
                                 f"Advance of {advance:,} agreed on 20.{TODAY.month:02d}"]],
        "Month start days": [["Month start", "Month name"]],
        "P&L": _pnl(),
        "Cash flow": _cash_flow(),
    }


# --- recalculation -----------------------------------------------------------------------------

def apply_plan(values: dict, result: Plan) -> dict:
    """The sheet as Google would hold it after the plan was applied."""
    values = copy.deepcopy(values)
    for (sheet, row, column), fields in result.changes.items():
        rows = values[sheet]
        while len(rows) < row:
            rows.append([""] * len(rows[0]))
        while len(rows[row - 1]) < column:
            rows[row - 1].append("")
        rows[row - 1][column - 1] = entered_value(fields)
    columns = result.history_columns
    for entry in result.history_appends:
        row = [""] * max(columns.values())
        for name in ["bike_id", "owner", "owner_id", "owner_share", "repair_share"]:
            row[columns[name] - 1] = "" if entry[name] is None else entry[name]
        row[columns["active_from"] - 1] = serial(entry["active_from"])
        values["Bike history"].append(row)
    return values


def snapshot(values: dict) -> Snapshot:
    return Snapshot(copy.deepcopy(values), copy.deepcopy(values), RUN_AT)


def build(seed: int = SEED) -> tuple[dict, Plan]:
    """The recalculated workbook and the plan that produced it. A second run must plan nothing."""
    values = workbook(seed)
    result = plan(snapshot(values))
    calculated = apply_plan(values, result)
    again = plan(snapshot(calculated))
    if again.changes or again.history_appends:
        raise ValueError(f"Demo data: a second run still plans {len(again.changes)} writes.")
    return calculated, result


def to_json(values: dict) -> str:
    """One sheet row per line: readable diffs when the generator changes."""
    lines = ["{", f'  "taken_at": {json.dumps(RUN_AT.isoformat())},', '  "values": {']
    sheets = []
    for name, rows in values.items():
        body = ",\n".join("      " + json.dumps(row, ensure_ascii=False, allow_nan=False) for row in rows)
        sheets.append(f"    {json.dumps(name, ensure_ascii=False)}: [\n{body}\n    ]")
    lines += [",\n".join(sheets), "  }", "}"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fleet_ledger.demo", description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=Path("demo/snapshot.json"))
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    values, result = build(args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_json(values), encoding="utf-8")
    free = result.reports["Free cash"]
    print(f"{args.out}: {len(values['Operations']) - 1} operations, {len(values['Bikes']) - 1} bikes, "
          f"free cash {free['TOTAL FREE:']:,.0f} THB as of {RUN_AT:%d.%m.%Y}")
    for warning in result.warnings:
        print("⚠", warning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
