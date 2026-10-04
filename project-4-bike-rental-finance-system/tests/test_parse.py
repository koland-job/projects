"""Reading cells and planning writes."""
import unittest

import pandas as pd
import support  # noqa: F401  (puts src/ on the path)

from fleet_ledger.parse import as_date, as_datetime, number, table, text
from fleet_ledger.writes import Writes


class ParseTests(unittest.TestCase):
    def test_bad_dates_rejected(self):
        for bad in ['', True, 'NaT', '2026-09-01T00:00:00+07:00']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                as_date(bad)

    def test_iso_date_is_not_read_day_first(self):
        for value in ['2026-10-04', ' 2026-10-04 ', '04.10.2026', 46299]:
            with self.subTest(value=value):
                self.assertEqual(as_date(value), pd.Timestamp('2026-10-04'))
        self.assertEqual(as_datetime('2026-10-04 10:00'), pd.Timestamp('2026-10-04 10:00'))

    def test_numbers_and_text(self):
        self.assertEqual(number('1 234,5'), 1234.5)
        self.assertIsNone(number(''))
        self.assertEqual(text(3.0), '3')
        with self.assertRaises(ValueError):
            number('1,234.56')

    def test_table_needs_one_header_and_unique_columns(self):
        rows = [['', ''], ['a', 'b'], [1, 2], ['', '']]
        found = table(rows, ['a'], 'Sheet')
        self.assertEqual((found.header, found.columns), (2, {'a': 1, 'b': 2}))
        self.assertEqual(found.frame['sheet_row'].tolist(), [3])
        with self.assertRaisesRegex(ValueError, 'repeated'):
            table([['a', 'a']], ['a'], 'Sheet')
        with self.assertRaisesRegex(ValueError, 'exactly one header row'):
            table([['a'], ['a']], ['a'], 'Sheet')


class WritesTests(unittest.TestCase):
    def test_float_noise_does_not_plan_a_write(self):
        writes = Writes({'P&L': [[0.30000000000000004, 'x']]})
        writes.put('P&L', 1, 1, 0.1 + 0.2 - 1e-12)
        writes.put('P&L', 1, 2, 'x')
        self.assertEqual(writes.changes, {})
        writes.put('P&L', 1, 1, 0.31)
        self.assertIn(('P&L', 1, 1), writes.changes)

    def test_writing_the_old_value_again_cancels_the_change(self):
        writes = Writes({'P&L': [[1]]})
        writes.put('P&L', 1, 1, 2)
        writes.put('P&L', 1, 1, 1)
        self.assertEqual(writes.changes, {})

    def test_infinite_values_and_bad_addresses_are_refused(self):
        writes = Writes({'P&L': [[1]]})
        for row, column, value in [(1, 1, float('inf')), (0, 1, 1)]:
            with self.assertRaises(ValueError):
                writes.put('P&L', row, column, value)


if __name__ == '__main__':
    unittest.main()
