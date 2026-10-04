import logging
import threading
import time
from datetime import datetime, timezone

import gspread
from gspread.utils import ValueRenderOption, fill_gaps

from fleet_ledger.names import resolve_sheets

from . import config, demo_data

logger = logging.getLogger(__name__)

_lock = threading.Lock()  # guards the dicts below; never held during a Google call
_key_locks: dict[str, threading.Lock] = {}
_refreshing: set[str] = set()
_generation = 0  # bumped by clear_cache() so an older background fetch cannot overwrite fresher data
_client = None
_spreadsheet = None

# key -> (fetched_at_monotonic, fetched_at_wall_clock_iso, value)
_cache: dict[str, tuple[float, str, object]] = {}

# Stale data is served while a background refresh runs, but only up to this
# age: past it (Google failing for a long time) requests load synchronously
# again, so an error shows instead of quietly ageing numbers.
MAX_STALE_SECONDS = 30 * 60

# Tab name -> current title. Titles change rarely, so this outlives the data
# cache; a rename makes the batch read fail once, which drops it and retries.
_titles: dict[str, str] = {}


def _get_client():
    global _client
    if _client is None:
        keys = list(config.CREDENTIALS_DIR.glob("*.json"))
        if len(keys) != 1:
            raise RuntimeError(
                f"Expected exactly one JSON key in {config.CREDENTIALS_DIR}, found {len(keys)}"
            )
        _client = gspread.service_account(filename=str(keys[0]))
    return _client


def _get_spreadsheet():
    global _spreadsheet
    if _spreadsheet is None:
        _spreadsheet = _get_client().open_by_url(config.SPREADSHEET_URL)
    return _spreadsheet


def _store(key: str, value, generation: int) -> None:
    with _lock:
        if generation == _generation:
            _cache[key] = (time.monotonic(), datetime.now(timezone.utc).isoformat(), value)


def _refresh_in_background(key: str, loader, generation: int) -> None:
    def run():
        try:
            _store(key, loader(), generation)
        except Exception:
            # Keep serving the previous snapshot; the next request tries again.
            logger.exception("Background refresh of %s failed", key)
        finally:
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=run, name=f"refresh-{key}", daemon=True).start()


def _cached(key: str, loader):
    """Stale-while-revalidate cache.

    Fresh (younger than CACHE_TTL_SECONDS): returned as is.
    Stale: returned immediately, and one background refresh is started.
    Missing or older than MAX_STALE_SECONDS: loaded synchronously; concurrent
    requests for the same key wait for that single load instead of each
    calling Google."""
    with _lock:
        entry = _cache.get(key)
        age = time.monotonic() - entry[0] if entry else None
        if entry is not None and age < config.CACHE_TTL_SECONDS:
            return entry[2]
        if entry is not None and age < MAX_STALE_SECONDS:
            if key not in _refreshing:
                _refreshing.add(key)
                _refresh_in_background(key, loader, _generation)
            return entry[2]
        key_lock = _key_locks.setdefault(key, threading.Lock())

    with key_lock:
        with _lock:  # someone else may have loaded it while we waited
            entry = _cache.get(key)
            if entry is not None and time.monotonic() - entry[0] < config.CACHE_TTL_SECONDS:
                return entry[2]
            generation = _generation
        value = loader()
        _store(key, value, generation)
        return value


def _sheet_titles() -> dict[str, str]:
    if not _titles:
        meta = _get_spreadsheet().fetch_sheet_metadata()
        found = resolve_sheets([s["properties"] for s in meta["sheets"]], config.SHEET_NAMES, config.SHEET_ID_OVERRIDES)
        _titles.update({name: sheet["title"] for name, sheet in found.items()})
    return _titles


def _batch_read() -> dict[str, list[list]]:
    titles = _sheet_titles()
    names = list(config.SHEET_NAMES)
    ranges = ["'{}'".format(titles[n].replace("'", "''")) for n in names]
    resp = _get_spreadsheet().values_batch_get(
        ranges, params={"valueRenderOption": ValueRenderOption.unformatted}
    )
    # Same shape as Worksheet.get_all_values(): a rectangle padded with "".
    return {name: fill_gaps(vr.get("values", [])) for name, vr in zip(names, resp["valueRanges"])}


def _load_snapshot() -> dict[str, list[list]]:
    """Every sheet the dashboard reads, in one Sheets API call."""
    if config.DEMO:
        return demo_data.sheets(config.SHEET_NAMES)
    try:
        return _batch_read()
    except (gspread.exceptions.APIError, ValueError):
        _titles.clear()  # a sheet may have been renamed - look the titles up again
        return _batch_read()


def get_sheet_rows(sheet_name: str) -> list[list]:
    return _cached("sheets", _load_snapshot)[sheet_name]


def get_last_modified_iso() -> str:
    if config.DEMO:
        return demo_data.modified_iso()

    def load():
        sp = _get_spreadsheet()
        resp = _get_client().http_client.session.get(
            f"https://www.googleapis.com/drive/v3/files/{sp.id}",
            params={"fields": "modifiedTime"},
        )
        resp.raise_for_status()
        return resp.json()["modifiedTime"]

    return _cached("last_modified", load)


def get_cache_loaded_at() -> str | None:
    """Wall-clock time the sheet snapshot currently in cache was fetched."""
    with _lock:
        entry = _cache.get("sheets")
        return entry[1] if entry else None


def clear_cache():
    global _generation
    with _lock:
        _cache.clear()
        _generation += 1
