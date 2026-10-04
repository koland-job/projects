const SVG_NS = "http://www.w3.org/2000/svg";
const MINUS = "−"; // proper minus sign, not a hyphen

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function fmtThb(value) {
  if (value === null || value === undefined) return "—";
  const rounded = Math.round(value);
  const sign = rounded < 0 ? MINUS : "";
  return sign + new Intl.NumberFormat("en-US").format(Math.abs(rounded)) + " ฿";
}

function fmtPct(pct) {
  if (pct === null || pct === undefined) return null;
  let rounded = Math.round(pct);
  if (Object.is(rounded, -0)) rounded = 0;
  const sign = rounded > 0 ? "+" : rounded < 0 ? MINUS : "";
  return { rounded, text: `${sign}${Math.abs(rounded)}%` };
}

function fmtDateShort(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-US", { day: "numeric", month: "short" });
}

const pluralBikes = (n) => (n === 1 ? "bike" : "bikes");
const pluralInvestors = (n) => (n === 1 ? "investor" : "investors");

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML.replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function debounce(fn, ms) {
  let t = null;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

function observeResize(el, callback) {
  const run = debounce(() => callback(el.clientWidth), 80);
  const ro = new ResizeObserver(() => run());
  ro.observe(el);
  return ro;
}

function ensureTooltip(container) {
  let tip = container.querySelector(".viz-tooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "viz-tooltip";
    container.style.position = "relative";
    container.appendChild(tip);
  }
  return tip;
}

function showTooltip(tip, container, x, y, html) {
  tip.innerHTML = html;
  tip.classList.add("visible");
  const rect = container.getBoundingClientRect();
  requestAnimationFrame(() => {
    const tw = tip.offsetWidth || 160;
    const half = tw / 2 + 8;
    const left = Math.max(half, Math.min(x, rect.width - half));
    tip.style.left = left + "px";
    tip.style.top = Math.max(y - 12, 10) + "px";
  });
}

function hideTooltip(tip) {
  tip.classList.remove("visible");
}

/* Status -> color, grouped by meaning (see --st-* in styles.css): blue for
   earning, green for ready, warm for needs attention, grey for leaving. */
const STATUS_COLOR_VAR = {
  "Rented": "var(--series-1)",
  "Reserved": "var(--st-booked)",
  "Available": "var(--st-free)",
  "Idle": "var(--st-idle)",
  "In repair": "var(--st-repair)",
  "For sale": "var(--st-sale)",
  "Sold": "var(--st-sold)",
  "Retired": "var(--st-sold)",
  "Sold/Retired": "var(--st-sold)",
};
function statusColor(status) {
  return STATUS_COLOR_VAR[status] || "var(--text-muted)";
}

/* ---------------- Bike revenue list (HTML, % widths - reflows for free) ---------------- */

/* The sheet's colour names are free text ("Black", "Dark blue matte",
   "Red and black", "black/chrome", "White pearl"), so each part is matched
   by word stem. Two or three colours become a split swatch. */
const BIKE_COLORS = {
  // neutrals
  white: "#f4f3ef", ivory: "#f3eee2", cream: "#efe3c4", pearl: "#ece9e2",
  black: "#1d1d1b", graphite: "#3a3b3d", anthracite: "#2e3033", charcoal: "#3a3b3d", asphalt: "#4a4d52",
  gray: "#9b9a94", grey: "#9b9a94", ash: "#b4b2ab", steel: "#71797e", titanium: "#8a8d8f", silver: "#c9ccd1", chrome: "#c9ccd1", metallic: "#a8abb0",
  // reds / pinks
  red: "#d63a3a", scarlet: "#e5322d", burgundy: "#7a1f2b", maroon: "#7a1f2b", cherry: "#8e1b32", raspberry: "#c2185b", ruby: "#9b111e",
  pink: "#ea6fa8", fuchsia: "#d4219b", coral: "#f07158", terracotta: "#c0603f",
  // oranges / yellows / browns
  orange: "#f07c1f", copper: "#b8733a", bronze: "#a8712f",
  yellow: "#f2c230", lemon: "#f2e25a", mustard: "#c9a227", gold: "#c9a13b", sand: "#d8bf8a",
  beige: "#d9c7a3", brown: "#8b5a2b", chocolate: "#5a3622", coffee: "#6f4e37", khaki: "#8a8455", olive: "#6b7a3a",
  // greens / blues
  green: "#2f9e44", lime: "#b5d334", mint: "#7fd1b9", emerald: "#1f8a5b", turquoise: "#1fb5ad", teal: "#168b89", camo: "#6b6e4a",
  blue: "#2a6fd6", sky: "#79c1ee", azure: "#2b8fd6", indigo: "#3b3f9c", ultramarine: "#2a3fb0", navy: "#1f2d5a", cobalt: "#2f6bff",
  // purples
  purple: "#7b4bc4", violet: "#7b4bc4", lilac: "#b392d8", lavender: "#b9a7e0", plum: "#4b2a4a", magenta: "#8e2c8e",
};
const COLOR_KEYS = Object.keys(BIKE_COLORS).sort((a, b) => b.length - a.length);
// Mix to a concrete hex so contrast works for light/dark shades too.
const SHADE = { dark: 0.35, light: -0.45, pale: -0.55 };
const FINISH = /^(metallic|pearl)/;
function shadeHex(hex, shade) {
  const target = shade > 0 ? 0 : 255;
  return "#" + hex.slice(1).match(/../g).map((v) => Math.round(parseInt(v, 16) * (1 - Math.abs(shade)) + target * Math.abs(shade)).toString(16).padStart(2, "0")).join("");
}

function paintColors(name) {
  const text = String(name ?? "").toLowerCase().replace(/[‐‑–—]/g, "-");
  const colors = [];
  for (const chunk of text.split(/[\/,+&;]|\s+(?:and|with)\s+/)) {
    let shade = 0;
    const paints = [];
    for (let word of chunk.split(/[-\s]+/).filter(Boolean)) {
      const shadeKey = Object.keys(SHADE).find((k) => word.startsWith(k));
      if (shadeKey) {
        shade = SHADE[shadeKey];
        word = word.slice(shadeKey.length);
        if (!word) continue;
      }
      const key = COLOR_KEYS.find((k) => word.startsWith(k));
      const hex = /^#[0-9a-f]{6}$/.test(word) ? word : /^#[0-9a-f]{3}$/.test(word) ? "#" + [...word.slice(1)].map((c) => c + c).join("") : null;
      if (!key && !hex) continue;
      const base = hex || BIKE_COLORS[key];
      paints.push({ color: shade ? shadeHex(base, shade) : base, finish: FINISH.test(word) });
      shade = 0;
    }
    // "Blue metallic" is blue paint, not a blue/silver split circle.
    const hasPaint = paints.some((p) => !p.finish);
    colors.push(...paints.filter((p) => !hasPaint || !p.finish).map((p) => p.color));
  }
  return [...new Set(colors)].slice(0, 3);
}

function swatchTone(name) {
  const colors = paintColors(name);
  if (!colors.length) return " is-unknown";
  const luminance = colors.map((c) => {
    const n = parseInt(c.slice(1), 16);
    return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  });
  return luminance.every((v) => v > 0.85) ? " is-light" : luminance.every((v) => v < 0.2) ? " is-dark" : "";
}

function colorSwatch(name) {
  const colors = paintColors(name);
  if (!colors.length) return "transparent";
  if (colors.length === 1) return colors[0];
  const step = 100 / colors.length;
  return `linear-gradient(90deg, ${colors.map((c, i) => `${c} ${i * step}% ${(i + 1) * step}%`).join(", ")})`;
}

function splitBikeName(fullName) {
  const parts = (fullName || "").trim().split(/\s+/);
  if (parts.length <= 2) return { name: fullName || "Bike", meta: "" };
  const name = parts.slice(0, 2).join(" ");
  const meta = parts.slice(2).join(" · ");
  return { name, meta };
}

function renderBikeList(container, bikes, maxVal, total, prevWidths = null) {
  container.innerHTML = "";
  if (!bikes.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "No bikes in this category";
    container.appendChild(empty);
    return;
  }

  bikes.forEach((bike, i) => {
    const row = document.createElement("div");
    row.className = "bike-row" + (bike.revenue === 0 ? " zero" : "");

    const rank = document.createElement("span");
    rank.className = "bike-row-rank";
    rank.textContent = i + 1;

    const label = document.createElement("div");
    label.className = "bike-row-label";
    const title = [bike.brand, bike.model].filter(Boolean).join(" ") || splitBikeName(bike.full_name).name;
    const cc = bike.capacity ? `<span class="bike-row-cc">${escapeHtml(bike.capacity)} cc</span>` : "";
    // Marks come from the same per-bike problem model as the header badges.
    const badge =
      (bike._overdue ? `<span class="bike-badge overdue">overdue</span>` : "") +
      (bike._errors ? `<span class="bike-badge data-error">${bike._errors > 1 ? bike._errors + " " + pluralErrors(bike._errors) : "error"}</span>` : "");
    label.innerHTML = `<div class="bike-row-name"><span class="bike-swatch${swatchTone(bike.color)}" style="background:${colorSwatch(bike.color)}"></span><span class="bike-row-title">${escapeHtml(title)}</span>${cc}${badge}</div><div class="bike-row-meta">${bike.plate ? escapeHtml(bike.plate) : "no plate"}</div>`;

    const swatch = label.querySelector(".bike-swatch");
    swatch.title = !bike.color ? "Colour not set" : paintColors(bike.color).length ? bike.color : `Colour not recognised: ${bike.color}`;

    const track = document.createElement("div");
    track.className = "bike-row-track";
    const bar = document.createElement("div");
    bar.className = "bike-row-bar";
    const target = (maxVal > 0 ? (bike.revenue / maxVal) * 100 : 0) + "%";
    bar.dataset.target = target;
    bar.dataset.id = bike.bike_id;
    bar.style.width = prevWidths ? prevWidths.get(String(bike.bike_id)) || "0%" : target;
    if (prevWidths) {
      const delay = Math.min(i * 28, 560) + "ms";
      row.classList.add("enter");
      row.style.animationDelay = delay;
      bar.style.transitionDelay = delay;
    }
    track.appendChild(bar);

    const value = document.createElement("div");
    value.className = "bike-row-value";
    value.textContent = fmtThb(bike.revenue);

    row.append(rank, label, track, value);
    container.appendChild(row);
  });
  if (prevWidths) {
    void container.offsetWidth; // commit the start widths so the change transitions
    container.querySelectorAll(".bike-row-bar").forEach((b) => (b.style.width = b.dataset.target));
  }
}

/* Picks the smallest "nice" step (1/2/2.5/5 x 10^n) that covers maxVal in at
   most `maxTicks` intervals, so the top gridline sits just above the data
   instead of wasting a third of the plot. */
function niceTicks(maxVal, maxTicks = 5) {
  if (maxVal <= 0) return { step: 1, max: 1 };
  const magnitude = Math.pow(10, Math.floor(Math.log10(maxVal / maxTicks)));
  for (const m of [1, 2, 2.5, 5, 10, 20]) {
    const step = m * magnitude;
    if (Math.ceil(maxVal / step) <= maxTicks) return { step, max: Math.ceil(maxVal / step) * step };
  }
  return { step: maxVal, max: maxVal };
}

function compactNumber(v) {
  if (v >= 1000) return (v / 1000).toFixed(v % 1000 === 0 ? 0 : 1) + "K";
  return String(Math.round(v));
}

function bucketLabel(point, bucket) {
  if (bucket === "day") return fmtDateShort(point.start);
  if (bucket === "week") return fmtDateShort(point.start) + "–" + fmtDateShort(point.end);
  const d = new Date(point.start + "T00:00:00");
  return `${d.toLocaleDateString("en-US", { month: "short" })} \u2019${String(d.getFullYear()).slice(2)}`;
}

/* Axis ticks stay short; the full week range lives in the tooltip. */
function axisLabel(point, bucket) {
  return bucket === "week" ? fmtDateShort(point.start) : bucketLabel(point, bucket);
}

function drawAxes(svg, width, height, margin, plotW, plotH, maxVal, current, bucket) {
  const { step, max } = niceTicks(maxVal);
  const yAt = (v) => margin.top + plotH - (v / max) * plotH;
  const ticks = [];
  for (let v = 0; v <= max + 1e-9; v += step) ticks.push(v);
  ticks.forEach((v) => {
    const y = yAt(v);
    svg.appendChild(
      svgEl("line", { x1: margin.left, x2: width - margin.right, y1: y, y2: y, class: v === 0 ? "baseline-line" : "grid-line" })
    );
    const label = svgEl("text", { x: margin.left - 8, y: y + 4, "text-anchor": "end", class: "axis-text" });
    label.textContent = compactNumber(v);
    svg.appendChild(label);
  });

  // Short day ranges get every date centred under its point, so the plot is
  // inset by half a label on each side instead of pinning the ends.
  const everyDay = bucket === "day" && current.length <= 14;
  const inset = everyDay ? 24 : 0;
  const xAt = (i) => margin.left + inset + (current.length <= 1 ? 0 : (i / (current.length - 1)) * (plotW - 2 * inset));
  // X labels follow one regular rhythm - every day, every N weeks (anchored
  // on Mondays) or every N buckets - chosen by how many fit. Period ends are
  // not forced in: they already sit in the card subtitle, and forcing them
  // is what broke the rhythm before.
  const measure = (i) => {
    const t = svgEl("text", { class: "axis-text", "font-size": 11 });
    t.textContent = axisLabel(current[i], bucket);
    svg.appendChild(t);
    const w = t.getComputedTextLength();
    svg.removeChild(t);
    return w;
  };
  const n = current.length;
  const labelW = Math.max(...[0, Math.floor(n / 2), n - 1].map(measure));
  const minGap = labelW + 14;
  const pxPer = n > 1 ? (xAt(1) - xAt(0)) : plotW;
  let kept = [];
  if (everyDay) {
    const every = pxPer < minGap ? 2 : 1;
    for (let i = (n - 1) % every; i < n; i += every) kept.push(i);
  } else if (bucket === "day") {
    const mondays = [];
    current.forEach((d, i) => { if (new Date(d.start + "T00:00:00").getDay() === 1) mondays.push(i); });
    const weeks = [1, 2, 3, 4].find((k) => pxPer * 7 * k >= minGap);
    if (weeks) {
      kept = mondays.filter((_, j) => j % weeks === 0);
    } else {
      // Very long daily ranges: the 1st of every month (or every other).
      const firsts = [];
      current.forEach((d, i) => { if (d.start.endsWith("-01")) firsts.push(i); });
      const months = [1, 2, 3, 6].find((k) => pxPer * 30 * k >= minGap) || 6;
      kept = firsts.filter((_, j) => j % months === 0);
    }
  } else {
    const every = Math.max(1, Math.ceil(minGap / pxPer));
    for (let i = 0; i < n; i += every) kept.push(i);
  }
  // Every label centred under its point; drop one that would stick out of the plot.
  kept = kept.filter((i) => xAt(i) - labelW / 2 >= 2 && xAt(i) + labelW / 2 <= width - 2);
  kept.forEach((i) => {
    const label = svgEl("text", { x: xAt(i), y: height - 10, "text-anchor": "middle", class: "axis-text" });
    label.textContent = axisLabel(current[i], bucket);
    svg.appendChild(label);
  });

  return { xAt, yAt, max };
}

/* Monotone cubic path through the points: smooth, but never overshoots
   between two values (so a zero day never dips below the baseline). */
function monotonePath(pts) {
  const n = pts.length;
  if (n < 3) return "M " + pts.map((p) => `${p[0]},${p[1]}`).join(" L ");
  const dx = [], slope = [];
  for (let i = 0; i < n - 1; i++) {
    dx.push(pts[i + 1][0] - pts[i][0]);
    slope.push((pts[i + 1][1] - pts[i][1]) / dx[i]);
  }
  const t = [slope[0]];
  for (let i = 1; i < n - 1; i++) {
    if (slope[i - 1] * slope[i] <= 0) t.push(0);
    else {
      const w1 = 2 * dx[i] + dx[i - 1], w2 = dx[i] + 2 * dx[i - 1];
      t.push((w1 + w2) / (w1 / slope[i - 1] + w2 / slope[i]));
    }
  }
  t.push(slope[n - 2]);
  let d = `M ${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += ` C ${pts[i][0] + h},${pts[i][1] + t[i] * h} ${pts[i + 1][0] - h},${pts[i + 1][1] - t[i + 1] * h} ${pts[i + 1][0]},${pts[i + 1][1]}`;
  }
  return d;
}

/* ---------------- revenue area chart (day / week / month buckets) ---------------- */

function renderRevenueChart(container, width, height, payload, animate = false) {
  container.innerHTML = "";
  let { current } = payload;
  const { previous, bucket } = payload;
  if (!current.length) {
    container.innerHTML = '<div class="empty-state">No data for the selected period</div>';
    return;
  }

  const margin = { top: 12, right: 12, bottom: 28, left: 44 };
  const plotW = Math.max(width - margin.left - margin.right, 60);
  const plotH = Math.max(height - margin.top - margin.bottom, 60);

  // Buckets past data_as_of are "not entered yet", not zero - keep them on
  // the x-axis but never draw them as part of the line.
  // Days after data_as_of are not shown at all - the axis ends on the last entered day.
  current = current.filter((d) => !d.after_data);
  if (!current.length) {
    container.innerHTML = '<div class="empty-state">Data for this period has not been entered yet</div>';
    return;
  }
  const known = current.map((d, i) => ({ d, i }));
  // The scale follows the current period only, so switching the comparison
  // (WoW/MoM/QoQ vs YoY) never rescales the chart.
  const maxVal = Math.max(...known.map((p) => p.d.revenue), 1);

  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": "Revenue for the period" });
  container.appendChild(svg);
  const defs = svgEl("defs");
  const grad = svgEl("linearGradient", { id: "area-grad", x1: 0, y1: 0, x2: 0, y2: 1 });
  grad.appendChild(svgEl("stop", { offset: "0%", "stop-color": "var(--series-1)", "stop-opacity": 0.28 }));
  grad.appendChild(svgEl("stop", { offset: "100%", "stop-color": "var(--series-1)", "stop-opacity": 0.02 }));
  defs.appendChild(grad);
  svg.appendChild(defs);

  const { xAt, yAt } = drawAxes(svg, width, height, margin, plotW, plotH, maxVal, current, bucket);
  const baseY = margin.top + plotH;

  // New data draws itself in left to right (clip reveal); a plain resize
  // redraw does not animate.
  if (animate) svg.classList.add("chart-enter");
  const series = svgEl("g", { class: "series" });
  svg.appendChild(series);
  if (known.length > 1) {
    const pts = known.map((p) => [xAt(p.i), yAt(p.d.revenue)]);
    const line = monotonePath(pts);
    const lastX = pts[pts.length - 1][0];
    series.appendChild(svgEl("path", { d: `${line} L ${lastX},${baseY} L ${pts[0][0]},${baseY} Z`, class: "area-fill" }));
    // Partial (first/last) buckets are drawn dashed so an incomplete week
    // does not read as a real drop; the solid line covers complete buckets only.
    const fullIdx = known.map((p, k) => (p.d.partial ? -1 : k)).filter((k) => k >= 0);
    if (fullIdx.length === known.length) {
      series.appendChild(svgEl("path", { d: line, class: "line-path" }));
    } else {
      series.appendChild(svgEl("path", { d: line, class: "line-path partial" }));
      if (fullIdx.length > 1) series.appendChild(svgEl("path", { d: monotonePath(fullIdx.map((k) => pts[k])), class: "line-path" }));
    }
  }
  if (known.length) {
    const last = known[known.length - 1];
    svg.appendChild(svgEl("circle", { cx: xAt(last.i), cy: yAt(last.d.revenue), r: 3.5, class: last.d.partial ? "partial-dot" : "end-dot" }));
  }

  const crosshair = svgEl("line", { x1: 0, x2: 0, y1: margin.top, y2: baseY, class: "crosshair-line" });
  const hoverDot = svgEl("circle", { r: 5, class: "hover-dot" });
  const ghostDot = svgEl("circle", { r: 4.5, class: "ghost-dot" });
  svg.appendChild(crosshair);
  svg.appendChild(ghostDot);
  svg.appendChild(hoverDot);

  const tip = ensureTooltip(container);
  const overlay = svgEl("rect", { x: margin.left, y: margin.top, width: plotW, height: plotH, fill: "transparent" });
  const onMove = (e) => {
    if (!known.length) return;
    const bounds = svg.getBoundingClientRect();
    const px = e.clientX - bounds.left;
    const lastKnown = known[known.length - 1].i;
    // Nearest point by its real x (the scale may be inset for short ranges).
    let idx = 0;
    for (let i = 1; i <= lastKnown; i++) if (Math.abs(xAt(i) - px) < Math.abs(xAt(idx) - px)) idx = i;
    const cur = current[idx];
    const cx = xAt(idx);
    const cy = yAt(cur.revenue);
    crosshair.setAttribute("x1", cx);
    crosshair.setAttribute("x2", cx);
    crosshair.style.opacity = 1;
    hoverDot.setAttribute("cx", cx);
    hoverDot.setAttribute("cy", cy);
    hoverDot.style.opacity = 1;

    const p = previous && previous[idx];
    let prevHtml = "";
    if (p) {
      ghostDot.setAttribute("cx", cx);
      // A previous value above the scale sits pinned to the top edge.
      ghostDot.setAttribute("cy", Math.max(margin.top, yAt(p.revenue)));
      ghostDot.style.opacity = 1;
      const d = fmtPct(p.revenue === 0 ? null : ((cur.revenue - p.revenue) / Math.abs(p.revenue)) * 100);
      const dir = !d ? "" : d.rounded > 0 ? "up" : d.rounded < 0 ? "down" : "flat";
      const diff = cur.revenue - p.revenue;
      prevHtml = `<div class="tt-row"><span class="tt-key ghost"></span><span class="tt-muted">${bucketLabel(p, bucket)}${p.start.slice(0, 4) !== cur.start.slice(0, 4) ? " " + p.start.slice(0, 4) : ""}</span><strong class="tt-val">${fmtThb(p.revenue)}</strong></div>
        <div class="tt-diff">${d ? `<span class="tt-delta ${dir}">${d.text}</span>` : ""}<span class="tt-muted">${diff === 0 ? "no change" : (diff > 0 ? "+" : MINUS) + fmtThb(Math.abs(diff))}</span></div>`;
    } else {
      ghostDot.style.opacity = 0;
    }
    const partialRow = cur.partial ? `<div class="tt-muted">Partial period: ${cur.actual_days} of ${cur.expected_days} days</div>` : "";
    showTooltip(
      tip,
      container,
      px,
      Math.min(cy, p ? Math.max(margin.top, yAt(p.revenue)) : cy),
      `<div class="tt-row"><span class="tt-key"></span><span>${bucketLabel(cur, bucket)}</span><strong class="tt-val">${fmtThb(cur.revenue)}</strong></div>
       ${prevHtml}
       ${partialRow}`
    );
  };
  overlay.addEventListener("pointermove", onMove);
  overlay.addEventListener("pointerdown", onMove);
  overlay.addEventListener("pointerleave", () => {
    crosshair.style.opacity = 0;
    hoverDot.style.opacity = 0;
    ghostDot.style.opacity = 0;
    hideTooltip(tip);
  });
  svg.appendChild(overlay);
  container.appendChild(tip);
}
