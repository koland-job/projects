"""Deposit regressions: wallets, opening deposits and the ledger, without Google or file writes."""
import copy
import unittest

import pandas as pd
import support  # noqa: F401  (puts src/ on the path)

from fleet_ledger.catalog import load_wallets
from fleet_ledger.ledger import Ledger
from fleet_ledger.operations import Operations
from fleet_ledger.writes import Writes


class Run:
    """What a test looks at: wallets, the ledger and the completed operations."""

    def __init__(self, wallets, ledger=None):
        self.wallets, self.ledger = wallets, ledger
        self.operations = ledger.operations if ledger else None


def fixture(openings, ops=(), today='2026-09-30', wallet_balances=None):
    # Each opening is (wallet, currency, date, native amount); the currency comes from the wallet name.
    # Live layout: deposits are a column of the wallet table and start with the wallet.
    rows = [['Wallet', 'Start date (SET ONCE)', 'Amount \n(SET ONCE)', 'Deposits \n(SET ONCE)']]
    for name, currency, day, amount in openings:
        rows.append([name, pd.Timestamp(day), (wallet_balances or {}).get(name, 0), amount])
    operations = []
    signs = {'Deposit received': 1, 'Deposit refund': -1, 'Rent from deposit': 1, 'Damage charge': 1, 'Extra services': 1}
    for i, row in enumerate(ops, 2):
        op = dict(date=pd.Timestamp('2026-09-10'), operation='Deposit received', wallet_from='', wallet_to='', amount_thb=None,
                  amount_cur=None, bike_id=1, bike='Demo', owner='Demo', sheet_row=i)
        op.update(row); op['date'] = pd.Timestamp(op['date']); op['sign'] = signs[op['operation']]
        op['from_deposit'] = op['operation'] in {'Rent from deposit', 'Damage charge'}
        operations.append(op)
    columns = ['date', 'operation', 'wallet_from', 'wallet_to', 'amount_thb', 'amount_cur', 'bike_id', 'bike', 'owner',
               'sheet_row', 'sign', 'from_deposit']
    frame = pd.DataFrame(operations, columns=columns)
    frame['date'] = pd.to_datetime(frame['date'])
    frame['from_deposit'] = frame['from_deposit'].astype(bool)
    for col in ['amount_thb', 'amount_cur']: frame[col] = frame[col].astype(object)
    values = {'Wallets': [['Wallet']] + [[name] for name, *_ in openings], 'Opening balances': rows, 'Operations': []}
    return dict(values=values, frame=frame, today=pd.Timestamp(today))


def execute(fx, ledger=True):
    wallets = load_wallets(fx['values'], fx['today'])
    if not ledger:
        return Run(wallets)
    operations = Operations(fx['frame'], 1, {'total': 1}, [])
    return Run(wallets, Ledger(operations, wallets, Writes(copy.deepcopy(fx['values']))))


def quote(wallet,cur,thb,date='2026-09-01'):
    return dict(operation='Extra services',wallet_to=wallet,amount_cur=cur,amount_thb=thb,date=date,bike_id=None)


