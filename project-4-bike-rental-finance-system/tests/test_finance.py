"""Offline financial and dashboard regressions; no credentials or Google writes.
Run: PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
"""
import copy
import sys
import types
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import support  # noqa: F401  (puts src/ on the path)

from fleet_ledger.accruals import Accruals
from fleet_ledger.bikes import Bikes, BikeHistory
from fleet_ledger.catalog import Catalog, Wallets
from fleet_ledger.parse import find_label
from fleet_ledger.reports import pnl
from fleet_ledger.writes import Writes

ROOT = Path(__file__).resolve().parents[1]
SIGNS = {'Rent payment': 1, 'Maintenance & consumables': -1, 'Repair': -1, 'Investor payout': -1, 'Other expenses': -1}


def pnl_run(month, payout_day='2026-10-05', today='2026-10-31', new_label=False, history=None, keep=None):
    # Layout has separate section and expense-label columns, like the real report.
    label = '-Accrued to investors' if new_label else '-Investor payout'
    rows = [
        ['', '', 'Amount', '% of revenue'],
        ['Revenue', '', 0], ['Rent payment', '', 0],
        ['Variable costs', '', 0], ['', '-Maintenance & consumables', 0], ['', '-Repair', 0],
        ['', label, 0], ['Contribution margin', '', 0],
        ['Fixed costs', '', 0], ['', '-Depreciation', 0], ['Gross profit', '', 0],
        ['Administrative expenses', '', 0], ['', '-Other expenses', 0], ['Operating profit', '', 0],
        ['Tax', '', 0], ['Net profit', '', 0],
    ]
    ops = pd.DataFrame([
        dict(date=pd.Timestamp('2026-09-10'), bike_id=1, operation='Rent payment', total=100000., amount_thb=100000.),
        dict(date=pd.Timestamp('2026-09-11'), bike_id=1, operation='Maintenance & consumables', total=-40000., amount_thb=40000.),
        dict(date=pd.Timestamp(payout_day), bike_id=None, operation='Investor payout', total=-20000., amount_thb=20000.),
    ])
    selected = ops.loc[ops.date.dt.month == month]
    if keep:
        selected = keep(selected)
    history = [{'repair_share': 1., **row} for row in history or
               [dict(bike_id=1, owner='Demo', owner_share=1/3, active_from=pd.Timestamp('2026-01-01'), sheet_row=2)]]
    accruals = Accruals(Bikes(pd.DataFrame([dict(id=1, full_name='Demo', repair_share=1.)]), {}),
                        BikeHistory(pd.DataFrame(history), {}), {})
    writes = Writes({'P&L': copy.deepcopy(rows)})
    result = pnl(rows, selected, {'month': pd.Timestamp(2026, month, 1), 'end': pd.Timestamp(today)},
                 Catalog(SIGNS, set(), set()), Wallets({'Cash ฿': 'THB'}, None, None), None, accruals,
                 pd.Timestamp(today), writes)
    return result, writes


# Stub only the I/O boundary; use the actual parser, ranges and endpoint.
sys.path.insert(0, str(ROOT/'dashboard/backend'))
config = types.ModuleType('app.config')
config.BACKEND_DIR = ROOT/'dashboard/backend'
config.TIMEZONE = 'Etc/GMT-7'
config.DEMO = False
config.RENT_OPERATIONS = ['Rent payment','Rent from deposit']
config.REVENUE_OPERATIONS = config.RENT_OPERATIONS + ['Delivery (client)','Extra services']
config.RENTED_BIKE_STATUS = 'Rented'
sys.modules['app.config'] = config
sheets = types.ModuleType('app.sheets_client')
ROWS = {}
sheets.get_sheet_rows = lambda name: ROWS[name]
sheets.clear_cache = lambda: None
sheets.get_cache_loaded_at = lambda: '2026-09-27T10:00:00+00:00'
sheets.get_last_modified_iso = sheets.get_cache_loaded_at
sys.modules['app.sheets_client'] = sheets
from app import main, parsing, ranges


