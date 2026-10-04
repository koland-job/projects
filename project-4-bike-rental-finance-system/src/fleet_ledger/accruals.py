"""Bike ROI, investor accruals and debts, and the "Investor settlement" report."""
import pandas as pd

from .bikes import Bikes, BikeHistory
from .investors import InvestorDirectory
from .names import DAMAGE_WITHHOLDING, INVESTOR_PAYOUT, MAINTENANCE, PERCENT, RENT_OPERATIONS, REPAIR
from .parse import table, text
from .writes import Writes

ROI_EXPENSES = [MAINTENANCE, REPAIR]
REPORT_COLUMNS = ["owner", "owner_id", "total_bike_worth", "total_earned", "to_be_paid", "roi"]


class Accruals:
    """Investor accrual = rent × owner_share − (repair + maintenance − damage withheld) × repair_share.

    Damage withheld compensates the bike's costs, so it is subtracted from them.
    Accruals follow the periods of "Bike history": the owner, owner_share and
    repair_share of the period apply, so a change never rewrites earlier months.
    """

    def __init__(self, bikes: Bikes, history: BikeHistory, settlements: dict):
        self.bikes = bikes
        self.history = history.frame
        self.settlements = settlements

    def by_bike(self, rows: pd.DataFrame) -> pd.DataFrame:
        """(owner, bike_id, accrual) per history period with operations."""
        with_bike = rows.loc[rows["bike_id"].notna()]
        identity_field = "owner_id" if "owner_id" in self.history else "owner"
        records = []
        for bike_id, bike_rows in with_bike.groupby("bike_id"):
            bike_id = int(bike_id)
            history = self.history.loc[self.history["bike_id"] == bike_id].sort_values(["active_from", "sheet_row"])
            starts = [value.normalize() for value in history["active_from"]]
            for position, (_, period) in enumerate(history.iterrows()):
                start = starts[position]
                end = starts[position + 1] if position + 1 < len(starts) else pd.Timestamp.max
                period_rows = bike_rows.loc[(bike_rows["date"] >= start) & (bike_rows["date"] < end)]
                if period_rows.empty:
                    continue
                rent = period_rows.loc[period_rows["operation"].isin(RENT_OPERATIONS), "total"].sum()
                costs = period_rows.loc[period_rows["operation"].isin([REPAIR, MAINTENANCE]), "amount_thb"].sum()
                withheld = period_rows.loc[period_rows["operation"] == DAMAGE_WITHHOLDING, "amount_thb"].sum()
                costs_net = costs - withheld
                repair_share = period["repair_share"]
                if abs(costs_net) > 1e-9 and pd.isna(repair_share):
                    raise ValueError(f"Bikes: fill repair_share for bike {self.bikes.label(bike_id)} — "
                                     f"it has repair or maintenance costs since {start:%d.%m.%Y}.")
                share = period["owner_share"]
                if abs(rent) > 1e-9 and pd.isna(share):
                    raise ValueError(
                        f"Bike history: bike {self.bikes.label(bike_id)} has no owner_share for the period from {start:%d.%m.%Y}."
                    )
                accrual = ((rent * share if abs(rent) > 1e-9 else 0.0)
                           - (costs_net * repair_share if abs(costs_net) > 1e-9 else 0.0))
                records.append({"owner": period[identity_field], "bike_id": bike_id, "accrual": accrual})
        return pd.DataFrame(records, columns=["owner", "bike_id", "accrual"])

    def by_owner(self, rows: pd.DataFrame) -> pd.Series:
        records = self.by_bike(rows)
        if records.empty:
            return pd.Series(dtype=float)
        return records.groupby("owner")["accrual"].sum()

    def due(self, identity, rows: pd.DataFrame) -> tuple[float, float]:
        """Accrued and paid to an investor. After a settlement — its debt plus later operations."""
        settlement = self.settlements.get(identity)
        opening = 0.0
        if settlement:
            rows = rows.loc[rows["date"] > settlement["date"]]
            opening = settlement["due"]
        accrued = opening + self.by_owner(rows).get(identity, 0.0)
        payouts = rows.loc[(rows["owner_id"] == identity) & (rows["operation"] == INVESTOR_PAYOUT)]
        return accrued, payouts["amount_thb"].sum()


def investor_ids(bikes: Bikes, history: BikeHistory, current: pd.DataFrame) -> list:
    # Former owners stay in the report: they may still be owed rent.
    return sorted({int(value) for value in [*bikes.frame["owner_id"], *history.frame["owner_id"], *current["owner_id"]]
                   if not pd.isna(value)})


