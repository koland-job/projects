"""Google Sheets I/O: read a consistent snapshot, apply a plan safely. All network access lives here."""
import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
import pandas as pd
from google.auth.exceptions import TransportError
from gspread.exceptions import APIError
from gspread.utils import ValueRenderOption, fill_gaps
from requests.exceptions import ConnectionError as RequestConnectionError, Timeout

from .names import SHEET_NAMES, resolve_sheets
from .parse import serial
from .plan import Plan, Snapshot

# Columns of "Operations" the recalculation may write; everything else there is the owner's input.
OPERATION_OUTPUTS = ["owner", "owner_id", "owner_name_snapshot", "total", "bike_id"]


def with_retries(func, attempts=4, base_delay=2, retry_statuses=(429, 500, 503)):
    # Only clearly temporary Google API errors are retried (rate limit, unavailable).
    # No access or a bad request will not pass on a second try either.
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except APIError as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            if attempt == attempts or status not in retry_statuses:
                raise
            # Google's limit is per minute: short pauses will not wait it out.
            delay = 30 if status == 429 else base_delay * (2 ** (attempt - 1))
            print(f"Google API: error {status}, attempt {attempt}/{attempts}, waiting {delay} s…", flush=True)
            time.sleep(delay)


class Spreadsheet:
    def __init__(self, credentials_dir: Path, url: str = ""):
        keys = list(Path(credentials_dir).glob("*.json"))
        if len(keys) != 1:
            raise ValueError(f"{credentials_dir} must hold exactly one JSON key (found: {len(keys)}).")
        url = url or os.environ.get("SPREADSHEET_URL", "")
        if not url:
            raise ValueError("The sheet link is not set: environment variable SPREADSHEET_URL.")
        client = gspread.service_account(filename=str(keys[0]))
        self.spreadsheet = with_retries(lambda: client.open_by_url(url))
        metadata = with_retries(self.spreadsheet.fetch_sheet_metadata)
        self.time_zone = ZoneInfo(metadata["properties"]["timeZone"])
        self.sheets = resolve_sheets([sheet["properties"] for sheet in metadata["sheets"]])

    def now(self) -> pd.Timestamp:
        return pd.Timestamp(datetime.now(self.time_zone)).tz_localize(None).floor("s")

    def read_all(self, render: ValueRenderOption) -> dict:
        # Every sheet in one request: one sheet at a time made ~80 reads per run and hit
        # Google's limit (60 per minute per key). Rows are padded like get_all_values.
        ranges = ["'" + self.sheets[name]["title"].replace("'", "''") + "'" for name in SHEET_NAMES]
        response = with_retries(lambda: self.spreadsheet.values_batch_get(ranges, params={"valueRenderOption": render.value}))
        return {name: fill_gaps(part.get("values", [[]])) for name, part in zip(SHEET_NAMES, response["valueRanges"])}

    def snapshot(self) -> Snapshot:
        run_at = self.now()
        before = self.read_all(ValueRenderOption.formula)
        values = self.read_all(ValueRenderOption.unformatted)
        formulas = self.read_all(ValueRenderOption.formula)
        for name in SHEET_NAMES:
            if before[name] != formulas[name]:
                raise ValueError(f'Tab "{name}" changed while it was being read. Run the recalculation again.')
        return Snapshot(values, formulas, run_at)

    def apply(self, plan: Plan, snapshot: Snapshot, project_dir: Path) -> Path | None:
        """Writes the plan in one atomic batchUpdate. Returns the backup file, None if nothing to write."""
        if not plan.changes and not plan.history_appends:
            return None
        if self.now().normalize() != snapshot.today:
            raise ValueError("A new day has started. Run the recalculation again so history gets the right date.")
        requests = self._requests(plan)
        # Never overwrite a sheet that changed during the calculation.
        self._check_unchanged(snapshot)

        # Backup of entered values and formulas; no access keys in it.
        backup = Path(project_dir) / "backups" / f"before_recalc_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
        backup.parent.mkdir(exist_ok=True)
        backup.write_text(json.dumps(snapshot.formulas, ensure_ascii=False, indent=2), encoding="utf-8")

        pending = pending_marker(project_dir)
        pending.parent.mkdir(parents=True, exist_ok=True)
        try:
            with pending.open("x", encoding="utf-8") as marker:
                json.dump({"started_at": snapshot.run_at.isoformat(), "backup": str(backup)}, marker)
                marker.flush()
                os.fsync(marker.fileno())
        except FileExistsError as error:
            raise RuntimeError("Another run is writing, or the previous write is not confirmed. Check the log.") from error
        # Checked again under the marker: no other local run can write between this check and
        # our request. Manual edits in Google cannot be fully locked out.
        try:
            self._check_unchanged(snapshot)
            if self.now().normalize() != snapshot.today:
                raise ValueError("A new day has started. Run the recalculation again.")
        except Exception:
            pending.unlink()  # nothing was sent yet — a new run is safe
            raise

        # Only 429 is retried: then Google certainly applied nothing. After 500/503 it is
        # unknown whether the request was applied — check the sheet by hand first.
        try:
            with_retries(lambda: self.spreadsheet.batch_update({"requests": requests}), retry_statuses=(429,))
        except APIError as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            if status in (500, 503):
                raise RuntimeError("Google returned an error while writing. Check the sheet by hand before running again.") from error
            raise
        except (Timeout, TransportError, RequestConnectionError) as error:
            raise RuntimeError("No answer from Google while writing. Check the sheet by hand before running again.") from error
        pending.unlink()
        return backup

    def _check_unchanged(self, snapshot: Snapshot) -> None:
        current = self.read_all(ValueRenderOption.formula)
        for name in SHEET_NAMES:
            if current[name] != snapshot.formulas[name]:
                raise ValueError(f'Tab "{name}" changed during the calculation. Run the recalculation again.')

    def _requests(self, plan: Plan) -> list:
        requests = []
        columns = plan.operation_columns
        allowed = [columns[name] for name in OPERATION_OUTPUTS]
        for (name, row, column), fields in plan.changes.items():
            if name == "Operations":
                if row <= plan.operation_header or column not in allowed:
                    raise ValueError(f'Only {", ".join(OPERATION_OUTPUTS)} may change in "Operations".')
                if set(fields) != {"userEnteredValue"}:
                    raise ValueError('Formatting and dropdowns of "Operations" must not change.')

        for name in SHEET_NAMES:
            targets = [(row, column) for sheet, row, column in plan.changes if sheet == name]
            if not targets:
                continue
            grid = self.sheets[name]["gridProperties"]
            for dimension, needed, available in [("ROWS", max(r for r, _ in targets), grid["rowCount"]),
                                                 ("COLUMNS", max(c for _, c in targets), grid["columnCount"])]:
                if needed > available:
                    requests.append({"appendDimension": {"sheetId": self.sheets[name]["sheetId"], "dimension": dimension,
                                                         "length": needed - available}})

        for (name, row, column), fields in plan.changes.items():
            mask = ",".join("userEnteredFormat.numberFormat" if key == "userEnteredFormat" else key for key in fields)
            requests.append({"updateCells": {
                "range": {"sheetId": self.sheets[name]["sheetId"], "startRowIndex": row - 1, "endRowIndex": row,
                          "startColumnIndex": column - 1, "endColumnIndex": column},
                "rows": [{"values": [fields]}], "fields": mask,
            }})

        # History is only appended to the end of its sheet; existing rows are never rewritten.
        if plan.history_appends:
            history_columns = plan.history_columns
            new_rows = []
            for entry in plan.history_appends:
                cells = [{} for _ in range(max(history_columns.values()))]
                cells[history_columns["bike_id"] - 1] = {"userEnteredValue": {"numberValue": entry["bike_id"]}}
                cells[history_columns["owner"] - 1] = {"userEnteredValue": {"stringValue": entry["owner"]}}
                if entry["owner_id"] is not None:
                    cells[history_columns["owner_id"] - 1] = {"userEnteredValue": {"numberValue": int(entry["owner_id"])}}
                for share in ["owner_share", "repair_share"]:
                    if entry.get(share) is not None:
                        cells[history_columns[share] - 1] = {
                            "userEnteredValue": {"numberValue": float(entry[share])},
                            "userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0%"}},
                        }
                cells[history_columns["active_from"] - 1] = {
                    "userEnteredValue": {"numberValue": serial(entry["active_from"])},
                    "userEnteredFormat": {"numberFormat": {"type": "DATE_TIME", "pattern": "dd.mm.yyyy hh:mm:ss"}},
                }
                new_rows.append({"values": cells})
            requests.append({"appendCells": {"sheetId": self.sheets["Bike history"]["sheetId"], "rows": new_rows,
                                             "fields": "userEnteredValue,userEnteredFormat.numberFormat"}})
        # A separate number check also guards fields not added through Writes.put.
        json.dumps(requests, allow_nan=False)
        return requests


def pending_marker(project_dir: Path) -> Path:
    """Exists while a write to Google is not confirmed; a new run must not start then."""
    return Path(project_dir) / "outputs" / "write_pending.json"
