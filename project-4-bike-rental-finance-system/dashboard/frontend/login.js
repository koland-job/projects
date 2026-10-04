const form = document.getElementById("login-form");
const userInput = document.getElementById("login-user");
const passwordInput = document.getElementById("login-password");
const revealBtn = document.getElementById("login-reveal");
const errorEl = document.getElementById("login-error");
const submitBtn = document.getElementById("login-submit");

const nextUrl = new URLSearchParams(location.search).get("next") || "/";
let lockTimer = null;
let locked = false;

const count = (n, one, other) => `${n} ${n === 1 ? one : other}`;

function showError(text) {
  errorEl.textContent = text;
  errorEl.hidden = false;
}

function setBusy(busy) {
  submitBtn.disabled = busy;
  submitBtn.textContent = busy ? "Signing in…" : "Sign in";
}

function lockFor(seconds) {
  clearInterval(lockTimer);
  const until = Date.now() + seconds * 1000;
  locked = true;
  submitBtn.disabled = true;
  const tick = () => {
    const left = Math.max(0, Math.ceil((until - Date.now()) / 60000));
    if (Date.now() >= until) {
      clearInterval(lockTimer);
      locked = false;
      submitBtn.disabled = false;
      errorEl.hidden = true;
      return;
    }
    showError(`Too many attempts. Try again in ${count(left, "minute", "minutes")}.`);
  };
  tick();
  lockTimer = setInterval(tick, 5000);
}

revealBtn.addEventListener("click", () => {
  const show = passwordInput.type === "password";
  passwordInput.type = show ? "text" : "password";
  revealBtn.setAttribute("aria-pressed", String(show));
  revealBtn.setAttribute("aria-label", show ? "Hide password" : "Show password");
  passwordInput.focus();
});

[userInput, passwordInput].forEach((input) =>
  input.addEventListener("input", () => {
    input.removeAttribute("aria-invalid");
    if (!locked) errorEl.hidden = true;
  })
);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const user = userInput.value.trim();
  const password = passwordInput.value;
  if (!user || !password) {
    showError(!user ? "Enter your username" : "Enter your password");
    (!user ? userInput : passwordInput).focus();
    return;
  }

  setBusy(true);
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user, password, next: nextUrl }),
    });
    const body = await res.json().catch(() => ({}));
    if (res.ok) {
      location.replace(body.next || "/");
      return;
    }
    setBusy(false);
    if (res.status === 429) {
      lockFor(body.retry_after || 900);
      return;
    }
    if (res.status === 401) {
      const left = body.attempts_left;
      const tail = left && left <= 3 ? ` ${count(left, "attempt", "attempts")} left.` : "";
      showError(`Wrong username or password.${tail}`);
      passwordInput.value = "";
      passwordInput.setAttribute("aria-invalid", "true");
      passwordInput.focus();
      return;
    }
    showError(body.detail || "Could not sign in. Please try again.");
  } catch (e) {
    setBusy(false);
    showError("Cannot reach the server. Check your connection and try again.");
  }
});