def dashboard_rows(today=date(2026,9,27)):
    headers=['id','full_name','owner','status','purchase_price','roi','brand','model','color','capacity','licence_plate','vehicle_type']
    colors=['White','Black','Red and black','Blue metallic','Dark blue matte','Burgundy','Graphite','Light green','dark blue','cosmic dust','#abc','Beige','White pearl','black/chrome']
    statuses=['Rented']*5+['Available','In repair','Idle','Reserved','For sale','Sold','Retired','Available','Available']
    bikes=[headers]+[[i+1,f'Demo Bike {i+1}','Demo Investor',statuses[i],60000,0.25,'Demo',f'Bike {i+1}',colors[i],125,str(1000+i),'Bike'] for i in range(len(colors))]
    ops=[['date','operation','bike_id','total','end_date']]
    def op(ago,bike,total=1000,end=None,operation='Rent payment'):
        ops.append([(today-timedelta(days=ago)-date(1899,12,30)).days,operation,bike if bike is not None else "",total,(end-date(1899,12,30)).days if end else ''])
    # Data lags today by five days. End dates: overdue 7, overdue 3, today, missing, no payment.
    op(5,1,end=today-timedelta(days=7)); op(5,2,end=today-timedelta(days=3)); op(5,3,end=today)
    op(5,4); op(-2,1,end=today+timedelta(days=40)) # future payment must not hide overdue
    op(5,None,500,operation='Extra services')
    for d in range(6,80): op(d,6,100+d,end=today+timedelta(days=3))
    op(400,6,100,end=today-timedelta(days=360))
    return {'Bikes':bikes,'Operations':ops,
            'Investors':[['owner_id','Name'],[1,'Demo Investor'],[2,'Paid Ahead']],
            'Free cash today':[['Item','Amount'],['Card ฿',90000],['TOTAL NOW:',90000],['Unreturned deposits',10000],['Accrued but unpaid to investors',20000],['Upcoming fixed payments',5000],['Operating reserve',5000],['TOTAL FREE:',50000]],
            'Investor settlement':[['owner','to_be_paid'],['Demo Investor',20000],['Paid Ahead',-1000]]}


class PnlTests(unittest.TestCase):
    def test_september_accrual_october_payment(self):
        # Rent 100000 × 1/3 minus maintenance 40000 × repair_share 1.0.
        for month, expense, profit in [(9, 100000/3-40000, 60000-(100000/3-40000)), (10, 0, 0)]:
            result, _ = pnl_run(month)
            self.assertAlmostEqual(result.investor_expense, expense)
            self.assertAlmostEqual(result.values['Net profit'], profit)
            self.assertIn('-Accrued to investors', result.values)

    def test_payout_date_does_not_change_profit(self):
        profits = [pnl_run(9, payout)[0].values['Net profit'] for payout in ['2026-09-15', '2026-10-05']]
        self.assertEqual(*profits)

    def test_future_accrual_excluded(self):
        self.assertEqual(pnl_run(9, today='2026-09-01')[0].investor_expense, 0)

    def test_future_rows_leave_revenue_costs_and_accrual_together(self):
        # Rent on 10.09 has happened; maintenance on 11.09 has not yet.
        result, _ = pnl_run(9, today='2026-09-10')
        self.assertEqual(result.values['Revenue'], 100000)
        self.assertEqual(result.values['-Maintenance & consumables'], 0)
        self.assertAlmostEqual(result.investor_expense, 100000/3)

    def test_new_label_and_repeat_are_supported(self):
        for migrated in [False, True]:
            first, writes = pnl_run(9, new_label=migrated)
            again, _ = pnl_run(9, new_label=True)
            self.assertEqual(again.values, first.values)
            self.assertEqual(bool(writes.changes), not migrated)

    def test_negative_accrual_is_not_lost(self):
        # A repair without rent: the investor's repair_share (1.0 here) is a negative accrual.
        result, _ = pnl_run(9, keep=lambda d: d.loc[d.operation == 'Maintenance & consumables'].assign(operation='Repair'))
        self.assertAlmostEqual(result.investor_expense, -40000)

    def test_ratio_and_write_targets_follow_new_label(self):
        result, _ = pnl_run(9)
        self.assertEqual(result.ratios['-Accrued to investors'], '')
        self.assertAlmostEqual(result.ratios['-Maintenance & consumables'], (40000+100000/3-40000)/100000)
        for label in result.values:
            find_label(result.rows, label, 'P&L')

    def test_history_splits_accrual(self):
        result, _ = pnl_run(9, history=[
            dict(bike_id=1, owner='A', owner_share=.5, active_from=pd.Timestamp('2026-01-01'), sheet_row=2),
            dict(bike_id=1, owner='B', owner_share=.25, active_from=pd.Timestamp('2026-09-11'), sheet_row=3)])
        self.assertEqual(result.investor_expense, 10000)  # A: 100000 × 0.5; B: −40000 maintenance × repair_share 1.0


