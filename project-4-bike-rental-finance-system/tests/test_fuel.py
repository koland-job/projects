"""Two-way "Fuel" and a deposit refunded in another currency; no Google, no writes."""
import copy
import unittest

import pandas as pd
import support  # noqa: F401  (puts src/ on the path)

from fleet_ledger.bikes import Bikes
from fleet_ledger.catalog import Wallets, load_catalog
from fleet_ledger.investors import InvestorDirectory
from fleet_ledger.operations import load_operations
from fleet_ledger.writes import Writes

DIRECTORY = [['Name', 'Type'], ['Rent payment', 'Income'], ['Rent from deposit', 'Income'],
             ['Damage charge', 'Income'], ['Deposit received', 'Income'],
             ['Deposit refund', 'Expense'], ['Fuel', 'Income/Expense'], ['Repair', 'Expense'],
             ['Transfer', 'Transfer']]
HEADER = ['date', 'operation', 'wallet_from', 'wallet_to', 'amount_thb', 'amount_cur',
          'bike', 'bike_id', 'owner', 'total', 'owner_id', 'owner_name_snapshot']


class OneOwner:
    """History stub: every bike belongs to one investor."""

    def ownership_at(self, bike_id, day, bikes):
        return dict(owner='Demo', owner_id=200001, owner_share=0.5)


def chapter4(*rows, directory=DIRECTORY):
    # Directories and operations on one bike and one investor.
    operations = [HEADER] + [[46294, op, src, dst, thb, cur, 'Demo 1', '', '', '', '', '']
                             for op, src, dst, thb, cur in rows]
    wallets = Wallets({'Cash ฿': 'THB', 'Card ₽': 'RUB'}, None, None)
    bikes = Bikes(pd.DataFrame([dict(id=1, full_name='Demo 1', old_name='')]), {})
    investors = InvestorDirectory([{'owner_id': 200001, 'Name': 'Demo'}])
    writes = Writes({'Operations': copy.deepcopy(operations)})
    return load_operations(operations, load_catalog(directory), wallets, bikes, OneOwner(), investors, writes).frame


class FuelTests(unittest.TestCase):
    def test_wallet_sets_direction(self):
        ops = chapter4(('Fuel', 'Cash ฿', '', 450, ''), ('Fuel', '', 'Cash ฿', 600, ''),
                       ('Repair', 'Cash ฿', '', 100, ''))
        self.assertEqual(list(ops['sign']), [-1, 1, -1])

    def test_fuel_needs_exactly_one_wallet(self):
        for src, dst in (('', ''), ('Cash ฿', 'Card ₽')):
            with self.assertRaisesRegex(ValueError, 'wallet_from for an expense, wallet_to for an income'):
                chapter4(('Fuel', src, dst, 100, ''))

    def test_other_expense_still_rejects_wallet_to(self):
        with self.assertRaisesRegex(ValueError, 'an expense fills only wallet_from'):
            chapter4(('Repair', '', 'Cash ฿', 100, ''))

    def test_direction_by_wallet_only_for_marked_type(self):
        plain = [row if row[0] != 'Fuel' else ['Fuel', 'Expense'] for row in DIRECTORY]
        with self.assertRaisesRegex(ValueError, 'an expense fills only wallet_from'):
            chapter4(('Fuel', '', 'Cash ฿', 600, ''), directory=plain)

    def test_cross_currency_refund_explains_two_rows(self):
        with self.assertRaisesRegex(ValueError, 'two rows'):
            chapter4(('Deposit refund', 'Card ₽', 'Cash ฿', 5200, 15000))

    def test_bad_date_names_the_row(self):
        with self.assertRaisesRegex(ValueError, 'Operations, row 2'):
            operations = [HEADER, ['not a date', 'Repair', 'Cash ฿', '', 100, '', '', '', '', '', '', '']]
            load_operations(operations, load_catalog(DIRECTORY), Wallets({'Cash ฿': 'THB'}, None, None),
                            Bikes(pd.DataFrame(columns=['id', 'full_name', 'old_name']), {}), OneOwner(),
                            InvestorDirectory([{'owner_id': 1, 'Name': 'A'}]), Writes({'Operations': operations}))


if __name__ == '__main__':
    unittest.main()
