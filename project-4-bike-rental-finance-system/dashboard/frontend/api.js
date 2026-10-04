const API_BASE = "/api";

async function apiGet(path, params, signal, method = "GET") {
  const url = new URL(API_BASE + path, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) url.searchParams.set(key, value);
    }
  }
  const res = await fetch(url, { signal, method });
  if (res.status === 401) {
    // Session expired or logged out in another tab: back to the login page.
    location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search));
    throw new Error("Sign-in required");
  }
  if (!res.ok) {
    // The backend answers errors as {"detail": "..."} - show that sentence,
    // not the raw JSON.
    const body = await res.text().catch(() => "");
    let detail = body;
    try { detail = JSON.parse(body).detail || body; } catch (e) {}
    throw new Error(detail || `Server error (${res.status})`);
  }
  return res.json();
}

const Api = {
  ranges: (signal) => apiGet("/ranges", null, signal),
  dashboard: (range, signal, from, to, compare) => apiGet("/dashboard", { range, from, to, compare }, signal),
  refresh: () => apiGet("/refresh", null, undefined, "POST"),
  me: () => apiGet("/me"),
  logout: () => fetch(API_BASE + "/logout", { method: "POST" }),
};