class DashboardTests(unittest.TestCase):
    def setUp(self):
        ROWS.clear(); ROWS.update(dashboard_rows()); parsing._memo.clear()
        self.clock=patch.object(ranges,'today',return_value=date(2026,9,27)); self.clock.start()
    def tearDown(self): self.clock.stop()
    def dashboard(self,key='last_30_days',**kwargs):
        return main.api_dashboard(range=key,date_from=kwargs.get('date_from'),date_to=kwargs.get('date_to'),compare=kwargs.get('compare','prev'))
    def test_overdue_today_future_payment_and_missing_data(self):
        d=self.dashboard(); bikes={b['bike_id']:b for b in d['bikes_revenue']['bikes']}
        self.assertEqual(d['data_as_of'],'2026-09-22'); self.assertEqual(d['payment_as_of'],'2026-09-27')
        self.assertEqual((bikes[1]['payment_state'],bikes[1]['overdue_days']),('overdue',7))
        self.assertEqual(bikes[2]['overdue_days'],3); self.assertEqual(bikes[3]['payment_state'],'ok')
        self.assertEqual(bikes[4]['payment_state'],'no_term'); self.assertEqual(bikes[5]['payment_state'],'no_ops')
        self.assertIsNone(bikes[6]['payment_state'])
        self.assertEqual(bikes[1]['last_paid_at'],'2026-09-22')
    def test_overdue_is_independent_of_period(self):
        a=self.dashboard('last_7_days'); b=self.dashboard('all_time')
        self.assertEqual([(x['bike_id'],x['overdue_days']) for x in sorted(a['bikes_revenue']['bikes'],key=lambda b:b['bike_id'])],[(x['bike_id'],x['overdue_days']) for x in sorted(b['bikes_revenue']['bikes'],key=lambda b:b['bike_id'])])
    def test_revenue_totals_and_investor_advance(self):
        d=self.dashboard()
        self.assertEqual(sum(b['revenue'] for b in d['bikes_revenue']['bikes'])+500,d['period']['revenue']['value'])
        self.assertEqual(sum(p['revenue'] for p in d['daily']['current'] if not p['after_data']),d['period']['revenue']['value'])
        self.assertEqual(d['now']['investor_dues']['owed'],20000)
        self.assertEqual(d['now']['fleet']['total'],sum(s['count'] for s in d['now']['fleet']['by_status']))
    def test_calendar_graph_does_not_count_future_operations(self):
        for key in ['this_month','this_year','all_time']:
            d=self.dashboard(key)
            self.assertEqual(sum(p['revenue'] for p in d['daily']['current'] if not p['after_data']),d['period']['revenue']['value'],key)
    def test_investor_waiting_for_id_does_not_break_dashboard(self):
        ROWS['Investors'].append(['','New person']); parsing._memo.clear()
        self.assertEqual(self.dashboard()['now']['investor_dues']['owed'],20000)
    def test_iso_text_date_is_not_read_day_first(self):
        self.assertEqual(parsing.as_date('2026-10-04'),pd.Timestamp('2026-10-04'))
        self.assertEqual(parsing.as_date('04.10.2026'),pd.Timestamp('2026-10-04'))
    def test_empty_operations(self):
        ROWS['Operations']=ROWS['Operations'][:1]; parsing._memo.clear()
        d=self.dashboard(); self.assertEqual(d['period']['revenue']['value'],0)
    def test_same_week_future_row_does_not_enter_graph(self):
        ops=pd.DataFrame({'date':pd.to_datetime(['2026-09-22','2026-09-25']), 'total':[100,900]})
        points=main._bucket_series(ops,date(2026,9,21),date(2026,9,27),'week',date(2026,9,22))
        self.assertEqual(sum(p['revenue'] for p in points),100)

    def test_new_day_increments_overdue(self):
        before=self.dashboard()
        with patch.object(ranges,'today',return_value=date(2026,9,28)):
            after=self.dashboard()
        def days(d): return next(b['overdue_days'] for b in d['bikes_revenue']['bikes'] if b['bike_id']==1)
        self.assertEqual(days(after),days(before)+1)

    def test_missing_plate_placeholder(self):
        ROWS['Bikes'][1][-2]='-'
        d=self.dashboard()
        self.assertEqual(next(b['plate'] for b in d['bikes_revenue']['bikes'] if b['bike_id']==1),'')

    def test_partial_week_uses_data_cutoff(self):
        series=main._bucket_series(parsing.load_operations(),date(2026,9,21),date(2026,9,27),'week',date(2026,9,22))
        self.assertTrue(series[0]['partial']); self.assertEqual(series[0]['actual_days'],2)

if __name__=='__main__': unittest.main()
