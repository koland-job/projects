"""DATA_SOURCE=demo: the sheets come from a saved synthetic snapshot instead of Google.

The snapshot is the workbook after a recalculation (python -m fleet_ledger.demo), so the
dashboard reads exactly what it would read from a real sheet. "Today" is the moment the
snapshot was taken: the demo looks the same whenever it is opened.
"""
import json
from datetime import date, timezone
from functools import lru_cache

import pandas as pd

from . import config


@lru_cache(maxsize=1)
def _snapshot() -> dict:
    return json.loads(config.DEMO_SNAPSHOT.read_text(encoding="utf-8"))


def taken_at() -> pd.Timestamp:
    """The run moment, in the business time zone."""
    return pd.Timestamp(_snapshot()["taken_at"]).tz_localize(config.TIMEZONE)


def today() -> date:
    return taken_at().date()


def sheets(names: list[str]) -> dict[str, list[list]]:
    values = _snapshot()["values"]
    missing = [name for name in names if name not in values]
    if missing:
        raise ValueError(f"The demo snapshot has no tabs: {', '.join(missing)}")
    return {name: values[name] for name in names}


def modified_iso() -> str:
    return taken_at().astimezone(timezone.utc).isoformat()
