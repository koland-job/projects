"""Recalculation runner with shared locking and durable execution status.

Each run is a child process `python -m fleet_ledger` with a timeout; its output is the
run's log in RUNS_DIR, and its last line "ERROR: …" is the message shown to people.

A host-wide flock serializes callers sharing RUNS_DIR. Durable state makes
interrupted runs visible; they require reconciliation before another launch.
This is still an in-process worker: production should host it in a dedicated
service, not assume a web-server restart will preserve execution.
"""
import fcntl
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import telegram
from .sheets_client import clear_cache

PROJECT_DIR = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_DIR / "outputs" / "runs"
STATE_FILE = RUNS_DIR / "last_run.json"
KEEP_RUNS = 30
RUN_TIMEOUT_SECONDS = 900
ERROR_PREFIX = "ERROR: "  # written by fleet_ledger.cli


def command() -> list[str]:
    return [sys.executable, "-m", "fleet_ledger", "--project-dir", str(PROJECT_DIR)]


class RecalcFailed(Exception):
    """The calculation stopped; the message is the one it printed for people."""


class Busy(RuntimeError):
    """A deploy holds the lock: nothing started, a retry in a minute will work."""
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_saved() -> dict:
    try:
        saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"last": None, "last_success_at": None}
    except (json.JSONDecodeError, UnicodeError) as error:
        raise RuntimeError("The recalculation journal is corrupted. Check the last run before retrying.") from error
    if (not isinstance(saved, dict) or "last" not in saved or
            (saved["last"] is not None and not isinstance(saved["last"], dict))):
        raise RuntimeError("The recalculation journal is invalid. Check the last run before retrying.")
    return saved


def _save(saved: dict) -> None:
    # Readers see either the entire old state or the entire new one.
    temporary = STATE_FILE.with_name(f".{STATE_FILE.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(saved, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(STATE_FILE)
    finally:
        temporary.unlink(missing_ok=True)


def _acquire():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    lock = (RUNS_DIR / "recalc.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def _snapshot(saved: dict, running: bool) -> dict:
    last = saved.get("last") or {}
    # The deploy script also takes this lock. A lock without a running journal
    # entry must not make callers wait for a run that never started.
    busy = running and last.get("status") != "running"
    interrupted = not running and last.get("status") == "running"
    pending = not running and (PROJECT_DIR / "outputs" / "write_pending.json").exists()
    needs_review = interrupted or last.get("status") == "needs_review" or pending
    detail = None
    if interrupted:
        detail = "The previous recalculation was interrupted. Check the sheet and the journal before retrying."
        saved = {**saved, "last": {**last, "status": "needs_review", "error": detail}}
    elif pending:
        detail = "The result of the write to Google is not confirmed. Ask an administrator to reconcile the sheet and the journal before retrying."
    elif busy:
        detail = "The server is busy with maintenance. No new recalculation was started. Try again in a minute."
    return {**saved, "running": running and not busy, "busy": busy,
            "started_at": last.get("started_at") if running and not busy else None,
            "needs_review": needs_review, "detail": detail}


def status() -> dict:
    lock = _acquire()
    try:
        return _snapshot(_read_saved(), running=lock is None)
    finally:
        if lock is not None:
            lock.close()


def _error_text(error: Exception) -> str:
    if isinstance(error, (RecalcFailed, RuntimeError, ValueError)):
        return str(error)
    if isinstance(error, subprocess.TimeoutExpired):
        return (f"The recalculation did not finish within {RUN_TIMEOUT_SECONDS // 60} minutes and was stopped. "
                "Ask an administrator to check the journal and the write result.")
    return (f"Could not start the calculation: environment or file access problem ({type(error).__name__}). "
            "Tell an administrator.")


def _last_error(log: Path) -> str | None:
    lines = [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
             if line.startswith(ERROR_PREFIX)]
    return lines[-1][len(ERROR_PREFIX):] if lines else None


def _execute(log: Path) -> None:
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(command(), cwd=PROJECT_DIR, stdout=stream, stderr=subprocess.STDOUT,
                                   timeout=RUN_TIMEOUT_SECONDS, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    if completed.returncode != 0:
        raise RecalcFailed(_last_error(log) or
                           f"The recalculation exited with code {completed.returncode}. See the journal {log.name} for details.")


def _prune_old_runs() -> None:
    runs = sorted(RUNS_DIR.glob("run_*.*"))
    for old in runs[:-KEEP_RUNS]:
        old.unlink(missing_ok=True)


def _run(result: dict, lock) -> None:
    pending = PROJECT_DIR / "outputs" / "write_pending.json"
    try:
        try:
            _execute(RUNS_DIR / result["output"])
            if pending.exists():
                raise RuntimeError("The write is not confirmed: check the sheet before running again.")
            result.update(status="ok", error=None)
        except Exception as error:
            result.update(status="needs_review" if pending.exists() else "error", error=_error_text(error))
        result["finished_at"] = _now_iso()
        saved = _read_saved()
        saved["last"] = result
        if result["status"] == "ok":
            saved["last_success_at"] = result["finished_at"]
        _save(saved)
        if result["status"] != "ok":
            telegram.send("recalculation failed", f"{result['error']}\njournal: outputs/runs/{result['output']}")
        # Housekeeping must never replace the calculation result or retain the lock.
        for cleanup in (clear_cache, _prune_old_runs):
            try:
                cleanup()
            except Exception:
                logger.exception("Maintenance error after the recalculation")
    except Exception:
        # Durable 'running' remains: status() will report needs_review once unlocked.
        logger.exception("Could not save the recalculation result; the last run needs to be checked")
        telegram.send("recalculation: result not saved", "The recalculation journal was not written. Check the sheet and the server before retrying.")
    finally:
        lock.close()


def start() -> dict:
    """Start one run, or return the shared status of the run already in progress."""
    lock = _acquire()
    if lock is None:
        state = status()
        if state.get("busy"):
            raise Busy(state["detail"])
        return state
    transferred = False
    try:
        saved = _read_saved()
        if _snapshot(saved, False)["needs_review"] or (PROJECT_DIR / "outputs" / "write_pending.json").exists():
            raise RuntimeError("The previous run needs the sheet and the journal checked before retrying.")
        result = {"started_at": _now_iso(), "status": "running", "error": None,
                  "output": f"run_{datetime.now(timezone.utc):%Y%m%d_%H%M%S_%f}_{uuid4().hex}.log"}
        _save({**saved, "last": result})
        try:
            worker = threading.Thread(target=_run, args=(result.copy(), lock),
                                      name="finance-recalc", daemon=False)
            worker.start()
            transferred = True
        except Exception as error:
            _save({**saved, "last": {**result, "status": "error", "finished_at": _now_iso(),
                                     "error": _error_text(error)}})
            raise
    finally:
        if not transferred:
            lock.close()
    return status()
