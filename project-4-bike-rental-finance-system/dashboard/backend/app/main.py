import calendar
import dataclasses
import logging
import threading
from urllib.parse import quote
from datetime import date, timedelta

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, ranges, recalc
from .parsing import (
    FREE_CASH_TOTAL,
    as_date,
    bike_status_counts,
    data_boundaries,
    load_bikes,
    load_free_money,
    load_investor_dues,
    load_operations,
)
from .sheets_client import clear_cache, get_cache_loaded_at, get_last_modified_iso

app = FastAPI(title="Fleet Ledger Dashboard API")


@app.on_event("startup")
def warm_cache():
    # Load the sheets in the background right after start, so the first
    # visitor after a restart does not wait for Google. A request that comes
    # in mid-load simply waits for this same load.
    def run():
        try:
            load_operations()
            get_last_modified_iso()
        except Exception:
            logging.getLogger(__name__).exception("Could not warm up the cache at startup")

    threading.Thread(target=run, name="warm-cache", daemon=True).start()


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    # The frontend is edited constantly during design work; without this the
    # browser keeps serving stale charts.js/styles.css after a change.
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Reachable without a session: the login page itself and what it loads.
PUBLIC_PATHS = {"/login", "/login.html", "/login.js", "/styles.css", "/api/login", "/api/health"}
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
# The Google Sheets button has no session; it signs in with X-Recalc-Token instead.
RECALC_PATH = "/api/recalc"


def _client_ip(request: Request) -> str:
    # Behind Caddy uvicorn already swaps in the real client ip from
    # X-Forwarded-For (it trusts the proxy on 127.0.0.1).
    return request.client.host if request.client else ""


def _is_authorized(request: Request) -> bool:
    if config.DEMO:
        return True  # synthetic data, open to everyone
    if auth.ENABLED:
        return auth.session_valid(request.cookies.get(auth.COOKIE_NAME))
    # No account configured: only direct local access (./start.sh on the Mac).
    return _client_ip(request) in LOCAL_HOSTS and "x-forwarded-for" not in request.headers


def _safe_next(target: str | None) -> str:
    if not target or not target.startswith("/") or target.startswith("//") or target.startswith("/login"):
        return "/"
    return target


@app.middleware("http")
async def require_login(request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or _is_authorized(request):
        return await call_next(request)
    if path == RECALC_PATH and auth.recalc_token_valid(request.headers.get("x-recalc-token")):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Sign-in required"}, status_code=401)
    target = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


@app.get("/login", include_in_schema=False)
def login_page(request: Request):
    if config.DEMO or (auth.ENABLED and _is_authorized(request)):
        return RedirectResponse(_safe_next(request.query_params.get("next")), status_code=303)
    return FileResponse(FRONTEND_DIR / "login.html")


@app.post("/api/login")
async def api_login(request: Request):
    ip = _client_ip(request)
    if config.DEMO:
        return JSONResponse({"detail": "The demo has no login."}, status_code=404)
    if not auth.ENABLED:
        return JSONResponse({"detail": "Sign-in is not configured on the server"}, status_code=503)
    locked = auth.seconds_locked(ip)
    if locked:
        return JSONResponse(
            {"detail": "Too many attempts", "retry_after": locked}, status_code=429
        )
    try:
        body = await request.json()
        user, password = str(body.get("user", "")).strip(), str(body.get("password", ""))
    except (ValueError, AttributeError):
        return JSONResponse({"detail": "Invalid request"}, status_code=400)

    if not auth.check_credentials(user, password):
        left = auth.register_failure(ip)
        if left == 0:
            return JSONResponse(
                {"detail": "Too many attempts", "retry_after": auth.seconds_locked(ip)},
                status_code=429,
            )
        return JSONResponse(
            {"detail": "Wrong username or password", "attempts_left": left}, status_code=401
        )

    auth.clear_failures(ip)
    response = JSONResponse({"ok": True, "next": _safe_next(body.get("next"))})
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.make_session(),
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
    )
    return response


@app.post("/api/logout")
def api_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME)
    return response


@app.get("/api/me")
def api_me():
    enabled = auth.ENABLED and not config.DEMO
    return {"auth": enabled, "user": auth.USER if enabled else None, "demo": config.DEMO}


def _resolve_range(key: str, date_from: str | None = None, date_to: str | None = None) -> tuple[ranges.Range, date | None, date | None]:
    today = ranges.today()
    try:
        earliest, latest = data_boundaries(today)
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error))
    try:
        if key == "custom":
            if not date_from or not date_to:
                raise ValueError("A custom period needs both from and to dates")
            r = ranges.resolve_custom(
                date.fromisoformat(date_from), date.fromisoformat(date_to), as_of=latest, earliest=earliest
            )
        else:
            r = ranges.resolve(key, as_of=latest, earliest=earliest)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return r, earliest, latest


