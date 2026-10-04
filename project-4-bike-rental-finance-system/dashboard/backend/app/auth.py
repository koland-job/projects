"""Login for the dashboard owner: one account, no database.

The account lives in environment variables (on the server they come from the
systemd unit, not from .env):
  DASHBOARD_USER           - login
  DASHBOARD_PASSWORD_HASH  - output of `python -m app.auth` (pbkdf2_sha256)
  DASHBOARD_SECRET         - random string used to sign the session cookie
  RECALC_TOKEN             - shared secret for the Google Sheets button that
                             starts the recalculation (header X-Recalc-Token)

When the account is not configured (local development) only requests coming
straight from this machine are let through, so a server that lost its config
fails closed instead of opening the dashboard to everyone.
"""

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time

COOKIE_NAME = "fleet_ledger_session"
SESSION_SECONDS = 180 * 24 * 3600
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
PBKDF2_ITERATIONS = 600_000

USER = os.environ.get("DASHBOARD_USER", "")
PASSWORD_HASH = os.environ.get("DASHBOARD_PASSWORD_HASH", "")
SECRET = os.environ.get("DASHBOARD_SECRET", "")
ENABLED = bool(USER and PASSWORD_HASH and SECRET)
RECALC_TOKEN = os.environ.get("RECALC_TOKEN", "")

_failures_lock = threading.Lock()
# client ip -> (failed attempts, time of first failure in the current window)
_failures: dict[str, tuple[int, float]] = {}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def _verify_password(password: str) -> bool:
    try:
        _, iterations, salt_b64, digest_b64 = PASSWORD_HASH.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iterations)
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def check_credentials(user: str, password: str) -> bool:
    # Always run the slow hash so a wrong login takes as long as a wrong password.
    password_ok = _verify_password(password)
    return hmac.compare_digest(user.encode(), USER.encode()) and password_ok


# --- session cookie: "<user>.<expires>.<signature>" ---
# The password hash is part of the signing key, so changing the password
# logs out every existing session.

def _sign(payload: str) -> str:
    key = (SECRET + PASSWORD_HASH).encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def make_session() -> str:
    payload = f"{USER}.{int(time.time()) + SESSION_SECONDS}"
    return f"{payload}.{_sign(payload)}"


def session_valid(token: str | None) -> bool:
    if not token:
        return False
    payload, _, signature = token.rpartition(".")
    if not payload or not hmac.compare_digest(signature, _sign(payload)):
        return False
    user, _, expires = payload.rpartition(".")
    return user == USER and expires.isdigit() and int(expires) > time.time()


def recalc_token_valid(token: str | None) -> bool:
    # No token configured means the button is off, not open to everyone.
    return bool(RECALC_TOKEN and token) and hmac.compare_digest(token.encode(), RECALC_TOKEN.encode())


# --- brute-force lockout, per client ip ---

def seconds_locked(ip: str) -> int:
    with _failures_lock:
        count, since = _failures.get(ip, (0, 0.0))
        left = since + LOCKOUT_SECONDS - time.time()
        if left <= 0:
            _failures.pop(ip, None)
            return 0
        return int(left) + 1 if count >= MAX_FAILURES else 0


def register_failure(ip: str) -> int:
    """Records a failed attempt; returns how many attempts are left."""
    with _failures_lock:
        count, since = _failures.get(ip, (0, 0.0))
        if time.time() - since > LOCKOUT_SECONDS:
            count, since = 0, time.time()
        count += 1
        _failures[ip] = (count, since)
        return max(0, MAX_FAILURES - count)


def clear_failures(ip: str) -> None:
    with _failures_lock:
        _failures.pop(ip, None)


if __name__ == "__main__":
    import getpass

    print(hash_password(getpass.getpass("Password: ")))
