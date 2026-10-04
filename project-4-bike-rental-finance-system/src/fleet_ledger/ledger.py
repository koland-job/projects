"""Money: row currencies, deposits per bike, exchange rates, wallet balances and FX result."""
import pandas as pd

from .catalog import Wallets
from .names import BASE_CURRENCY, DAMAGE_WITHHOLDING, DEPOSIT_RECEIVED, DEPOSIT_RENT, DEPOSIT_RETURNED
from .operations import Operations
from .parse import has
from .writes import Writes

ONE_DAY = pd.Timedelta(days=1)
# Same day: deposits received come before anything taken from them, then row order.
DEPOSIT_ORDER = {DEPOSIT_RECEIVED: 0, DEPOSIT_RENT: 1, DAMAGE_WITHHOLDING: 1, DEPOSIT_RETURNED: 2}


class Ledger:
    """Completes operations with currency, baht amounts and totals; answers balance questions.

    Builds on Operations.frame in place: adds currency, rate, from_native, to_native and
    fills amount_thb and total. Writes the total of every operation row.
    """

    def __init__(self, operations: Operations, wallets: Wallets, writes: Writes):
        self.operations = operations.frame
        self.wallets = wallets
        self.warnings = []
        self._row_currencies()
        deposit_moves = self._deposits()
        self._amounts()
        self._rates()
        self._fill_baht()
        for _, operation in self.operations.iterrows():
            writes.put("Operations", operation["sheet_row"], operations.columns["total"], operation["total"])
        self.deposit_moves = self._deposit_moves_thb(deposit_moves)
        self.cash = self.operations.loc[~self.operations["from_deposit"]]
        self.start_balance = dict(zip(wallets.balances["wallet"], wallets.balances["balance"]))
        self.wallet_start = wallets.start

    # --- 4.1 row currency and deposits per bike -------------------------------------------

    def _wallet_currency(self, wallet):
        # A row is always in its wallet's currency, also before the wallet's start date:
        # such rows go to P&L and revenue but not into balances (date filter below).
        return self.wallets.currency[wallet] if wallet else None

    def _row_currencies(self):
        # Baht, or the one foreign currency of the row's wallets. "From deposit" without
        # wallet_from gets its currency from the bike's deposit.
        currencies = {}
        for index, operation in self.operations.iterrows():
            side_from = self._wallet_currency(operation["wallet_from"])
            if operation["from_deposit"]:
                currencies[index] = side_from
                continue
            side_to = self._wallet_currency(operation["wallet_to"])
            foreign = {side for side in (side_from, side_to) if side} - {BASE_CURRENCY}
            if len(foreign) > 1:
                raise ValueError(f"Operations, row {operation['sheet_row']}: a direct {side_from} → {side_to} exchange "
                                 "is not supported. Record it as two transfers through a baht wallet "
                                 "on the same day.")
            currencies[index] = foreign.pop() if foreign else BASE_CURRENCY
        self.operations["currency"] = pd.Series(currencies, dtype=object)

    def _native_amount(self, index, currency):
        # The row amount in the deposit/wallet currency: baht for THB, amount_cur otherwise.
        field = "amount_thb" if currency == BASE_CURRENCY else "amount_cur"
        value = self.operations.at[index, field]
        if not has(value):
            raise ValueError(f"Operations, row {self.operations.at[index, 'sheet_row']}: "
                             f"fill {field} — the amount in {currency}.")
        return value

    def _deposits(self) -> list:
        # A deposit is owed to the client in the currency it was left in. Opening balances
        # are per wallet; only already started balances of the same currency are drawn on.
        # Money may be returned from another wallet of that currency.
        operations, wallets = self.operations, self.wallets
        rows = operations.loc[operations["operation"].isin(DEPOSIT_ORDER)].copy()
        rows["order"] = rows["operation"].map(DEPOSIT_ORDER)
        rows = rows.sort_values(["date", "order", "sheet_row"])
        deposit_start, first_start = wallets.deposit_start, wallets.deposit_start_date

        balance = {}
        pools = {row.wallet: {"start_date": row.start_date, "currency": row.currency, "remaining": row.balance}
                 for row in wallets.deposits.itertuples()}
        moves = [{"date": row.start_date, "bike_id": None, "currency": row.currency,
                  "native": row.balance, "op_index": None}
                 for row in wallets.deposits.itertuples()]
        for index, operation in rows.iterrows():
            row, day = operation["sheet_row"], operation["date"]
            wallet = operation["wallet_to"] if operation["operation"] == DEPOSIT_RECEIVED else operation["wallet_from"]
            # Events before this wallet's start are already inside its opening balance.
            if day < deposit_start.get(wallet, first_start):
                continue
            bike_id = int(operation["bike_id"])
            open_deposits = {currency: amount for (bike, currency), amount in balance.items()
                             if bike == bike_id and amount > 0.005}
            eligible = {name: pool for name, pool in pools.items()
                        if pool["start_date"] <= day and pool["remaining"] > 0.005}
            currency = operations.at[index, "currency"]
            if currency is None:
                candidates = set(open_deposits) or {pool["currency"] for pool in eligible.values()}
                if len(candidates) == 1:
                    currency = next(iter(candidates))
                elif not candidates:
                    raise ValueError(f"Operations, row {row}: the bike has no open deposit and there is no opening balance — "
                                     f'nothing to take "{operation["operation"]}" from.')
                else:
                    raise ValueError(f"Operations, row {row}: deposits are held in several currencies "
                                     f"({', '.join(sorted(candidates))}) — fill wallet_from to pick the currency.")
                operations.at[index, "currency"] = currency
            amount = self._native_amount(index, currency)
            key = (bike_id, currency)
            if operation["operation"] == DEPOSIT_RECEIVED:
                balance[key] = balance.get(key, 0.0) + amount
                moves.append({"date": day, "bike_id": bike_id, "currency": currency, "native": amount, "op_index": index})
                continue

            available = balance.get(key, 0.0)
            from_bike = min(available, amount)
            rest = amount - from_bike
            same_currency = [(name, pool) for name, pool in eligible.items() if pool["currency"] == currency]
            if rest > sum(pool["remaining"] for _, pool in same_currency) + 0.005:
                held = ", ".join(f"{value:,.2f} {cur}" for cur, value in sorted(open_deposits.items())) or "nothing"
                raise ValueError(f"Operations, row {row}: the bike's deposit holds {held}; opening balances in {currency} "
                                 f'are not enough for "{operation["operation"]}" of {amount:,.2f} {currency}.')
            # The named wallet's balance first, then the others of the same currency by date.
            for name, pool in sorted(same_currency, key=lambda item: (item[0] != wallet, item[1]["start_date"], item[0])):
                used = min(rest, pool["remaining"])
                if used > 0:
                    pool["remaining"] -= used
                    rest -= used
                    moves.append({"date": day, "bike_id": None, "currency": currency, "native": -used, "op_index": index})
            balance[key] = available - from_bike
            if from_bike > 0:
                moves.append({"date": day, "bike_id": bike_id, "currency": currency, "native": -from_bike, "op_index": index})

        without_currency = operations.loc[operations["currency"].isna(), "sheet_row"]
        if not without_currency.empty:
            raise ValueError(f"Operations, rows {list(without_currency)}: taken from a deposit before the deposits' "
                             "start date in Opening balances — fill wallet_from.")
        return moves

    # --- 4.2 exchange rates and baht amounts ---------------------------------------------

    def _amounts(self):
        # A baht row — amount_thb only; a foreign row — amount_cur required, amount_thb is
        # its price in baht if known; exchanging baht for a currency — both amounts.
        for _, operation in self.operations.iterrows():
            row, currency = operation["sheet_row"], operation["currency"]
            if currency == BASE_CURRENCY:
                if not has(operation["amount_thb"]):
                    raise ValueError(f"Operations, row {row}: fill amount_thb.")
                if has(operation["amount_cur"]):
                    raise ValueError(f"Operations, row {row}: the wallet is in baht — leave amount_cur empty.")
                continue
            if not has(operation["amount_cur"]):
                raise ValueError(f"Operations, row {row}: fill amount_cur — the amount in {currency}.")
            sides = {self._wallet_currency(operation[side]) for side in ("wallet_from", "wallet_to")}
            if operation["sign"] == 0 and BASE_CURRENCY in sides and not has(operation["amount_thb"]):
                raise ValueError(f"Operations, row {row}: exchanging {currency} and baht — fill both amounts: "
                                 "amount_cur and amount_thb.")

    def _rates(self):
        # The exchange rate comes only from rows where the owner entered both amounts.
        self.foreign = self.operations.loc[self.operations["currency"] != BASE_CURRENCY]
        quoted = self.foreign.loc[self.foreign["amount_thb"].map(has)].copy()
        quoted["rate"] = quoted["amount_thb"].astype(float) / quoted["amount_cur"].astype(float)
        self.rate_history = {
            currency: group.sort_values(["date", "sheet_row"])[["date", "rate", "sheet_row"]].reset_index(drop=True)
            for currency, group in quoted.groupby("currency")
        }
        # A jump of more than 5% from the previous rate of the same currency is likely a typo.
        for currency, history in self.rate_history.items():
            for previous, current in zip(history.itertuples(), history.iloc[1:].itertuples()):
                if abs(current.rate / previous.rate - 1) > 0.05:
                    self.warnings.append(
                        f"The rate differs from the previous one by more than 5% — check the amounts: row {current.sheet_row}, "
                        f"{currency} {current.rate:.4f} (previous {previous.rate:.4f}).")

    def rate_at(self, currency, day) -> float:
        # The last rate on or before the day; before the first rate — the first known one.
        if currency == BASE_CURRENCY:
            return 1.0
        history = self.rate_history.get(currency)
        if history is None:
            rows = sorted(self.foreign.loc[self.foreign["currency"] == currency, "sheet_row"])
            raise ValueError(f"No {currency} rate at all: a rate comes from rows with both amounts filled. "
                             f"Fill amount_thb (the value in baht) in at least one {currency} row "
                             f'in "Operations": {", ".join(map(str, rows))}.')
        known = history.loc[history["date"] <= day]
        return float(known.iloc[-1]["rate"] if not known.empty else history.iloc[0]["rate"])

    def value_thb(self, amount, currency, day) -> float:
        return 0.0 if abs(amount) < 1e-9 else amount * self.rate_at(currency, day)

    def _fill_baht(self):
        operations = self.operations
        # An empty amount_thb = the foreign amount × the last rate on the row's date.
        for index, operation in self.foreign.iterrows():
            if not has(operation["amount_thb"]):
                operations.at[index, "amount_thb"] = round(
                    operation["amount_cur"] * self.rate_at(operation["currency"], operation["date"]), 2)
        operations["amount_thb"] = operations["amount_thb"].astype(float)
        operations["rate"] = [row.amount_thb / row.amount_cur if row.currency != BASE_CURRENCY else 1.0
                              for row in operations.itertuples()]

        def side_native(operation, wallet):
            # How much left or entered the wallet, in that wallet's currency.
            if not wallet or operation["from_deposit"]:
                return 0.0
            if self._wallet_currency(wallet) == BASE_CURRENCY:
                return operation["amount_thb"]
            return operation["amount_cur"]

        operations["from_native"] = [side_native(op, op["wallet_from"]) for _, op in operations.iterrows()]
        operations["to_native"] = [side_native(op, op["wallet_to"]) for _, op in operations.iterrows()]
        operations["total"] = operations["amount_thb"] * operations["sign"]

    def _deposit_moves_thb(self, records: list) -> pd.DataFrame:
        moves = pd.DataFrame(records)
        operations = self.operations
        # Opening foreign deposits are valued at the start date. A movement uses the rate of
        # its operation, pro rata: one return may close a bike's deposit and several
        # wallets' opening balances at once.
        moves["thb"] = [
            move.native if move.currency == BASE_CURRENCY
            else self.value_thb(move.native, move.currency, move.date) if pd.isna(move.op_index)
            else operations.at[int(move.op_index), "amount_thb"] * move.native / operations.at[int(move.op_index), "amount_cur"]
            for move in moves.itertuples()
        ]
        return moves

    # --- 4.3 balances and FX --------------------------------------------------------------

    def wallet_native_at(self, wallet, day) -> float:
        # Wallet balance in its currency at the end of the day; the day before start — the opening amount.
        cash = self.cash
        moves = cash.loc[(cash["date"] >= self.wallet_start[wallet]) & (cash["date"] <= day)]
        return (self.start_balance[wallet]
                + moves.loc[moves["wallet_to"] == wallet, "to_native"].sum()
                - moves.loc[moves["wallet_from"] == wallet, "from_native"].sum())

    def wallet_flow_thb(self, wallet, first_day, last_day) -> float:
        # Wallet movement over a period in baht, at the operations' own rates.
        cash = self.cash
        moves = cash.loc[(cash["date"] >= max(first_day, self.wallet_start[wallet])) & (cash["date"] <= last_day)]
        return (moves.loc[moves["wallet_to"] == wallet, "amount_thb"].sum()
                - moves.loc[moves["wallet_from"] == wallet, "amount_thb"].sum())

    def deposits_native_at(self, day) -> dict:
        # Unreturned deposits at the end of the day: {(bike_id, currency): amount in that currency}.
        moves = self.deposit_moves.loc[self.deposit_moves["date"] <= day]
        balances = moves.groupby(["bike_id", "currency"], dropna=False)["native"].sum()
        return {key: amount for key, amount in balances.items() if abs(amount) > 0.005}

    def deposits_thb_at(self, day) -> float:
        return sum(self.value_thb(amount, currency, day) for (_, currency), amount in self.deposits_native_at(day).items())

    def fx_result(self, first_day, last_day) -> tuple[float, float]:
        # FX result over a period in baht (plus — gain): wallets — revaluation of money,
        # deposits — revaluation of what we owe clients. Ours = wallets − deposits: currency
        # we must give back carries no risk.
        wallets_fx = 0.0
        for wallet, currency in self.wallets.currency.items():
            start = max(first_day, self.wallet_start[wallet])
            if currency == BASE_CURRENCY or start > last_day:
                continue
            opening = self.value_thb(self.wallet_native_at(wallet, start - ONE_DAY), currency, start - ONE_DAY)
            closing = self.value_thb(self.wallet_native_at(wallet, last_day), currency, last_day)
            wallets_fx += closing - opening - self.wallet_flow_thb(wallet, start, last_day)

        deposits_fx = 0.0
        start = max(first_day, self.wallets.deposit_start_date)
        foreign_moves = self.deposit_moves.loc[self.deposit_moves["currency"] != BASE_CURRENCY]
        if start <= last_day:
            for currency, moves in foreign_moves.groupby("currency"):
                opening = self.value_thb(moves.loc[moves["date"] <= start - ONE_DAY, "native"].sum(), currency, start - ONE_DAY)
                closing = self.value_thb(moves.loc[moves["date"] <= last_day, "native"].sum(), currency, last_day)
                flow = moves.loc[(moves["date"] >= start) & (moves["date"] <= last_day), "thb"].sum()
                deposits_fx += closing - opening - flow
        # To satang: otherwise float noise like −9e-13 reaches the report.
        return round(wallets_fx, 2), round(deposits_fx, 2)
