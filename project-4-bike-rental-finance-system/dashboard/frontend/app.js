const state = {
  ranges: [],
  currentRange: "last_30_days",
  statusFilter: "all",
  bikeSortAsc: false,
  customFrom: null,
  customTo: null,
  animateChart: false,
  shownRevenue: null,
  compare: "prev", // "prev" = previous week/month/quarter, "yoy" = same dates a year ago
  lastData: null,
  abortController: null,
  requestSeq: 0,
  chartResizeObserver: null,
  chartSize: "",
  loading: false,
  initialized: false,
  retryAction: null,
};

const els = {};
[
  "range-row", "range-select", "kpi-row", "fleet-widget", "daily-chart-title",
  "status-filter", "bike-insight", "bike-list-section", "bike-list", "bike-sort", "bike-count",
  "bike-chart-sub", "bike-table-toggle", "bike-table-wrap", "bike-table",
  "daily-chart-sub", "daily-chart-body",
  "compare-toggle",
  "updated-line", "demo-badge", "theme-toggle", "theme-icon",
  "refresh-btn", "logout-btn", "error-banner", "error-text", "error-retry",
].forEach((id) => (els[id.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = document.getElementById(id)));

const ARROW_UP = `<svg viewBox="0 0 12 12" fill="none"><path d="M6 10V2M6 2L2.5 5.5M6 2l3.5 3.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const ARROW_DOWN = `<svg viewBox="0 0 12 12" fill="none"><path d="M6 2v8M6 10l3.5-3.5M6 10 2.5 6.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const SUN_ICON = `<circle cx="12" cy="12" r="4.2" stroke="currentColor" stroke-width="1.6"/><path d="M12 3v2.2M12 18.8V21M21 12h-2.2M5.2 12H3M18.4 5.6l-1.5 1.5M7.1 16.9l-1.5 1.5M18.4 18.4l-1.5-1.5M7.1 7.1 5.6 5.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>`;
const MOON_ICON = `<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.6 6.6 0 0 0 10.5 10.5Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>`;


/* ---------------- motion ---------------- */

const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)");
let revenueTween = null;

/* Rolls a money figure from its previous value to the new one (ease-out). */
function tweenNumber(el, from, to, ms = 1100) {
  if (revenueTween) cancelAnimationFrame(revenueTween);
  state.shownRevenue = to;
  if (from === to || REDUCED_MOTION.matches || document.hidden) {
    el.textContent = fmtThb(to);
    return;
  }
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / ms);
    const eased = 1 - Math.pow(1 - k, 3);
    el.textContent = fmtThb(from + (to - from) * eased);
    revenueTween = k < 1 ? requestAnimationFrame(step) : null;
  };
  revenueTween = requestAnimationFrame(step);
}

/* ---------------- theme ---------------- */

function renderThemeIcon() {
  const explicit = document.documentElement.getAttribute("data-theme");
  const isDark = explicit ? explicit === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  els.themeIcon.innerHTML = isDark ? SUN_ICON : MOON_ICON;
}

els.themeToggle.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme");
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const currentlyDark = current ? current === "dark" : systemDark;
  const next = currentlyDark ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("theme", next); } catch (e) {}
  renderThemeIcon();
});

/* ---------------- URL state ---------------- */

function readRangeFromUrl() {
  return new URLSearchParams(window.location.search).get("range");
}
function writeRangeToUrl(key) {
  const url = new URL(window.location.href);
  url.searchParams.set("range", key);
  if (key === "custom") {
    url.searchParams.set("from", state.customFrom);
    url.searchParams.set("to", state.customTo);
  } else {
    url.searchParams.delete("from");
    url.searchParams.delete("to");
  }
  if (state.compare === "yoy") url.searchParams.set("cmp", "yoy");
  else url.searchParams.delete("cmp");
  window.history.replaceState({}, "", url);
}

/* ---------------- formatting helpers ---------------- */

function deltaBadge(pct) {
  const f = fmtPct(pct);
  if (!f) return null;
  const direction = f.rounded > 0 ? "up" : f.rounded < 0 ? "down" : "flat";
  return { direction, text: f.text };
}

function timeAgo(iso) {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const hrs = Math.round(min / 60);
  if (hrs < 24) return `${hrs} h ago`;
  return `${Math.round(hrs / 24)} d ago`;
}
function fullDateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
}

/* ---------------- stat tiles ---------------- */

function statTile({ label, value, sub, delta, muted, hero }) {
  const tile = document.createElement("div");
  tile.className = "stat-tile" + (muted ? " muted" : "") + (hero ? " hero" : "");
  const deltaHtml = delta
    ? `<div class="stat-delta-row"><span class="stat-delta ${delta.direction}">${delta.direction === "up" ? ARROW_UP : delta.direction === "down" ? ARROW_DOWN : ""}${delta.text}</span>${delta.caption ? `<span class="stat-delta-caption">${delta.caption}</span>` : ""}</div>`
    : "";
  tile.innerHTML = `
    <div class="stat-label"><span>${label}</span></div>
    <div class="stat-value-row"><div class="stat-value">${value}</div></div>
    ${deltaHtml}
    ${sub ? `<div class="stat-sub">${sub}</div>` : ""}
  `;
  return tile;
}

function skeletonTile() {
  const tile = document.createElement("div");
  tile.className = "stat-tile skeleton";
  tile.innerHTML = `<div class="sk-line sk-w60"></div><div class="sk-line sk-w40 sk-tall"></div><div class="sk-line sk-w80"></div>`;
  return tile;
}

function renderSkeletons() {
  els.kpiRow.innerHTML = "";
  for (let i = 0; i < 4; i++) els.kpiRow.appendChild(skeletonTile());
}

/* ---------------- KPI row (all cards on top) ---------------- */

let breakdownPopover = null;
function ensureBreakdownPopover() {
  if (!breakdownPopover) {
    breakdownPopover = document.createElement("div");
    breakdownPopover.className = "free-money-breakdown";
    breakdownPopover.style.left = breakdownPopover.style.top = "0px"; // parked until first shown
    document.body.appendChild(breakdownPopover);
  }
  return breakdownPopover;
}

/* A hidden popover still takes up room where it last stood; near the bottom
   of the window that stretched the page and added a scrollbar. Once its
   fade-out is over, park it in the top-left corner. */
function parkWhenHidden(el) {
  clearTimeout(el._parkTimer);
  el._parkTimer = setTimeout(() => {
    if (!el.classList.contains("visible")) {
      el.style.left = "0px";
      el.style.top = "0px";
    }
  }, 250);
}

function attachBreakdown(valueEl, rows, { underline = true, compact = false } = {}) {
  if (!rows) return;
  if (underline) valueEl.classList.add("has-tip");
  valueEl.tabIndex = 0;
  const pop = ensureBreakdownPopover();
  const showPop = () => {
    pop.innerHTML = typeof rows === "function" ? rows() : rows;
    pop.classList.toggle("compact", !!compact);
    pop.classList.add("visible");
    const r = valueEl.getBoundingClientRect();
    const left = Math.min(r.left, document.documentElement.clientWidth - pop.offsetWidth - 8);
    pop.style.left = Math.max(8, left) + window.scrollX + "px";
    const below = r.bottom + 6 + pop.offsetHeight <= window.innerHeight - 8;
    pop.style.top = (below ? r.bottom + 6 : Math.max(8, r.top - 6 - pop.offsetHeight)) + window.scrollY + "px";
  };
  const hidePop = () => {
    pop.classList.remove("visible");
    parkWhenHidden(pop);
  };
  valueEl.addEventListener("mouseenter", showPop);
  valueEl.addEventListener("focus", showPop);
  valueEl.addEventListener("mouseleave", hidePop);
  valueEl.addEventListener("blur", hidePop);
}

function fmRow(label, value, cls = "") {
  return `<div class="fm-row${cls ? " " + cls : ""}"><span>${escapeHtml(label)}</span><span>${value}</span></div>`;
}

/* The sheet lists wallets, then "TOTAL NOW", then what is held back
   (deposits, investor payouts, bills) and finally "TOTAL FREE". Rows
   after the first total are subtracted, so they carry a minus sign. */
function attachFreeMoneyBreakdown(valueEl, data) {
  let deducting = false;
  const rows = (data.now.free_money_breakdown || [])
    .map((row) => {
      const label = row.label.replace(/\s*\n\s*/g, " ");
      const isTotal = row.label.startsWith("TOTAL");
      const value = deducting && !isTotal && row.value > 0 ? MINUS + fmtThb(row.value) : fmtThb(row.value);
      if (isTotal) deducting = true;
      return fmRow(label, value, isTotal ? "total" : "");
    })
    .join("");
  attachBreakdown(valueEl, rows);
}

function attachInvestorBreakdown(valueEl, dues) {
  const owed = dues.rows.filter((r) => r.to_be_paid > 0);
  const over = dues.rows.filter((r) => r.to_be_paid < 0);
  let html = `<div class="fm-head">To be paid</div>` + owed.map((r) => fmRow(r.owner, fmtThb(r.to_be_paid))).join("");
  html += fmRow("Total to be paid", fmtThb(dues.owed), "total");
  if (over.length) {
    html += `<div class="fm-head">Paid in advance</div>` + over.map((r) => fmRow(r.owner, fmtThb(r.to_be_paid), "muted")).join("");
  }
  attachBreakdown(valueEl, html);
}

/* Fleet utilisation: a slim bar in the top-right of the control bar. Each
   segment shows its own tooltip on hover (status, count, share). */
let fleetTip = null;
function renderFleet(data) {
  const fleet = data.now.fleet;
  els.fleetWidget.innerHTML = `
    <span class="fleet-label">Fleet utilization</span>
    <div class="fleet-bar">${fleet.by_status
      .map((s) => `<div class="fleet-seg" tabindex="0" data-status="${escapeHtml(s.status)}" data-count="${s.count}" style="width:${fleet.total ? (s.count / fleet.total) * 100 : 0}%;background:${statusColor(s.status)}"></div>`)
      .join("")}</div>`;
  if (!fleetTip) {
    fleetTip = document.createElement("div");
    fleetTip.className = "viz-tooltip fleet-tip";
    fleetTip.style.left = fleetTip.style.top = "0px"; // parked until first shown
    document.body.appendChild(fleetTip);
  }
  els.fleetWidget.querySelectorAll(".fleet-seg").forEach((seg) => {
    const show = () => {
      const count = +seg.dataset.count;
      const share = fleet.total ? Math.round((count / fleet.total) * 100) : 0;
      fleetTip.innerHTML = `<div class="tt-row"><span class="tt-dot" style="background:${seg.style.background}"></span><strong>${escapeHtml(seg.dataset.status)}</strong></div>
        <div>${count} ${pluralBikes(count)} · ${share}% of fleet</div>`;
      const r = seg.getBoundingClientRect();
      // Coming back from its parking spot it must not glide across the page.
      const parked = fleetTip.style.left === "0px";
      if (parked) fleetTip.style.transition = "none";
      fleetTip.style.left = r.left + r.width / 2 + window.scrollX + "px";
      fleetTip.style.top = r.bottom + window.scrollY + 10 + "px";
      if (parked) {
        void fleetTip.offsetWidth;
        fleetTip.style.transition = "";
      }
      fleetTip.classList.add("visible");
      seg.classList.add("hover");
    };
    const hide = () => {
      fleetTip.classList.remove("visible");
      seg.classList.remove("hover");
      parkWhenHidden(fleetTip);
    };
    seg.addEventListener("pointerenter", show);
    seg.addEventListener("focus", show);
    seg.addEventListener("pointerleave", hide);
    seg.addEventListener("blur", hide);
  });
}

/* KPI panel - exactly the four figures from the brief: free money (now),
   free bikes / total (now), revenue (period, with delta), investor payouts
   to date (now). */
/* "Sep 1–22" within one month, "Aug 24 – Sep 22" across months. */
/* The year is written out whenever it is not obvious: when the range spans
   two years ("Sep 24, 2024 – Sep 23, 2026") or lies in another year than
   the latest data ("Jan 1 – Sep 23, 2025"). */
function fmtRange(a, b) {
  const currentYear = (state.lastData?.data_as_of || b).slice(0, 4);
  const y = (iso) => `, ${iso.slice(0, 4)}`;
  if (a.slice(0, 4) !== b.slice(0, 4)) return `${fmtDateShort(a)}${y(a)} – ${fmtDateShort(b)}${y(b)}`;
  const tail = b.slice(0, 4) !== currentYear ? y(b) : "";
  if (a === b) return fmtDateShort(a) + tail;
  if (a.slice(0, 7) === b.slice(0, 7)) return `${fmtDateShort(a)}–${+b.slice(8)}${tail}`;
  return `${fmtDateShort(a)} – ${fmtDateShort(b)}${tail}`;
}

/* Why there is nothing to compare with, in words that fit the period. */
function noPrevText(range) {
  if (range.compare === "yoy") return "no data for the same period last year";
  if (range.key === "all_time") return `since records began, ${fmtRange(range.start, range.start)}`;
  return { this_month: "no data for last month", this_quarter: "no data for last quarter", this_year: "no data for last year" }[range.key] || "no data for the previous period";
}

/* "last month" / "last quarter" for calendar periods, dates otherwise. */
function prevPeriodName(range) {
  if (range.compare === "yoy") return `the same period a year ago, ${fmtRange(range.prev_start, range.prev_end)}`;
  if (range.key === "custom") return fmtRange(range.prev_start, range.prev_end);
  const named = { this_month: "last month", this_quarter: "last quarter", this_year: "last year" }[range.key];
  return named ? `${named}, ${fmtRange(range.prev_start, range.prev_end)}` : fmtRange(range.prev_start, range.prev_end);
}

function renderKpis(data) {
  els.kpiRow.innerHTML = "";
  const rev = data.period.revenue;
  const fleet = data.now.fleet;
  const range = data.range;
  const free = (fleet.by_status.find((s) => s.status === "Available") || { count: 0 }).count;
  const freeShare = fleet.total ? Math.round((free / fleet.total) * 100) : 0;

  if (!rev.has_current_data) {
    els.kpiRow.appendChild(statTile({ label: "Revenue", value: fmtThb(0), sub: "no data entered for this period yet", muted: true }));
  } else {
    const delta = rev.has_previous_data ? deltaBadge(rev.delta_pct) : null;
    const revTile = statTile({ label: "Revenue", value: fmtThb(state.shownRevenue ?? rev.value), delta, sub: delta ? "" : rev.has_previous_data && rev.previous_value === 0 ? "revenue in the previous period was 0 ฿" : noPrevText(range) });
    tweenNumber(revTile.querySelector(".stat-value"), state.shownRevenue ?? rev.value, rev.value);
    // What we compare with lives in a tooltip on the badge, not on the card.
    const badge = revTile.querySelector(".stat-delta-row");
    if (badge) {
      attachBreakdown(
        badge,
        `<div class="fm-head">Compared with ${escapeHtml(prevPeriodName(range))}</div>` +
          fmRow("Now", fmtThb(rev.value)) +
          fmRow("Previous period", fmtThb(rev.previous_value)) +
          fmRow("Difference", (rev.value - rev.previous_value >= 0 ? "+" : MINUS) + fmtThb(Math.abs(rev.value - rev.previous_value)), "total"),
        { underline: false }
      );
    }
    els.kpiRow.appendChild(revTile);
  }

  const money = statTile({ label: "Free cash", value: fmtThb(data.now.free_money), sub: "today, across all wallets" });
  attachFreeMoneyBreakdown(money.querySelector(".stat-value"), data);
  els.kpiRow.appendChild(money);

  const dues = data.now.investor_dues;
  const duesTile = statTile({
    label: "Owed to investors",
    value: fmtThb(dues.owed),
    sub: `due to ${dues.owed_count} ${pluralInvestors(dues.owed_count)}`,
  });
  attachInvestorBreakdown(duesTile.querySelector(".stat-value"), dues);
  els.kpiRow.appendChild(duesTile);
  els.kpiRow.appendChild(
    statTile({
      label: "Available bikes",
      value: `${free}<span class="stat-value-total"> / ${fleet.total}</span>`,
      sub: `${freeShare}% of the fleet ready to rent`,
    })
  );
}

/* ---------------- bikes ---------------- */

function renderStatusFilter(data) {
  if (state.statusFilter !== "all" && !data.now.fleet.by_status.some((s) => s.status === state.statusFilter)) state.statusFilter = "all";
  els.statusFilter.innerHTML = "";
  const all = document.createElement("button");
  all.type = "button";
  all.className = "status-chip" + (state.statusFilter === "all" ? " active" : "");
  all.setAttribute("aria-pressed", state.statusFilter === "all" ? "true" : "false");
  all.textContent = `All ${data.now.fleet.total}`;
  all.addEventListener("click", () => {
    state.statusFilter = "all";
    renderStatusFilter(data);
    renderBikeSection(data);
  });
  els.statusFilter.appendChild(all);

  data.now.fleet.by_status.forEach((s) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "status-chip" + (state.statusFilter === s.status ? " active" : "");
    chip.setAttribute("aria-pressed", state.statusFilter === s.status ? "true" : "false");
    chip.innerHTML = `<span class="tt-dot" style="background:${statusColor(s.status)}"></span>${escapeHtml(s.status)} ${s.count}`;
    chip.addEventListener("click", () => {
      state.statusFilter = s.status;
      renderStatusFilter(data);
      renderBikeSection(data);
    });
    els.statusFilter.appendChild(chip);
  });
}

/* Every problem the dashboard reports, per bike, from one place:
   overdue  - a rented bike whose paid rental has run out;
   errors   - bookkeeping errors: the bike is rented but was never paid for,
              plus one error per rent row ("Rent payment", "Rent from deposit") without end_date.
   The header badges, the marks in the list and the bike tooltip all read
   this, so a problem is counted once and shown the same way everywhere. */
function bikeProblems(data) {
  const byBike = new Map();
  const entry = (id) => {
    if (!byBike.has(id)) byBike.set(id, { overdue: false, noOps: false, noTermRows: [] });
    return byBike.get(id);
  };
  data.bikes_revenue.bikes.forEach((b) => {
    if (b.payment_state === "overdue") entry(b.bike_id).overdue = true;
    if (b.payment_state === "no_ops") entry(b.bike_id).noOps = true;
  });
  (data.data_issues?.rent_without_end_date || []).forEach((r) => entry(r.bike_id).noTermRows.push(r));
  const errorCount = (p) => (p ? (p.noOps ? 1 : 0) + p.noTermRows.length : 0);
  return { get: (id) => byBike.get(id), errorCount };
}

function renderBikeSection(data) {
  if (breakdownPopover) {
    breakdownPopover.classList.remove("visible");
    parkWhenHidden(breakdownPopover);
  }
  const allBikes = data.bikes_revenue.bikes;
  const problems = bikeProblems(data);
  allBikes.forEach((b) => {
    const p = problems.get(b.bike_id);
    b._overdue = !!(p && p.overdue);
    b._errors = problems.errorCount(p);
  });
  const filtered = state.statusFilter === "all" ? allBikes : allBikes.filter((b) => b.status === state.statusFilter);
  const earning = filtered.filter((b) => b.revenue > 0);
  const total = filtered.reduce((sum, b) => sum + b.revenue, 0);

  els.bikeChartSub.innerHTML = `${fmtRange(data.range.start, data.range.effective_end)}${!data.range.has_current_data ? " · no data entered yet" : ""} <span class="info-dot" tabindex="0" aria-label="Explanation">i</span>`;
  attachBreakdown(
    els.bikeChartSub.querySelector(".info-dot"),
    `<div class="fm-note fm-note-plain">Rent + delivery to the client for each bike. Extra services count only toward total revenue.</div>`,
    { underline: false }
  );

  // Bikes with revenue first (by amount), then the ones that earned nothing -
  // the whole fleet in one list you can scroll through.
  const sorted = [...filtered].sort((a, b) => (state.bikeSortAsc ? a.revenue - b.revenue : b.revenue - a.revenue));
  const maxVal = Math.max(...allBikes.map((b) => b.revenue), 1);
  const prevWidths = REDUCED_MOTION.matches ? null : new Map([...els.bikeList.querySelectorAll(".bike-row-bar")].map((b) => [b.dataset.id, b.style.width]));
  renderBikeList(els.bikeList, sorted, maxVal, total, prevWidths);
  els.bikeList.querySelectorAll(".bike-row").forEach((row, i) => {
    attachBreakdown(row.querySelector(".bike-row-label"), () => bikeInfoTip(sorted[i], problems.get(sorted[i].bike_id)), { underline: false });
  });
  els.bikeList.scrollTop = 0;
  els.bikeListSection.scrollTop = 0;

  // The badges describe the bikes currently in the list (they follow the
  // status filter). Overdue is money; bookkeeping errors share one badge.
  const inList = new Set(filtered.map((b) => b.bike_id));
  const overdue = filtered.filter((b) => b._overdue).sort((a, b) => b.overdue_days - a.overdue_days);
  const noOps = filtered.filter((b) => problems.get(b.bike_id)?.noOps);
  const noTermRows = (data.data_issues?.rent_without_end_date || []).filter((r) => inList.has(r.bike_id));
  const errorCount = noOps.length + noTermRows.length;
  const badges = [
    overdue.length && `<span class="bike-badge overdue" id="badge-overdue">${overdue.length} overdue</span>`,
    errorCount && `<span class="bike-badge data-error" id="badge-errors">${errorCount} ${pluralErrors(errorCount)}</span>`,
  ].filter(Boolean).join("");
  els.bikeCount.innerHTML = filtered.length
    ? `<span class="bike-count-text">${earning.length} of ${filtered.length} earning · ${fmtThb(total)}</span>${badges}`
    : "";
  const tipOpts = { underline: false, compact: true };
  const bOverdue = document.getElementById("badge-overdue");
  if (bOverdue) attachBreakdown(bOverdue, () => overdueTip(overdue), tipOpts);
  const bErrors = document.getElementById("badge-errors");
  if (bErrors) attachBreakdown(bErrors, () => (noOps.length ? noOpsTip(noOps) : "") + (noTermRows.length ? noTermTip(noTermRows) : ""), tipOpts);
  els.bikeSort.innerHTML = state.bikeSortAsc ? `${ARROW_UP}<span class="sort-label">Lowest first</span>` : `${ARROW_DOWN}<span class="sort-label">Highest first</span>`;
  els.bikeSort.title = state.bikeSortAsc ? "Lowest first" : "Highest first";
  els.bikeSort.onclick = () => {
    state.bikeSortAsc = !state.bikeSortAsc;
    renderBikeSection(data);
  };

  els.bikeInsight.hidden = true;

  renderBikeTable(sorted);
}

/* Payment problems of rented bikes, decided by the backend from the
   operations log (not tied to the selected period):
   overdue - the paid rental ended before today in the business timezone;
   no_ops  - the bike was never paid for;
   no_term - its latest rent payment has no end_date. */
function problemNotes(b, p) {
  if (!p) return [];
  const notes = [];
  if (p.overdue) notes.push(`Overdue by ${b.overdue_days} days — paid through ${fmtDateShort(b.paid_until)}`);
  if (p.noOps) notes.push("Rented with no payments: the rental was not entered or the status is wrong");
  p.noTermRows.forEach((r) => notes.push(`Rent payment without an end date (end_date), row ${r.row} in the "Operations" sheet`));
  return notes;
}

const bikeLabel = (b) => `${bikeTitle(b)} · ${b.plate || b.color.toLowerCase()}`;

const rowLabel = (row, b) => `row ${row} · ${bikeLabel(b)}`;

function overdueTip(bikes) {
  return `<div class="fm-group">${bikes.length} overdue: the paid rental period ended before ${fmtDateShort(state.lastData.payment_as_of)}. Check the payment or the return (payment row in the "Operations" sheet)</div>` +
    bikes.map((b) => fmRow(rowLabel(b.problem_row, b), `${b.overdue_days} d`)).join("");
}

function pluralErrors(n) {
  return n === 1 ? "error" : "errors";
}

function noOpsTip(bikes) {
  return `<div class="fm-group">${bikes.length} rented with no payments: the rental was not entered or the status is wrong</div>` +
    bikes.map((b) => `<div>${escapeHtml(bikeLabel(b))}</div>`).join("");
}

/* One rule for every rent row without end_date: one error per row,
   newest first, with the sheet row so it is quick to fix. A rented bike whose
   latest payment is such a row is not listed a second time. */
function noTermTip(rows) {
  const LIMIT = 12;
  let html = `<div class="fm-group">${rows.length} rent payments without an end date (end_date), rows in the "Operations" sheet</div>`;
  html += rows.slice(0, LIMIT).map((r) => fmRow(`row ${r.row} · ${r.bike || "bike " + (r.bike_id ?? "?")}`, fmtThb(r.total))).join("");
  if (rows.length > LIMIT) html += `<div class="fm-row muted"><span>and ${rows.length - LIMIT} more</span><span></span></div>`;
  return html;
}

function bikeTitle(b) {
  return [b.brand, b.model].filter(Boolean).join(" ") || b.full_name;
}

function bikeInfoTip(b, problems) {
  const rows = [
    ["Type", b.vehicle_type],
    ["Colour", b.color],
    ["Engine", b.capacity ? `${b.capacity} cc` : ""],
    ["Plate", b.plate || "not set"],
    ["Status", b.status],
    ["Owner", b.owner],
    ["Rent in period", fmtThb(b.rent)],
    ["Delivery in period", b.delivery ? fmtThb(b.delivery) : ""],
    ["Revenue in period", fmtThb(b.revenue)],
    ["Last payment", b.last_paid_at ? fmtDateShort(b.last_paid_at) : "—"],
  ].filter(([, v]) => v);
  let html = `<div class="fm-title">${escapeHtml(b.full_name)}</div>` + rows.map(([k, v]) => fmRow(k, escapeHtml(v))).join("");
  problemNotes(b, problems).forEach((note) => (html += `<div class="fm-note">${escapeHtml(note)}</div>`));
  return html;
}

function renderBikeTable(bikes) {
  const table = els.bikeTable;
  table.innerHTML = `
    <thead><tr><th>Bike</th><th>Status</th><th>Revenue</th><th>ROI</th></tr></thead>
    <tbody>${bikes
      .map(
        (b) =>
          `<tr><td>${escapeHtml(b.full_name)}</td><td>${escapeHtml(b.status)}</td><td>${fmtThb(b.revenue)}</td><td>${b.roi === null || b.roi === undefined ? "—" : Math.round(b.roi * 100) + "%"}</td></tr>`
      )
      .join("")}</tbody>
  `;
}

/* ---------------- revenue chart ---------------- */

function drawRevenueChart() {
  if (!state.lastData) return;
  const body = els.dailyChartBody;
  const w = body.clientWidth;
  const h = body.clientHeight;
  if (!w || !h) return;
  state.chartSize = `${w}x${h}`;
  renderRevenueChart(body, w, h, state.lastData.daily, state.animateChart && !REDUCED_MOTION.matches);
  state.animateChart = false;
}

function renderDailySection(data) {
  const bucket = data.daily.bucket;
  els.dailyChartTitle.textContent = { day: "Revenue by day", week: "Revenue by week", month: "Revenue by month" }[bucket];
  const r = data.range;
  els.dailyChartSub.innerHTML = `<span class="range-tip">${fmtRange(r.start, r.effective_end)}</span>`;
  attachBreakdown(
    els.dailyChartSub.querySelector(".range-tip"),
    r.has_previous_data
      ? `<div class="fm-head">Compared with</div>${fmRow(prevPeriodName(r).replace(/^./, (c) => c.toUpperCase()), "")}<div class="fm-note">Hover over the chart to see the matching value from the previous period and the difference.</div>`
      : `<div class="fm-note">${noPrevText(r).replace(/^./, (c) => c.toUpperCase())}</div>`
  );
  drawRevenueChart();
  renderCompareToggle(data.range);

  if (!state.chartResizeObserver) {
    state.chartResizeObserver = new ResizeObserver(
      debounce(() => {
        const b = els.dailyChartBody;
        if (`${b.clientWidth}x${b.clientHeight}` !== state.chartSize) drawRevenueChart();
      }, 80)
    );
    state.chartResizeObserver.observe(els.dailyChartBody);
  }
}

/* Left button follows the period: week, month or quarter over the previous
   one; right button is always the same dates a year back. */
const COMPARE_PREV = {
  last_7_days: ["WoW", "Week vs previous week"],
  last_30_days: ["MoM", "30 days vs previous 30 days"],
  this_month: ["MoM", "Month vs previous month, same dates"],
  this_quarter: ["QoQ", "Quarter vs previous quarter, same dates"],
};
const NO_COMPARE_TOGGLE = new Set(["all_time", "this_year"]);
const effectiveCompare = () => (NO_COMPARE_TOGGLE.has(state.currentRange) ? "prev" : state.compare);

function renderCompareToggle(nextRange) {
  const range = nextRange || state.lastData?.range || { key: state.currentRange };
  // No choice to make here: "all time" has nothing before it, and for the
  // calendar year the previous period already is the same dates a year ago.
  els.compareToggle.hidden = NO_COMPARE_TOGGLE.has(range.key);
  const [prevLabel, prevTitle] = COMPARE_PREV[range.key] || ["PoP", "Vs the previous period of the same length"];
  const opts = [
    ["prev", prevLabel, prevTitle],
    ["yoy", "YoY", "Vs the same dates a year ago"],
  ];
  // Built once and then only updated, so the thumb can slide between
  // positions instead of jumping on every re-render.
  if (!els.compareToggle.querySelector(".cmp-thumb")) {
    els.compareToggle.innerHTML =
      `<span class="cmp-thumb" aria-hidden="true"></span>` +
      opts.map(([key]) => `<button type="button" class="cmp-btn" data-cmp="${key}"></button>`).join("");
    els.compareToggle.querySelectorAll(".cmp-btn").forEach((b) =>
      b.addEventListener("click", () => {
        if (state.compare === b.dataset.cmp) return;
        state.compare = b.dataset.cmp;
        renderCompareToggle(); // slide right away, data follows
        writeRangeToUrl(state.currentRange);
        load();
      })
    );
  }
  opts.forEach(([key, label, title], i) => {
    const b = els.compareToggle.querySelector(`[data-cmp="${key}"]`);
    const on = state.compare === key;
    b.textContent = label;
    b.title = title;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", on);
    if (on) els.compareToggle.style.setProperty("--cmp-index", i);
  });
}

/* ---------------- table view toggles ---------------- */

function wireTableToggle(button, wrap, chartBodyOrList) {
  button.setAttribute("aria-pressed", "false");
  button.addEventListener("click", () => {
    const showing = wrap.hidden;
    wrap.hidden = !showing;
    if (chartBodyOrList) chartBodyOrList.hidden = showing;
    button.textContent = showing ? "Show as list" : "Show as table";
    button.setAttribute("aria-pressed", showing ? "true" : "false");
  });
}
wireTableToggle(els.bikeTableToggle, els.bikeTableWrap, els.bikeListSection);

/* ---------------- range controls ---------------- */

function renderRangeControls() {
  els.rangeRow.innerHTML = "";
  els.rangeSelect.innerHTML = "";
  state.ranges.forEach((r) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "range-pill" + (r.key === state.currentRange ? " active" : "");
    btn.dataset.key = r.key;
    btn.setAttribute("aria-pressed", r.key === state.currentRange ? "true" : "false");
    btn.textContent = r.label;
    btn.addEventListener("click", () => selectRange(r.key));
    els.rangeRow.appendChild(btn);

    const opt = document.createElement("option");
    opt.value = r.key;
    opt.textContent = r.label;
    opt.selected = r.key === state.currentRange;
    els.rangeSelect.appendChild(opt);
  });

  const isCustom = state.currentRange === "custom";
  const custom = document.createElement("button");
  custom.type = "button";
  custom.id = "custom-range-btn";
  custom.className = "range-pill custom-pill" + (isCustom ? " active" : "");
  custom.setAttribute("aria-pressed", isCustom ? "true" : "false");
  custom.setAttribute("aria-haspopup", "dialog");
  custom.innerHTML = `${CALENDAR_ICON}<span>${isCustom ? fmtPillRange(state.customFrom, state.customTo) : "Custom"}</span>`;
  custom.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleDatePicker(custom);
  });
  els.rangeRow.appendChild(custom);
}

const CALENDAR_ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><rect x="3.5" y="5" width="17" height="15.5" rx="2.5" stroke="currentColor" stroke-width="1.8"/><path d="M3.5 10h17M8 3v4M16 3v4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`;

/* The pill has little room: a range that needs years goes numeric
   ("12/15/25 – 01/15/26"); the subtitles still spell it out in full. */
function fmtPillRange(a, b) {
  const full = fmtRange(a, b);
  if (!/\d{4}/.test(full)) return full;
  const short = (iso) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(2, 4)}`;
  return a === b ? short(a) : `${short(a)} – ${short(b)}`;
}

/* Hand-typed period: two native date inputs, limited to the days that have data. */
let datePicker = null;
function toggleDatePicker(anchor) {
  if (datePicker && !datePicker.hidden) {
    datePicker.hidden = true;
    return;
  }
  if (!datePicker) {
    datePicker = document.createElement("div");
    datePicker.className = "date-picker";
    datePicker.setAttribute("role", "dialog");
    datePicker.setAttribute("aria-label", "Custom period");
    datePicker.innerHTML = `
      <div class="dp-fields">
        <label>From<input type="date" id="dp-from" /></label>
        <span class="dp-dash">—</span>
        <label>To<input type="date" id="dp-to" /></label>
      </div>
      <div class="dp-error" id="dp-error" hidden></div>
      <div class="dp-actions">
        <button type="button" class="dp-cancel" id="dp-cancel">Cancel</button>
        <button type="button" class="dp-apply" id="dp-apply">Show</button>
      </div>`;
    document.body.appendChild(datePicker);
    datePicker.addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("click", () => (datePicker.hidden = true));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") datePicker.hidden = true; });
    datePicker.querySelector("#dp-cancel").addEventListener("click", () => (datePicker.hidden = true));
    datePicker.querySelector("#dp-apply").addEventListener("click", applyDatePicker);
    datePicker.addEventListener("keydown", (e) => { if (e.key === "Enter") applyDatePicker(); });
  }
  const d = state.lastData;
  const min = d?.earliest_data || "";
  const max = d?.data_as_of || "";
  const from = datePicker.querySelector("#dp-from");
  const to = datePicker.querySelector("#dp-to");
  [from, to].forEach((i) => { i.min = min; i.max = max; });
  from.value = state.customFrom || d?.range.start || min;
  to.value = state.customTo || d?.range.effective_end || max;
  datePicker.querySelector("#dp-error").hidden = true;
  datePicker.hidden = false;
  const r = anchor.getBoundingClientRect();
  const maxLeft = document.documentElement.clientWidth - datePicker.offsetWidth - 8;
  datePicker.style.left = Math.max(8, Math.min(r.left, maxLeft)) + window.scrollX + "px";
  datePicker.style.top = r.bottom + window.scrollY + 8 + "px";
  from.focus();
}

function applyDatePicker() {
  const from = datePicker.querySelector("#dp-from").value;
  const to = datePicker.querySelector("#dp-to").value;
  const fromInput = datePicker.querySelector("#dp-from");
  const toInput = datePicker.querySelector("#dp-to");
  const err = datePicker.querySelector("#dp-error");
  const fail = (msg) => { err.textContent = msg; err.hidden = false; };
  if (!from || !to) return fail("Enter both dates");
  if (from > to) return fail("Start date is after end date");
  if ((fromInput.min && from < fromInput.min) || (toInput.max && to > toInput.max)) return fail("Pick dates within the available data");
  if (!fromInput.validity.valid || !toInput.validity.valid) return fail("Enter valid dates");
  datePicker.hidden = true;
  state.customFrom = from;
  state.customTo = to;
  state.currentRange = "custom";
  state.statusFilter = "all";
  writeRangeToUrl("custom");
  renderRangeControls();
  load();
}
els.rangeSelect.addEventListener("change", () => selectRange(els.rangeSelect.value));

/* Flips the active pill in place instead of rebuilding the row, so the
   colour change can transition instead of snapping. */
function syncRangePills() {
  els.rangeRow.querySelectorAll(".range-pill[data-key]").forEach((btn) => {
    const on = btn.dataset.key === state.currentRange;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  els.rangeSelect.value = state.currentRange;
}

function selectRange(key) {
  if (state.currentRange === key) return;
  const wasCustom = state.currentRange === "custom";
  state.currentRange = key;
  state.statusFilter = "all";
  writeRangeToUrl(key);
  // Leaving a custom period changes the custom pill's label, so rebuild then.
  if (wasCustom) renderRangeControls();
  else syncRangePills();
  load();
}

/* ---------------- error banner ---------------- */

function showError(message, retryAction = null) {
  state.retryAction = retryAction;
  els.errorBanner.hidden = false;
  els.errorText.textContent = message;
}
function hideError() {
  els.errorBanner.hidden = true;
}
els.errorRetry.addEventListener("click", () => state.retryAction ? state.retryAction() : state.initialized ? load() : init());

/* ---------------- refresh ---------------- */

async function refreshDashboard() {
  els.refreshBtn.disabled = true;
  els.refreshBtn.classList.add("spinning");
  try {
    await Api.refresh();
    if (state.initialized) await load();
    else await init();
  } catch (e) {
    showError("Could not refresh the data. " + e.message, refreshDashboard);
  } finally {
    els.refreshBtn.disabled = false;
    els.refreshBtn.classList.remove("spinning");
  }
}
els.refreshBtn.addEventListener("click", refreshDashboard);

/* ---------------- logout ---------------- */

Api.me().then((me) => { els.logoutBtn.hidden = !me.auth; }).catch(() => {});
els.logoutBtn.addEventListener("click", async () => {
  try { await Api.logout(); } catch (e) {}
  location.replace("/login");
});

/* ---------------- timestamps (one line, details in a tooltip) ---------------- */

function renderTimestamps(data) {
  els.demoBadge.hidden = !data.demo;
  els.updatedLine.textContent = `Loaded ${timeAgo(data.cache_loaded_at)} · data through ${fmtDateShort(data.data_as_of || data.range.end)}`;
  els.updatedLine.title = `Sheet last modified: ${fullDateTime(data.sheet_modified_at)}\nData loaded from the sheet: ${fullDateTime(data.cache_loaded_at)}`;
  els.updatedLine.dataset.iso = data.cache_loaded_at || "";
  els.updatedLine.dataset.asof = data.data_as_of || data.range.end;
}
setInterval(() => {
  const iso = els.updatedLine.dataset.iso;
  if (iso) els.updatedLine.textContent = `Loaded ${timeAgo(iso)} · data through ${fmtDateShort(els.updatedLine.dataset.asof)}`;
  refreshForNewDay();
}, 30000);

function refreshForNewDay() {
  const data = state.lastData;
  // The demo is frozen at its snapshot date: there is no new day to wait for.
  if (document.hidden || state.loading || !data?.payment_as_of || data.demo) return;
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: data.timezone || "Etc/GMT-7", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  const part = (type) => parts.find((p) => p.type === type).value;
  const today = `${part("year")}-${part("month")}-${part("day")}`;
  if (today !== data.payment_as_of) load();
}
document.addEventListener("visibilitychange", refreshForNewDay);

/* ---------------- charts fill the screen ---------------- */

/* Side by side (wide screens) the two chart cards stretch from their top
   down to the bottom of the window, so a 14" MacBook and a 27" monitor both
   show the whole page without scrolling. Clamped so short screens keep a
   readable chart and huge ones do not get a stretched one. Stacked layout
   (narrow screens) keeps the fixed height from the CSS. */
const CHARTS_MIN_H = 400;
const CHARTS_MAX_H = 760;
function fitChartsHeight() {
  const row = document.querySelector(".charts-row");
  if (!row) return;
  const cards = row.querySelectorAll(".chart-card");
  const sideBySide = cards.length > 1 && Math.abs(cards[0].getBoundingClientRect().top - cards[1].getBoundingClientRect().top) < 4;
  if (!sideBySide) {
    row.style.removeProperty("--charts-h");
    return;
  }
  const top = row.getBoundingClientRect().top + window.scrollY;
  const bottomGap = parseFloat(getComputedStyle(document.querySelector(".page")).paddingBottom) || 16;
  const h = Math.round(Math.min(CHARTS_MAX_H, Math.max(CHARTS_MIN_H, window.innerHeight - top - bottomGap)));
  row.style.setProperty("--charts-h", h + "px");
}
window.addEventListener("resize", debounce(fitChartsHeight, 80));

/* ---------------- main load ---------------- */

async function load() {
  state.loading = true;
  const seq = ++state.requestSeq;
  if (state.abortController) state.abortController.abort();
  const controller = new AbortController();
  state.abortController = controller;

  if (!state.lastData) renderSkeletons();
  else {
    els.dailyChartBody.style.opacity = "0.45";
    els.bikeList.style.opacity = "0.45";
    els.kpiRow.style.opacity = "0.6";
  }

  try {
    const isCustom = state.currentRange === "custom";
    const data = await Api.dashboard(state.currentRange, controller.signal, isCustom ? state.customFrom : undefined, isCustom ? state.customTo : undefined, effectiveCompare());
    if (seq !== state.requestSeq) return;

    hideError();
    state.lastData = data;
    state.animateChart = true;
    renderTimestamps(data);
    renderKpis(data);
    renderFleet(data);
    renderStatusFilter(data);
    renderBikeSection(data);
    fitChartsHeight(); // before drawing, so the chart is drawn once at its final height
    renderDailySection(data);
  } catch (err) {
    if (err.name === "AbortError") return;
    if (seq !== state.requestSeq) return;
    console.error(err);
    showError("Could not load the data. " + err.message);
  } finally {
    if (seq === state.requestSeq) {
      state.loading = false;
      els.dailyChartBody.style.opacity = "1";
      els.bikeList.style.opacity = "1";
      els.kpiRow.style.opacity = "1";
    }
  }
}

async function init() {
  renderThemeIcon();
  const urlRange = readRangeFromUrl();
  try {
    state.ranges = await Api.ranges();
  } catch (e) {
    showError("Could not reach the server.");
    return;
  }
  const params = new URLSearchParams(window.location.search);
  if (params.get("cmp") === "yoy") state.compare = "yoy";
  const isoDate = /^\d{4}-\d{2}-\d{2}$/;
  if (urlRange === "custom" && isoDate.test(params.get("from") || "") && isoDate.test(params.get("to") || "")) {
    state.customFrom = params.get("from");
    state.customTo = params.get("to");
    state.currentRange = "custom";
  } else if (urlRange && state.ranges.some((r) => r.key === urlRange)) {
    state.currentRange = urlRange;
  } else {
    state.currentRange = state.ranges.some((r) => r.key === "last_30_days") ? "last_30_days" : state.ranges.some((r) => r.key === "all_time") ? "all_time" : state.ranges[0]?.key || "all_time";
    writeRangeToUrl(state.currentRange);
  }
  state.initialized = true;
  renderRangeControls();
  await load();
}

init();
