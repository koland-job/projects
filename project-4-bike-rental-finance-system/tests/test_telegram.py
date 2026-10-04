"""Telegram alert sender; never connects to Telegram."""
import importlib.util
import os
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_telegram_test', ROOT/'dashboard/backend/app/telegram.py')
telegram = importlib.util.module_from_spec(spec)
spec.loader.exec_module(telegram)
ENV = {'TELEGRAM_BOT_TOKEN':'123:abc', 'TELEGRAM_CHAT_ID':'42'}


class TelegramTests(unittest.TestCase):
    def test_disabled_without_env(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'', 'TELEGRAM_CHAT_ID':''}), \
                patch.object(telegram.urllib.request, 'urlopen') as urlopen:
            self.assertFalse(telegram.send('x'))
        urlopen.assert_not_called()

    def test_message_names_project_and_is_plain_text(self):
        with patch.dict(os.environ, ENV), patch.object(telegram.urllib.request, 'urlopen', MagicMock()) as urlopen:
            self.assertTrue(telegram.send('recalculation failed', 'a < b ' + 'x'*5000))
        url, data = urlopen.call_args.args[:2]
        self.assertEqual(url, 'https://api.telegram.org/bot123:abc/sendMessage')
        fields = urllib.parse.parse_qs(data.decode())
        self.assertEqual(fields['chat_id'], ['42'])
        self.assertNotIn('parse_mode', fields)
        text = fields['text'][0]
        self.assertTrue(text.startswith('🔴 fleet-ledger · recalculation failed'))
        self.assertLess(len(text), 4096)

    def test_network_error_is_logged_without_token(self):
        with patch.dict(os.environ, ENV), \
                patch.object(telegram.urllib.request, 'urlopen', side_effect=OSError('https://api.telegram.org/bot123:abc')), \
                self.assertLogs(telegram.logger) as logs:
            self.assertFalse(telegram.send('x'))
        self.assertNotIn('123:abc', '\n'.join(logs.output))


if __name__ == '__main__':
    unittest.main()
