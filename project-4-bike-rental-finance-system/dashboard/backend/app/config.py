import os
from pathlib import Path

from fleet_ledger import names
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

# "sheets" reads the live Google Sheet; "demo" reads the synthetic snapshot demo/snapshot.json
# (python -m fleet_ledger.demo) — no Google, no keys, no login, recalculation off.
DATA_SOURCE = os.environ.get("DATA_SOURCE", "sheets").strip().lower()
if DATA_SOURCE not in ("sheets", "demo"):
    raise RuntimeError(f"DATA_SOURCE must be sheets or demo, not {DATA_SOURCE!r}")
DEMO = DATA_SOURCE == "demo"
DEMO_SNAPSHOT = Path(os.environ.get("DEMO_SNAPSHOT") or BACKEND_DIR.parents[1] / "demo" / "snapshot.json")

SPREADSHEET_URL = "" if DEMO else os.environ["SPREADSHEET_URL"]
CREDENTIALS_DIR = None if DEMO else (BACKEND_DIR / os.environ["CREDENTIALS_DIR"]).resolve()
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
TIMEZONE = os.environ.get("TIMEZONE", "Etc/GMT-7")

# The tabs the dashboard reads; found by title (or by id from SHEET_IDS), as in the recalculation package.
SHEET_NAMES = [
    "Investors", "Bikes", "Operations", "Operation types", "Free cash today", "Investor settlement",
]
SHEET_ID_OVERRIDES = names.sheet_id_overrides()

# "Rent from deposit" is the same rent, only paid by withholding it from the bike's deposit.
RENT_OPERATIONS = names.RENT_OPERATIONS
# "Extra services" have no bike, so they count in the "Revenue" KPI but not in
# "Revenue by bike" — the owner confirmed this difference.
REVENUE_OPERATIONS = RENT_OPERATIONS + ["Delivery (client)", "Extra services"]
FREE_BIKE_STATUS = names.AVAILABLE
RENTED_BIKE_STATUS = names.RENTED