def bike_roi(bikes: Bikes, current: pd.DataFrame, writes: Writes) -> None:
    """ROI = (rent − maintenance − repair + damage withheld) / purchase price, all time up to today.

    Only costs with a bike count: shared costs cannot be split between bikes fairly.
    """
    frame = bikes.frame
    with_bike = current.loc[current["bike_id"].notna()]
    rent = current.loc[current["operation"].isin(RENT_OPERATIONS)].groupby("bike_id")["total"].sum()
    costs = with_bike.loc[with_bike["operation"].isin(ROI_EXPENSES)]
    withheld = with_bike.loc[with_bike["operation"] == DAMAGE_WITHHOLDING]
    expenses = costs.groupby("bike_id")["amount_thb"].sum().sub(
        withheld.groupby("bike_id")["amount_thb"].sum(), fill_value=0)
    frame["rental_income"] = frame["id"].map(rent).fillna(0)
    frame["roi_expenses"] = frame["id"].map(expenses).fillna(0)
    frame["roi"] = (frame["rental_income"] - frame["roi_expenses"]) / frame["purchase_price"].replace(0, float("nan"))
    for _, bike in frame.iterrows():
        writes.put("Bikes", bike["sheet_row"], bikes.columns["full_name"], bike["full_name"])
        writes.put("Bikes", bike["sheet_row"], bikes.columns["roi"], bike["roi"], number_format=PERCENT)


def investor_report(accruals: Accruals, investors: InvestorDirectory, ids: list, current: pd.DataFrame):
    """One row per investor; and the investors whose ROI is empty because a bike has no price.

    total_earned — accrued for all time (bikes since sold or transferred included). ROI —
    accrued on the bikes owned now, over their ownership, divided by their price.
    """
    bikes = accruals.bikes.frame
    earnings_by_bike = accruals.by_bike(current)
    records, missing = [], []
    for identity in ids:
        owner = investors.name(identity)
        owned = bikes.loc[bikes["owner_id"] == identity]
        price = owned["purchase_price"].sum()
        earnings = earnings_by_bike.loc[earnings_by_bike["owner"] == identity]
        earned_on_owned = earnings.loc[earnings["bike_id"].isin(owned["id"]), "accrual"].sum()
        accrued, paid = accruals.due(identity, current)
        # The total ROI holds only if every bike of the owner has a price.
        missing_prices = owned.loc[owned["purchase_price"].isna(), "full_name"]
        roi = earned_on_owned / price if price and missing_prices.empty else None
        if not missing_prices.empty:
            missing.append({"Owner": owner, "Bikes without price": "; ".join(missing_prices)})
        records.append({"owner": owner, "owner_id": identity, "total_bike_worth": price,
                        "total_earned": earnings["accrual"].sum(), "to_be_paid": accrued - paid, "roi": roi})
    return pd.DataFrame(records, columns=REPORT_COLUMNS), missing


def write_investor_report(report: pd.DataFrame, rows: list[list], investors: InvestorDirectory, writes: Writes) -> None:
    sheet = "Investor settlement"
    old = table(rows, REPORT_COLUMNS, sheet)
    # Comments and other extra fields follow the investor, not the row number.
    extra_fields = [name for name in old.columns if name not in report.columns]
    old_by_owner = {}
    for _, previous in old.frame.iterrows():
        owner = text(previous["owner"]).strip()
        if not owner:
            if any(text(previous[name]).strip() for name in extra_fields):
                raise ValueError(f"Row {previous['sheet_row']} has a comment but no owner. Fill owner before the rows are compacted.")
            continue
        identity = investors.resolve_record(previous, sheet)
        if identity in old_by_owner:
            raise ValueError(f"Investor repeated in the report: {owner}")
        old_by_owner[identity] = previous

    # Rows are written right below the header and the leftover tail is cleared.
    # Physical sheet rows are never deleted.
    old_count = len(rows) - old.header
    for offset in range(max(len(report), old_count)):
        row = old.header + 1 + offset
        investor = report.iloc[offset] if offset < len(report) else None
        for name in report.columns:
            value = investor[name] if investor is not None else ""
            writes.put(sheet, row, old.columns[name], value, number_format=PERCENT if name == "roi" else None)
        previous = old_by_owner.get(investor["owner_id"], {}) if investor is not None else {}
        for name in extra_fields:
            writes.put(sheet, row, old.columns[name], previous.get(name, ""))
