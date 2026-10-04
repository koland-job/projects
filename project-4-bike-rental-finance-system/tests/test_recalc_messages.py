"""Error contract between runner, HTTP API and Apps Script; no live I/O."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_finance import main
from test_recalc import runner

from fleet_ledger.cli import human_error

class MessagesTests(unittest.TestCase):
    def test_google_errors_explain_next_step_without_project_ids(self):
        APIError = type('APIError', (Exception,), {})
        for code, message in [('429','1–2 minutes'),('403','service account'),('503','before retrying')]:
            result=human_error(APIError(f"APIError: [{code}]: project_number:123456789"))
            self.assertIn(message,result);self.assertNotIn('123456789',result)
    def test_input_error_is_preserved(self):
        self.assertEqual(human_error(ValueError('Operations, row 3: choose a wallet.')),'Operations, row 3: choose a wallet.')
        self.assertEqual(runner._error_text(runner.RecalcFailed('Operations, row 3: choose a wallet.')),'Operations, row 3: choose a wallet.')
    def test_environment_and_timeout_are_explained(self):
        self.assertIn('administrator',runner._error_text(FileNotFoundError('python')))
        self.assertIn('did not finish',runner._error_text(subprocess.TimeoutExpired('recalc',900)))
        self.assertIn('date',human_error(type('DateParseError',(Exception,),{})('technical detail')))
    def test_last_error_line_of_the_log_is_the_message(self):
        with tempfile.TemporaryDirectory() as folder:
            log=Path(folder)/'run.log'
            log.write_text('Traceback…\nValueError: x\nERROR: Tab "P&L" changed.\n',encoding='utf-8')
            self.assertEqual(runner._last_error(log),'Tab "P&L" changed.')
    def test_both_api_routes_return_readable_failures(self):
        for name,endpoint in [('start',main.api_recalc_start),('status',main.api_recalc_status)]:
            for error,code,text in [(RuntimeError('Journal corrupted'),409,'corrupted'),(OSError('sensitive filesystem info'),503,'permissions'),(Exception('sensitive internals'),500,'Internal')]:
                with self.subTest(name=name,code=code),patch.object(main.recalc,name,side_effect=error):
                    if code>=500:
                        with self.assertLogs(main.__name__):response=endpoint()
                    else:response=endpoint()
                    body=json.loads(response.body)
                    self.assertEqual(response.status_code,code);self.assertIn(text,body['detail'])
                    self.assertNotIn('sensitive',body['detail'])
    def test_deploy_busy_is_a_retryable_503(self):
        with patch.object(main.recalc,'start',side_effect=main.recalc.Busy('The server is busy with maintenance.')):
            response=main.api_recalc_start()
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.headers['Retry-After'],'30')
        self.assertIn('busy',json.loads(response.body)['detail'])
    def test_token_gates_api_and_status(self):
        from fastapi.testclient import TestClient
        client=TestClient(main.app)
        with patch.object(main,'_is_authorized',return_value=False),patch.object(main.auth,'RECALC_TOKEN','test-token'),patch.object(main.recalc,'status',return_value={'running':False,'last':None}) as status:
            self.assertEqual(client.get('/api/recalc').status_code,401)
            status.assert_not_called()
            response=client.get('/api/recalc',headers={'X-Recalc-Token':'test-token'})
            self.assertEqual(response.status_code,200)
            status.assert_called_once()
            self.assertEqual(client.get('/api/dashboard',headers={'X-Recalc-Token':'test-token'}).status_code,401)
    def test_pending_flag_and_deployment_are_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(runner,'PROJECT_DIR',Path(folder)):
            saved={'last':{'status':'ok'},'last_success_at':'previous'}
            busy=runner._snapshot(saved,True)
            self.assertTrue(busy['busy']);self.assertFalse(busy['running'])
            (Path(folder)/'outputs').mkdir();(Path(folder)/'outputs/write_pending.json').write_text('{}')
            pending=runner._snapshot(saved,False)
            self.assertTrue(pending['needs_review']);self.assertIn('not confirmed',pending['detail'])

if __name__=='__main__':unittest.main()