class DepositTests(unittest.TestCase):
    def test_start_columns_are_found_by_header_not_position(self):
        ns=fixture([('Cash ฿','THB','2026-09-01',400)],wallet_balances={'Cash ฿':1000})
        for rows in (ns['values']['Opening balances'],):
            for row in rows: row.insert(1,'Comment' if row is rows[0] else 'note')
        run=execute(ns,ledger=False)
        self.assertEqual(run.wallets.balances.iloc[0]['balance'],1000)
        self.assertEqual(run.wallets.deposits.iloc[0]['balance'],400)

    def test_wallet_openings_are_liabilities_not_extra_cash(self):
        ns=execute(fixture([('Cash ฿','THB','2026-09-01',400)],wallet_balances={'Cash ฿':1000}))
        day=pd.Timestamp('2026-09-30')
        self.assertEqual(ns.ledger.wallet_native_at('Cash ฿',day),1000)
        self.assertEqual(ns.ledger.deposits_thb_at(day),400)
        self.assertEqual(ns.ledger.wallet_native_at('Cash ฿',day)-ns.ledger.deposits_thb_at(day),600)
    def test_multiple_currencies_and_wallets(self):
        ns=execute(fixture([('Card ฿','THB','2026-09-01',100),('Cash ฿','THB','2026-09-01',200),('Cash $','USD','2026-09-01',100),('Cash €','EUR','2026-09-01',50)],
                           [quote('Cash $',100,3500),quote('Cash €',100,4000)]))
        self.assertEqual(ns.ledger.deposits_thb_at(pd.Timestamp('2026-09-30')),5800)
    def test_split_refund_has_one_conversion(self):
        ns=execute(fixture([('Cash $','USD','2026-09-01',100)], [quote('Cash $',100,3500),
            dict(wallet_to='Cash $',amount_cur=40,amount_thb=1400),
            dict(operation='Deposit refund',wallet_from='Cash $',amount_cur=120,amount_thb=4200,date='2026-09-11')]))
        moves=ns.ledger.deposit_moves; refund=moves.loc[moves.op_index==2]
        self.assertEqual(len(refund),2)
        self.assertEqual(refund.native.sum(),-120)
        self.assertEqual(refund.thb.sum(),-4200)
        self.assertEqual(ns.ledger.deposits_thb_at(pd.Timestamp('2026-09-30')),700)
    def test_opening_is_not_available_before_its_date(self):
        ns=fixture([('Card ฿','THB','2026-09-01',100),('Cash ฿','THB','2026-09-20',200)],
                   [dict(operation='Deposit refund',wallet_from='Card ฿',amount_thb=150,date='2026-09-10')])
        with self.assertRaisesRegex(ValueError,'not enough'):execute(ns)
    def test_same_currency_pool_can_be_refunded_from_another_wallet(self):
        ns=execute(fixture([('Card ฿','THB','2026-09-01',100),('Cash ฿','THB','2026-09-20',200)],
                   [dict(operation='Deposit refund',wallet_from='Card ฿',amount_thb=150,date='2026-09-21')]))
        self.assertEqual(ns.ledger.deposits_thb_at(pd.Timestamp('2026-09-30')),150)
    def test_events_before_each_wallet_start_are_not_double_counted(self):
        ns=execute(fixture([('Card ฿','THB','2026-09-01',0),('Cash ฿','THB','2026-09-20',200)],
                   [dict(wallet_to='Cash ฿',amount_thb=200,date='2026-09-10')]))
        self.assertEqual(ns.ledger.deposits_thb_at(pd.Timestamp('2026-09-30')),200)
        self.assertEqual(ns.ledger.deposits_thb_at(pd.Timestamp('2026-09-19')),0)
    def test_foreign_row_before_wallet_start_keeps_its_currency(self):
        # A row before the rouble wallet's start is in roubles, not baht, and stays out of the balance.
        ns=execute(fixture([('Card ₽','RUB','2026-09-29',0)],
                   [dict(operation='Extra services',wallet_to='Card ₽',amount_cur=18400,date='2026-09-04',bike_id=None),
                    quote('Card ₽',16260,5500,date='2026-09-09')]))
        row=ns.operations.iloc[0]
        self.assertEqual(row.currency,'RUB')
        self.assertAlmostEqual(row.amount_thb,round(18400*5500/16260,2))
        self.assertEqual(ns.ledger.wallet_native_at('Card ₽',pd.Timestamp('2026-09-30')),0)
    def test_foreign_currency_cannot_cover_baht_refund(self):
        ns=fixture([('Cash ฿','THB','2026-09-01',0),('Cash $','USD','2026-09-01',100)],
                   [quote('Cash $',100,3500),dict(operation='Deposit refund',wallet_from='Cash ฿',amount_thb=100)])
        with self.assertRaisesRegex(ValueError,'not enough'):execute(ns)
    def test_ambiguous_withholding_requires_currency(self):
        ns=fixture([('Cash ฿','THB','2026-09-01',100),('Cash $','USD','2026-09-01',100)],
                   [dict(operation='Rent from deposit',amount_thb=100)])
        with self.assertRaisesRegex(ValueError,'several currencies'):execute(ns)
    def test_unambiguous_foreign_withholding_does_not_move_cash(self):
        ns=execute(fixture([('Cash $','USD','2026-09-01',100)], [quote('Cash $',100,3500),dict(operation='Rent from deposit',amount_cur=20)]))
        self.assertEqual(ns.ledger.deposits_thb_at(pd.Timestamp('2026-09-30')),2800)
        self.assertEqual(ns.operations.iloc[-1].total,700)
        self.assertEqual(ns.operations.iloc[-1].from_native,0)
        self.assertEqual(ns.operations.iloc[-1].to_native,0)
    def test_fx_on_starting_deposits_and_partial_refund(self):
        ns=execute(fixture([('Cash $','USD','2026-09-01',100)], [quote('Cash $',100,3500),
            dict(operation='Deposit refund',wallet_from='Cash $',amount_cur=20,amount_thb=700,date='2026-09-05'),
            quote('Cash $',100,3600,date='2026-09-10')]))
        _,fx=ns.ledger.fx_result(pd.Timestamp('2026-09-01'),pd.Timestamp('2026-09-10'))
        self.assertAlmostEqual(fx,80)
    def test_zero_foreign_openings_need_no_exchange_rate(self):
        ns=execute(fixture([('Cash $','USD','2026-09-01',0)]))
        self.assertEqual(ns.ledger.deposits_thb_at(pd.Timestamp('2026-09-30')),0)
    def test_missing_negative_and_future_start_rejected(self):
        for amount,day,error in [(None,'2026-09-01','amount'),(-1,'2026-09-01','amount'),(0,'2026-10-01','date')]:
            with self.subTest(amount=amount,day=day),self.assertRaisesRegex(ValueError,error):
                execute(fixture([('Cash ฿','THB',day,amount)]),ledger=False)
    def test_duplicate_wallet_row_rejected(self):
        ns=fixture([('Cash ฿','THB','2026-09-01',0)])
        ns['values']['Opening balances'].append(['Cash ฿',pd.Timestamp('2026-09-01'),0,100])
        with self.assertRaisesRegex(ValueError,'exactly one row'):execute(ns,ledger=False)
    def test_deposit_column_required(self):
        ns=fixture([('Cash ฿','THB','2026-09-01',0)])
        ns['values']['Opening balances'][0][3]='Other'
        with self.assertRaisesRegex(ValueError,'Deposits'):execute(ns,ledger=False)
    def test_live_sheet_layout(self):
        # Offset table with an empty first row/column, as in the real sheet.
        ns=fixture([('Cash ฿','THB','2026-09-01',250)])
        rows=ns['values']['Opening balances']
        ns['values']['Opening balances']=[['']*5]+[['']+row for row in rows]
        run=execute(ns,ledger=False)
        self.assertEqual(run.wallets.deposits.balance.tolist(),[250])

if __name__=='__main__':unittest.main()