def _sum_revenue(ops: pd.DataFrame, start: date, end: date, operation_names: list[str]) -> float:
    if end < start:
        return 0.0
    mask = (
        ops["operation"].isin(operation_names)
        & (ops["date"] >= pd.Timestamp(start))
        & (ops["date"] <= pd.Timestamp(end))
    )
    return float(ops.loc[mask, "total"].sum())


def _pct_delta(current: float, previous: float):
    if previous == 0:
        return None
    return (current - previous) / abs(previous) * 100


def _bucket_series(revenue_ops: pd.DataFrame, start: date, end: date, bucket: str, as_of: date) -> list[dict]:
    # A partly entered week/month ends at the data cutoff, not the calendar end.
    end = min(end, as_of)
    if end < start:
        return []
    idx = pd.date_range(start, end, freq="D")
    daily = (
        revenue_ops.loc[
            (revenue_ops["date"] >= pd.Timestamp(start)) & (revenue_ops["date"] <= pd.Timestamp(end))
        ]
        .groupby("date")["total"]
        .sum()
    )
    daily = daily.reindex(idx, fill_value=0.0)

    if bucket == "day":
        out = []
        for i, (d, v) in enumerate(daily.items()):
            day = d.date()
            out.append(
                {
                    "offset": i,
                    "start": day.isoformat(),
                    "end": day.isoformat(),
                    "revenue": float(v),
                    "after_data": day > as_of,
                }
            )
        return out

    freq = "W-SUN" if bucket == "week" else "MS"
    grouped = daily.groupby(pd.Grouper(freq=freq)).sum()
    out = []
    for i, (label_ts, v) in enumerate(grouped.items()):
        if bucket == "week":
            b_end = min(label_ts.date(), end)
            b_start = max(label_ts.date() - timedelta(days=6), start)
            expected_days = 7
        else:
            month_start = label_ts.date()
            month_end = ranges.add_months(month_start, 1) - timedelta(days=1)
            b_start = max(month_start, start)
            b_end = min(month_end, end)
            expected_days = calendar.monthrange(month_start.year, month_start.month)[1]
        if b_end < b_start:
            continue
        actual_days = (b_end - b_start).days + 1
        out.append(
            {
                "offset": i,
                "start": b_start.isoformat(),
                "end": b_end.isoformat(),
                "revenue": float(v),
                "after_data": b_start > as_of,
                "partial": actual_days < expected_days,
                "actual_days": actual_days,
                "expected_days": expected_days,
            }
        )
    return out


def _clean(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _safe_date(value):
    if value is None or str(value).strip() == "":
        return pd.NaT
    try:
        return as_date(value)
    except (ValueError, TypeError):
        return pd.NaT


def _iso(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date().isoformat()


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/ranges")
def api_ranges():
    today = ranges.today()
    try:
        earliest, latest = data_boundaries(today)
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error))
    return ranges.all_ranges(as_of=latest, earliest=earliest)


@app.post("/api/refresh")
def api_refresh():
    clear_cache()
    return {"ok": True}


DEMO_RECALC_OFF = "Recalculation is disabled in the demo: the data is a fixed synthetic snapshot."


def _recalc_response(action):
    if config.DEMO:
        return JSONResponse({"detail": DEMO_RECALC_OFF}, status_code=403)
    try:
        return action()
    except recalc.Busy as error:
        # Temporary: the nightly timer retries on 503, people see the detail.
        return JSONResponse({"detail": str(error)}, status_code=503, headers={"Retry-After": "30"})
    except (ValueError, RuntimeError) as error:
        return JSONResponse({"detail": str(error)}, status_code=409)
    except OSError:
        logging.getLogger(__name__).exception("File access error in the recalculation journal")
        return JSONResponse({"detail": "The server cannot read or save the recalculation journal. "
                             "An administrator needs to check free disk space and file permissions. "
                             "The result of the run is not confirmed; do not run it again until this is checked."}, status_code=503)
    except Exception:
        logging.getLogger(__name__).exception("Could not get the result of the recalculation command")
        return JSONResponse({"detail": "Internal error in the recalculation server. The result of the run is not confirmed. "
                             "Check the status later; if it keeps happening, tell an administrator."}, status_code=500)


@app.post("/api/recalc")
def api_recalc_start():
    return _recalc_response(recalc.start)


@app.get("/api/recalc")
def api_recalc_status():
    return _recalc_response(recalc.status)


