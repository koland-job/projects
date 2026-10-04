"""Runner and safe writes to Google; never connect to Google."""
import copy
import importlib.util
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import support  # noqa: F401  (puts src/ on the path)

from fleet_ledger.names import SHEET_NAMES, resolve_sheets, sheet_id_overrides
from fleet_ledger.plan import Plan, Snapshot
from fleet_ledger.sheets import Spreadsheet

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('_recalc_test_app')
package.__path__ = []
sys.modules[package.__name__] = package
sheets = types.ModuleType('_recalc_test_app.sheets_client')
sheets.clear_cache = Mock()
sys.modules[sheets.__name__] = sheets
telegram = types.ModuleType('_recalc_test_app.telegram')
telegram.send = Mock()
sys.modules[telegram.__name__] = telegram
spec = importlib.util.spec_from_file_location('_recalc_test_app.recalc', ROOT / 'dashboard/backend/app/recalc.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for key, value in {'PROJECT_DIR': root, 'RUNS_DIR': root / 'outputs/runs',
                           'STATE_FILE': root / 'outputs/runs/last_run.json'}.items():
            self.enterContext(patch.object(runner, key, value))
        sheets.clear_cache.reset_mock(side_effect=True)
        telegram.send.reset_mock(side_effect=True)

    def seed(self):
        lock = runner._acquire()
        result = {'status': 'running', 'started_at': '2026-09-28', 'output': 'run_test.log'}
        runner._save({'last': result, 'last_success_at': 'previous'})
        return result, lock

    def finish(self, error=None):
        result, lock = self.seed()
        with patch.object(runner, '_execute', side_effect=error):
            runner._run(result, lock)
        return runner.status()

    def test_success_saved_and_lock_released(self):
        state = self.finish()
        self.assertFalse(state['running'])
        self.assertEqual(state['last']['status'], 'ok')
        self.assertNotEqual(state['last_success_at'], 'previous')

    def test_calculation_error_preserves_last_success(self):
        state = self.finish(runner.RecalcFailed('bad input'))
        self.assertEqual(state['last']['status'], 'error')
        self.assertEqual(state['last']['error'], 'bad input')
        self.assertEqual(state['last_success_at'], 'previous')

    def test_success_sends_no_alert(self):
        self.finish()
        telegram.send.assert_not_called()

    def test_calculation_error_sends_alert_with_reason(self):
        self.finish(runner.RecalcFailed('bad input'))
        telegram.send.assert_called_once()
        self.assertIn('bad input', telegram.send.call_args.args[1])

    def test_unsaved_result_sends_alert(self):
        result, lock = self.seed()
        with patch.object(runner, '_execute'), patch.object(runner, '_save', side_effect=OSError('disk full')), \
                self.assertLogs(runner.logger):
            runner._run(result, lock)
        self.assertIn('not saved', telegram.send.call_args.args[0])

    def test_housekeeping_error_does_not_break_result_or_lock(self):
        sheets.clear_cache.side_effect = OSError('cache')
        with self.assertLogs(runner.logger, level='ERROR'):
            state = self.finish()
        self.assertEqual(state['last']['status'], 'ok')
        self.assertFalse(state['running'])

    def test_state_save_failure_releases_lock_and_requires_review(self):
        result, lock = self.seed()
        with patch.object(runner, '_execute'), patch.object(runner, '_save', side_effect=OSError('disk full')), \
                self.assertLogs(runner.logger):
            runner._run(result, lock)
        self.assertTrue(runner.status()['needs_review'])
        with self.assertRaisesRegex(RuntimeError, 'checked'):
            runner.start()

    def test_lock_is_visible_to_another_process(self):
        result, lock = self.seed()
        self.addCleanup(lock.close)
        program = 'import fcntl,sys; f=open(sys.argv[1],"a+"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
        child = subprocess.run([sys.executable, '-c', program, str(runner.RUNS_DIR / 'recalc.lock')], capture_output=True)
        self.assertNotEqual(child.returncode, 0)
        with patch.object(runner.threading, 'Thread') as worker:
            self.assertTrue(runner.start()['running'])
            worker.assert_not_called()

    def test_lock_held_by_a_deploy_is_busy_not_running(self):
        runner.RUNS_DIR.mkdir(parents=True)
        runner._save({'last': {'status': 'ok', 'output': 'run.log'}, 'last_success_at': 'previous'})
        program = 'import fcntl,sys,time; f=open(sys.argv[1],"a+"); fcntl.flock(f,fcntl.LOCK_EX); print(1,flush=True); time.sleep(10)'
        deploy = subprocess.Popen([sys.executable, '-c', program, str(runner.RUNS_DIR / 'recalc.lock')], stdout=subprocess.PIPE)
        self.addCleanup(deploy.kill)
        deploy.stdout.readline()
        with self.assertRaises(runner.Busy):
            runner.start()

    def test_interrupted_run_blocks_repeat(self):
        result, lock = self.seed()
        lock.close()
        self.assertTrue(runner.status()['needs_review'])
        with self.assertRaises(RuntimeError):
            runner.start()

    def test_pending_write_blocks_repeat(self):
        runner.RUNS_DIR.mkdir(parents=True)
        (runner.PROJECT_DIR / 'outputs/write_pending.json').write_text('{}')
        with self.assertRaises(RuntimeError):
            runner.start()

    def test_failed_thread_start_is_retryable(self):
        with patch.object(runner.threading, 'Thread') as worker:
            worker.return_value.start.side_effect = RuntimeError('thread failed')
            with self.assertRaises(RuntimeError):
                runner.start()
        self.assertFalse(runner.status()['running'])
        self.assertEqual(runner.status()['last']['status'], 'error')

    def test_corrupt_journal_blocks_launch(self):
        runner.RUNS_DIR.mkdir(parents=True)
        for value in ['broken', '[]', '{"last":42}']:
            runner.STATE_FILE.write_text(value)
            with self.assertRaises(RuntimeError):
                runner.start()
        lock = runner._acquire()
        self.assertIsNotNone(lock)
        lock.close()

    def test_real_child_process_stops_at_error_and_can_retry(self):
        import time
        for fail in (True, False):
            program = textwrap.dedent(f"""
                import sys
                from pathlib import Path
                print('working', flush=True)
                if {fail}:
                    print('ERROR: bad input', file=sys.stderr)
                    sys.exit(1)
                Path('finished.txt').write_text('ok')
            """)
            with patch.object(runner, 'command', return_value=[sys.executable, '-c', program]):
                runner.start()
                deadline = time.monotonic() + 30
                while runner.status()['running'] and time.monotonic() < deadline:
                    time.sleep(0.05)
            state = runner.status()
            self.assertFalse(state['running'])
            self.assertEqual(state['last']['status'], 'error' if fail else 'ok', state)
            self.assertEqual((runner.PROJECT_DIR / 'finished.txt').exists(), not fail)
            if fail:
                self.assertEqual(state['last']['error'], 'bad input')
            self.assertIn('working', (runner.RUNS_DIR / state['last']['output']).read_text())

    def test_timeout_is_explained(self):
        state = self.finish(subprocess.TimeoutExpired('recalc', 900))
        self.assertIn('did not finish', state['last']['error'])

    def test_uncertain_write_never_reports_success(self):
        def execute(log):
            (runner.PROJECT_DIR / 'outputs/write_pending.json').write_text('{}')
        result, lock = self.seed()
        with patch.object(runner, '_execute', side_effect=execute):
            runner._run(result, lock)
        self.assertTrue(runner.status()['needs_review'])
        self.assertEqual(runner.status()['last_success_at'], 'previous')

    def test_command_runs_the_package_in_the_project_folder(self):
        self.assertEqual(runner.command()[1:], ['-m', 'fleet_ledger', '--project-dir', str(runner.PROJECT_DIR)])


