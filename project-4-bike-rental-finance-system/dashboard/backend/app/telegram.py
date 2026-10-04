"""Failure alerts to the shared Telegram bot.

One bot serves several projects; PROJECT names this one in every message.
Token and chat come from /etc/dashboard/auth.env. Without them alerts are off.
send() never raises: a Telegram outage must not change a recalc result.
"""
import logging
import os
import socket
import urllib.parse
import urllib.request

PROJECT = "fleet-ledger"
MAX_TEXT = 3500  # Telegram rejects messages over 4096 characters
logger = logging.getLogger(__name__)


def send(title: str, details: str = "") -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat):
        return False
    text = f"🔴 {PROJECT} · {title}\n{details}".strip()[:MAX_TEXT] + f"\nserver: {socket.gethostname()}"
    # No parse_mode: error text with < or _ would otherwise break the message.
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10):
            return True
    except Exception as error:
        # The URL contains the token; log only the error type.
        logger.error("Could not send the Telegram notification: %s", type(error).__name__)
        return False