@app.get("/api/dashboard")
def api_dashboard(
    range: str = Query(...),
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    compare: str = Query("prev"),
):
    payment_as_of = ranges.today()
    r, earliest, latest = _resolve_range(range, date_from, date_to)
    if compare == "yoy":
        # Same dates one year back instead of the period right before.
        prev_start = ranges.add_months(r.start, -12)
        prev_end = ranges.add_months(r.effective_end if r.has_current_data else r.end, -12)
        r = dataclasses.replace(
            r,
            prev_start=prev_start,
            prev_end=prev_end,
            has_previous_data=earliest is not None and prev_start >= earliest,
        )
    elif compare != "prev":
        raise HTTPException(status_code=400, detail="compare: prev | yoy")

    try:
        ops = load_operations()
        free_money_rows = load_free_money()
        fleet = bike_status_counts()
        bikes = load_bikes()
        investor_dues = load_investor_dues()
    except ValueError as error:
        raise HTTPException(status_code=502, detail=str(error))

    # --- "right now" block: not period-dependent ---
    bikes_by_status = {row["status"]: row["count"] for row in fleet["by_status"]}
    rented = bikes_by_status.get(config.RENTED_BIKE_STATUS, 0)

    free_money_breakdown = [{"label": label, "value": value} for label, value in free_money_rows.items()]

    # Owed to investors today: only positive balances add up - an investor
    # paid ahead does not reduce what the others are owed.
    dues_sorted = sorted(investor_dues, key=lambda x: x["to_be_paid"], reverse=True)
    owed_rows = [d for d in dues_sorted if d["to_be_paid"] > 0]
    overpaid_rows = [d for d in dues_sorted if d["to_be_paid"] < 0]

    # --- period revenue ---
    current_revenue = (
        _sum_revenue(ops, r.start, r.effective_end, config.REVENUE_OPERATIONS) if r.has_current_data else 0.0
    )
    previous_revenue = (
        _sum_revenue(ops, r.prev_start, r.prev_end, config.REVENUE_OPERATIONS) if r.has_previous_data else 0.0
    )
    has_both_periods = r.has_current_data and r.has_previous_data

    # --- revenue by bike ---
    # Bike revenue excludes additional services, which have no bike link.
    in_period = (
        (ops["date"] >= pd.Timestamp(r.start)) & (ops["date"] <= pd.Timestamp(r.effective_end))
        if r.has_current_data
        else pd.Series(False, index=ops.index)
    )
    period_ops = ops.loc[in_period & ops["operation"].isin(config.REVENUE_OPERATIONS)]
    by_bike_op = period_ops.groupby(["bike_id", "operation"])["total"].sum()
    by_bike = period_ops.groupby("bike_id")["total"].sum()

    all_time_rent_mask = (
        ops["operation"].isin(config.RENT_OPERATIONS)
        & (ops["date"] <= pd.Timestamp(payment_as_of))
    )

    # Payment trail per bike: when it was last paid for and up to which date
    # the paid rental runs (end_date). Only rented bikes get a state, and it
    # is not tied to the selected period:
    #   no_ops  - never paid for rent (usually a wrong status or rent not entered)
    #   no_term - the latest rent payment has no end_date, so nobody can tell
    #             how long it covers (a data error, flagged separately)
    #   overdue - the paid rental ended before today in the business timezone
    #   ok      - paid up to or past today
    rent_all = ops.loc[all_time_rent_mask].copy()
    rent_all["paid_until"] = rent_all["end_date"].map(_safe_date)
    last_paid_at = rent_all.groupby("bike_id")["date"].max()
    paid_until = rent_all.groupby("bike_id")["paid_until"].max()
    latest_rent = rent_all.loc[rent_all["date"] == rent_all.groupby("bike_id")["date"].transform("max")]
    latest_has_term = latest_rent.groupby("bike_id")["paid_until"].apply(lambda s: s.notna().any())
    # Sheet rows to point at when something is wrong: the payment whose term
    # ran out (overdue) and the latest payment that has no end_date (no_term).
    term_row = rent_all.dropna(subset=["paid_until"]).sort_values(["paid_until", "sheet_row"]).groupby("bike_id")["sheet_row"].last()
    no_term_row = latest_rent.loc[latest_rent["paid_until"].isna()].groupby("bike_id")["sheet_row"].max()
    ops_per_bike = ops.groupby("bike_id").size()

    def payment_state(bike_id, status):
        if status != config.RENTED_BIKE_STATUS:
            return None, None
        if bike_id not in last_paid_at.index:
            return "no_ops", None
        if not latest_has_term.get(bike_id, False):
            return "no_term", None
        until = paid_until.get(bike_id).date()
        if until < payment_as_of:
            return "overdue", (payment_as_of - until).days
        return "ok", None

    def problem_row(bike, state):
        """Payment row in "Operations" to fix. A bike that was never paid for has
        no such row - the suspect there is its status, found by name."""
        if state == "overdue":
            return int(term_row[bike["id"]])
        if state == "no_term":
            return int(no_term_row[bike["id"]])
        return None

    names = dict(zip(bikes["id"], bikes["full_name"]))
    no_term_rows = rent_all.loc[rent_all["paid_until"].isna()].sort_values("date", ascending=False)
    rent_without_end_date = [
        {
            "row": int(op.sheet_row),
            "bike_id": None if pd.isna(op.bike_id) else int(op.bike_id),
            "bike": names.get(op.bike_id, ""),
            "date": _iso(op.date),
            "total": float(op.total),
        }
        for op in no_term_rows.itertuples()
    ]

    bike_rows = []
    for _, bike in bikes.iterrows():
        revenue = float(by_bike.get(bike["id"], 0.0))
        rent = float(sum(by_bike_op.get((bike["id"], op), 0.0) for op in config.RENT_OPERATIONS))
        roi = bike.get("roi")
        state, overdue_days = payment_state(bike["id"], bike["status"])
        plate = _clean(bike.get("licence_plate"))
        if plate in {"-", "–", "—"}:
            plate = ""
        bike_rows.append(
            {
                "bike_id": int(bike["id"]),
                "full_name": bike["full_name"],
                "status": bike["status"],
                "revenue": revenue,
                "rent": rent,
                "delivery": revenue - rent,
                "payment_state": state,
                "overdue_days": overdue_days,
                "problem_row": problem_row(bike, state),
                "roi": None if roi is None or pd.isna(roi) else float(roi),
                "brand": _clean(bike.get("brand")),
                "model": _clean(bike.get("model")),
                "color": _clean(bike.get("color")),
                "capacity": _clean(bike.get("capacity")).strip("-").strip(),
                "plate": plate,
                "vehicle_type": _clean(bike.get("vehicle_type")),
                "owner": _clean(bike.get("owner")),
                "ops_count": int(ops_per_bike.get(bike["id"], 0)),
                "last_paid_at": _iso(last_paid_at.get(bike["id"])),
                "paid_until": _iso(paid_until.get(bike["id"])),
            }
        )
    bike_rows.sort(key=lambda x: x["revenue"], reverse=True)

    # --- daily/weekly/monthly series ---
    revenue_ops = ops.loc[ops["operation"].isin(config.REVENUE_OPERATIONS)]
    current_series = _bucket_series(revenue_ops, r.start, r.end, r.bucket, latest or r.end)
    previous_series = (
        _bucket_series(revenue_ops, r.prev_start, r.prev_end, r.bucket, latest or r.end)
        if r.has_previous_data
        else []
    )

    return {
        "demo": config.DEMO,
        "range": {
            "key": r.key,
            "label": r.label,
            "start": r.start.isoformat(),
            "end": r.end.isoformat(),
            "effective_end": r.effective_end.isoformat(),
            "has_current_data": r.has_current_data,
            "prev_start": r.prev_start.isoformat(),
            "prev_end": r.prev_end.isoformat(),
            "has_previous_data": r.has_previous_data,
            "compare": compare,
            "bucket": r.bucket,
            "start_trimmed": r.start_trimmed,
        },
        "data_as_of": latest.isoformat() if latest else None,
        "payment_as_of": payment_as_of.isoformat(),
        "timezone": config.TIMEZONE,
        "earliest_data": earliest.isoformat() if earliest else None,
        "sheet_modified_at": get_last_modified_iso(),
        "cache_loaded_at": get_cache_loaded_at(),
        "now": {
            "free_money": free_money_rows.get(FREE_CASH_TOTAL),
            "free_money_breakdown": free_money_breakdown,
            "fleet": {"total": fleet["total"], "by_status": fleet["by_status"], "rented": rented},
            "investor_dues": {
                "owed": sum(d["to_be_paid"] for d in owed_rows),
                "owed_count": len(owed_rows),
                "overpaid": -sum(d["to_be_paid"] for d in overpaid_rows),
                "rows": owed_rows + overpaid_rows,
            },
        },
        "period": {
            "revenue": {
                "value": current_revenue,
                "previous_value": previous_revenue if r.has_previous_data else None,
                "delta_pct": _pct_delta(current_revenue, previous_revenue) if has_both_periods else None,
                "has_current_data": r.has_current_data,
                "has_previous_data": r.has_previous_data,
            },
        },
        "data_issues": {
            "rent_without_end_date": rent_without_end_date,
        },
        "bikes_revenue": {
            "note": "rent + delivery to the client",
            "bikes": bike_rows,
        },
        "daily": {
            "bucket": r.bucket,
            "current": current_series,
            "previous": previous_series,
        },
    }


FRONTEND_DIR = (config.BACKEND_DIR.parent / "frontend").resolve()
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