class SheetLookupTests(unittest.TestCase):
    """Tabs are found by title; numeric ids are an optional override."""

    def tabs(self, renamed=None):
        return [{'sheetId': 500 + i, 'title': (renamed or {}).get(name, name), 'gridProperties': {}}
                for i, name in enumerate(SHEET_NAMES)]

    def test_tabs_are_found_by_title(self):
        found = resolve_sheets(self.tabs(), overrides={})
        self.assertEqual(list(found), SHEET_NAMES)
        self.assertEqual(found['P&L']['sheetId'], 500 + SHEET_NAMES.index('P&L'))

    def test_surrounding_spaces_in_a_title_are_ignored(self):
        found = resolve_sheets(self.tabs({'P&L': ' P&L '}), overrides={})
        self.assertEqual(found['P&L']['title'], ' P&L ')

    def test_missing_tab_is_reported_by_name(self):
        tabs = [tab for tab in self.tabs() if tab['title'] != 'P&L']
        with self.assertRaisesRegex(ValueError, 'P&L'):
            resolve_sheets(tabs, overrides={})

    def test_id_override_survives_a_rename(self):
        tabs = self.tabs({'P&L': 'Profit and loss'})
        pnl_id = 500 + SHEET_NAMES.index('P&L')
        with self.assertRaises(ValueError):
            resolve_sheets(tabs, overrides={})
        found = resolve_sheets(tabs, overrides={'P&L': pnl_id})
        self.assertEqual(found['P&L']['title'], 'Profit and loss')

    def test_override_to_a_missing_id_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'P&L'):
            resolve_sheets(self.tabs(), overrides={'P&L': 99999})

    def test_overrides_are_read_from_json(self):
        self.assertEqual(sheet_id_overrides(''), {})
        self.assertEqual(sheet_id_overrides('{"P&L": 7}'), {'P&L': 7})
        with patch.dict('os.environ', {'SHEET_IDS': '{"P&L": 9}'}):
            self.assertEqual(sheet_id_overrides(), {'P&L': 9})
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(sheet_id_overrides(), {})

    def test_bad_overrides_are_rejected(self):
        for raw in ['not json', '[1]', '{"No such tab": 1}', '{"P&L": "7"}', '{"P&L": true}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sheet_id_overrides(raw)


class WriteTests(unittest.TestCase):
    """Spreadsheet.apply with the Google API replaced by mocks."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.now = pd.Timestamp.now(tz='Etc/GMT-7').tz_localize(None).floor('s')
        self.sheet = {name: [['old']] for name in SHEET_NAMES}
        self.reads = []          # what Google returns on each formula read; default — unchanged
        self.api = Mock()
        self.spreadsheet = Spreadsheet.__new__(Spreadsheet)
        self.spreadsheet.spreadsheet = self.api
        self.spreadsheet.time_zone = ZoneInfo('Etc/GMT-7')
        self.spreadsheet.sheets = {name: {'sheetId': 1000 + i, 'gridProperties': {'rowCount': 10, 'columnCount': 10}}
                                   for i, name in enumerate(SHEET_NAMES)}
        self.spreadsheet.read_all = lambda render: self.reads.pop(0) if self.reads else copy.deepcopy(self.sheet)
        self.plan = Plan(changes={('P&L', 1, 1): {'userEnteredValue': {'numberValue': 2}}}, history_appends=[],
                         history_columns={}, history_allowed_writes={}, operation_header=1,
                         operation_columns={name: i for i, name in enumerate(
                             ['owner', 'owner_id', 'owner_name_snapshot', 'total', 'bike_id'], start=2)})
        self.snapshot = Snapshot(copy.deepcopy(self.sheet), copy.deepcopy(self.sheet), self.now)

    @property
    def marker(self):
        return self.project / 'outputs/write_pending.json'

    def apply(self):
        with patch('fleet_ledger.sheets.with_retries', side_effect=lambda fn, **kwargs: fn()):
            return self.spreadsheet.apply(self.plan, self.snapshot, self.project)

    def test_success_writes_once_backs_up_and_removes_marker(self):
        backup = self.apply()
        self.api.batch_update.assert_called_once()
        self.assertTrue(backup.exists())
        self.assertFalse(self.marker.exists())

    def test_nothing_to_write_touches_nothing(self):
        self.plan.changes = {}
        self.assertIsNone(self.apply())
        self.api.batch_update.assert_not_called()

    def test_changed_source_before_write_blocks_api(self):
        self.reads = [{**self.sheet, 'P&L': [['new']]}]
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.apply()
        self.api.batch_update.assert_not_called()

    def test_change_after_initial_check_also_blocks_api(self):
        self.reads = [copy.deepcopy(self.sheet), {**self.sheet, 'P&L': [['new']]}]
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.apply()
        self.api.batch_update.assert_not_called()
        self.assertFalse(self.marker.exists())

    def test_timeout_keeps_marker_and_blocks_repeat(self):
        from requests.exceptions import Timeout
        self.api.batch_update.side_effect = Timeout('no answer')
        with self.assertRaisesRegex(RuntimeError, 'No answer from Google'):
            self.apply()
        self.assertTrue(self.marker.exists())
        with self.assertRaisesRegex(RuntimeError, 'not confirmed'):
            self.apply()
        self.api.batch_update.assert_called_once()

    def test_non_finite_plan_never_reaches_google(self):
        self.plan.changes[('P&L', 1, 1)]['userEnteredValue']['numberValue'] = float('inf')
        with self.assertRaises(ValueError):
            self.apply()
        self.api.batch_update.assert_not_called()

    def test_midnight_blocks_write(self):
        self.snapshot.run_at -= pd.Timedelta(days=1)
        with self.assertRaisesRegex(ValueError, 'new day'):
            self.apply()
        self.api.batch_update.assert_not_called()

    def test_operations_inputs_and_formatting_are_never_written(self):
        for key, fields in [(('Operations', 3, 1), {'userEnteredValue': {'stringValue': 'x'}}),
                            (('Operations', 1, 5), {'userEnteredValue': {'numberValue': 1}}),
                            (('Operations', 3, 5), {'userEnteredValue': {'numberValue': 1},
                                                  'userEnteredFormat': {'numberFormat': {}}})]:
            self.plan.changes = {key: fields}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.apply()
        self.api.batch_update.assert_not_called()

    def test_history_rows_are_appended_with_date_time(self):
        self.plan.history_columns = {'bike_id': 1, 'owner': 2, 'owner_id': 3, 'owner_share': 4, 'active_from': 5}
        self.plan.history_appends = [dict(bike_id=7, owner='Alex', owner_id=1, owner_share=.5,
                                          active_from=pd.Timestamp('2026-09-30 18:00'))]
        self.apply()
        requests = self.api.batch_update.call_args.args[0]['requests']
        append = next(r['appendCells'] for r in requests if 'appendCells' in r)
        self.assertEqual(append['sheetId'], self.spreadsheet.sheets['Bike history']['sheetId'])
        self.assertAlmostEqual(append['rows'][0]['values'][4]['userEnteredValue']['numberValue'], 46295.75)

    def test_read_detects_edit_between_values_and_formulas(self):
        reads = [{name: [['before']] for name in SHEET_NAMES}, {name: [['calculated']] for name in SHEET_NAMES},
                 {name: [['after']] for name in SHEET_NAMES}]
        self.spreadsheet.read_all = lambda render: reads.pop(0)
        with self.assertRaisesRegex(ValueError, 'while it was being read'):
            self.spreadsheet.snapshot()


if __name__ == '__main__':
    unittest.main()
