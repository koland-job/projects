"""Command line: read the sheet, recalculate, show the plan, write it.

    python -m fleet_ledger                     # recalculate and write to Google
    python -m fleet_ledger --dry-run           # show the plan, write nothing
    python -m fleet_ledger --snapshot FILE     # calculate from a saved snapshot (implies --dry-run)
    python -m fleet_ledger --save-snapshot FILE   # only read the sheet into FILE

Files live in --project-dir (default: current directory): credentials/ with the service
account key, backups/, outputs/write_pending.json. The last line of a failed run is
"ERROR: …" — a message for the person who pressed the button.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

from .plan import Snapshot, plan

EXIT_ERROR = 1          # nothing was written
EXIT_NEEDS_REVIEW = 2   # a write to Google may or may not have happened


def human_error(error: Exception) -> str:
    name = type(error).__name__
    text = str(error)
    if isinstance(error, (ValueError, RuntimeError)) and name in ("ValueError", "RuntimeError"):
        return text
    if name == "APIError":
        match = re.search(r"\[(\d{3})\]", text)
        code = match.group(1) if match else ""
        if code == "429":
            return "Google is rate-limiting requests for now. Wait 1–2 minutes and run the recalculation again."
        if code in ("401", "403"):
            return "No access to the Google Sheet. The administrator needs to check the service account's access and its key."
        return f"Google API error{(' HTTP ' + code) if code else ''}. Ask the administrator to check the write status and the log before retrying."
    if name in ("Timeout", "ReadTimeout", "ConnectTimeout", "ConnectionError", "TransportError"):
        return "Could not reach Google. Check the write status before running again; if it repeats, tell the administrator."
    if name in ("DateParseError", "OutOfBoundsDatetime"):
        return "Could not read a date in the sheet. Check the dates and the selected report month. Cause: " + text
    return f"Calculation error ({name}): {text}. If the cause is unclear, pass this message to the administrator."


def load_snapshot(path: Path) -> Snapshot:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    # A snapshot without formulas (the demo one) holds plain values: they are their own formulas.
    return Snapshot(raw["values"], raw.get("formulas", raw["values"]), pd.Timestamp(raw["taken_at"]))


def save_snapshot(snapshot: Snapshot, path: Path) -> None:
    Path(path).write_text(json.dumps({"taken_at": snapshot.run_at.isoformat(), "values": snapshot.values,
                                      "formulas": snapshot.formulas}, ensure_ascii=False), encoding="utf-8")


def report(result, snapshot: Snapshot) -> None:
    with pd.option_context("display.max_rows", None, "display.max_columns", None, "display.width", 200):
        for warning in result.warnings:
            print("⚠", warning)
        for title in ["Free cash", "Cash flow", "P&L"]:
            print(f"\n== {title}\n{pd.Series(result.reports[title], name='Amount').round(2).to_string()}")
        print(f"\n== Investors\n{result.reports['Investors'].to_string(index=False)}")
        diff = result.diff(snapshot)
        print(f"\n== Write plan: {len(diff)} cells")
        if len(diff):
            print(diff.to_string(index=False))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="fleet_ledger", description=__doc__.split("\n")[0])
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    parser.add_argument("--snapshot", type=Path, help="calculate from a saved snapshot instead of Google")
    parser.add_argument("--save-snapshot", type=Path, help="only read the sheet and save it to this file")
    args = parser.parse_args(argv)
    project_dir = args.project_dir.resolve()
    try:
        if args.snapshot:
            snapshot, spreadsheet = load_snapshot(args.snapshot), None
        else:
            from .sheets import Spreadsheet, pending_marker
            if pending_marker(project_dir).exists() and not (args.dry_run or args.save_snapshot):
                raise RuntimeError("The previous write is not confirmed. Check the sheet and outputs/write_pending.json "
                                   "before a new run.")
            print("Connecting to Google…", flush=True)
            spreadsheet = Spreadsheet(project_dir / "credentials")
            snapshot = spreadsheet.snapshot()
            if args.save_snapshot:
                save_snapshot(snapshot, args.save_snapshot)
                print(f"Snapshot saved: {args.save_snapshot}")
                return 0
        result = plan(snapshot)
        report(result, snapshot)
        if args.dry_run or spreadsheet is None:
            print("\nDry run: nothing was written to Google.")
            return 0
        backup = spreadsheet.apply(result, snapshot, project_dir)
        print("\nEvery value is already up to date. Nothing to write to Google." if backup is None
              else f"\nDone. Backup: {backup}")
        return 0
    except Exception as error:
        import traceback
        traceback.print_exc()
        from .sheets import pending_marker
        print(f"ERROR: {human_error(error)}", file=sys.stderr, flush=True)
        return EXIT_NEEDS_REVIEW if pending_marker(project_dir).exists() and not args.dry_run else EXIT_ERROR
