// Show/hide toggle for password fields. No inline script anywhere (CSP: script-src 'self').
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-toggle-password]");
  if (!button) return;
  var input = document.getElementById(button.getAttribute("data-toggle-password"));
  if (!input) return;
  var show = input.type === "password";
  input.type = show ? "text" : "password";
  button.textContent = show ? "Hide" : "Show";
  input.focus();
});

// When a page loads with an error, move focus to it so keyboard and screen-reader users
// meet the message first (role="alert" alone is not reliably announced on a full page load).
document.addEventListener("DOMContentLoaded", function () {
  var alertBox = document.querySelector("[data-focus-alert]");   // opt-in: not the login page, which autofocuses Username
  if (!alertBox) return;
  alertBox.setAttribute("tabindex", "-1");
  alertBox.focus();
});

// A form that talks to Azure can take several seconds: show that it is working and block a
// double-submit. Opt-in per form via data-busy-text. (The button is disabled AFTER the browser
// has taken the form data, otherwise a disabled submitter would not be sent.)
document.addEventListener("submit", function (event) {
  var form = event.target;
  var text = form.getAttribute("data-busy-text");
  if (!text) return;
  var submitter = event.submitter;
  form.setAttribute("aria-busy", "true");
  setTimeout(function () {
    var buttons = form.querySelectorAll('button[type="submit"]');
    for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = true; }
    var label = submitter && submitter.getAttribute("data-busy-label");
    if (label || (submitter && submitter.classList.contains("btn-primary"))) {
      submitter.setAttribute("data-idle-text", submitter.textContent);
      submitter.textContent = label || text;
    }
  }, 0);
});

// Back/forward can restore this page from the browser's cache with the busy state still applied.
window.addEventListener("pageshow", function (event) {
  if (!event.persisted) return;
  var forms = document.querySelectorAll("form[data-busy-text]");
  for (var i = 0; i < forms.length; i++) {
    forms[i].removeAttribute("aria-busy");
    var buttons = forms[i].querySelectorAll('button[type="submit"]');
    for (var j = 0; j < buttons.length; j++) {
      buttons[j].disabled = false;
      var idle = buttons[j].getAttribute("data-idle-text");
      if (idle !== null) { buttons[j].textContent = idle; buttons[j].removeAttribute("data-idle-text"); }
    }
  }
});

// A plain, always-on reminder of "what UTC is right now" next to any field that needs a UTC value
// entered (the timestamp cut-off): the native datetime-local picker carries no time-zone marking of
// its own, so this is the cheapest way to keep the operator from typing local time by mistake.
document.addEventListener("DOMContentLoaded", function () {
  var clocks = document.querySelectorAll("[data-utc-clock]");
  if (!clocks.length) return;
  function tick() {
    var now = new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC";
    for (var i = 0; i < clocks.length; i++) { clocks[i].textContent = now; }
  }
  tick();
  setInterval(tick, 30000);
});

// A live "= H:MM local (24-hour)" preview next to a UTC time-of-day box (the recurring timestamp
// cut-off): converts the clock reading that was typed into this browser's own time zone, so the
// operator can see at a glance what the UTC value they typed sounds like on their own clock. This is
// a clock-reading conversion only - it does NOT say which calendar day the cut-off is currently
// holding changes back to (see formatBoundary below for that); the application never treats anything
// but UTC as authoritative.
document.addEventListener("DOMContentLoaded", function () {
  var fields = document.querySelectorAll("[data-utc-time-preview]");
  for (var i = 0; i < fields.length; i++) {
    (function (field) {
      var target = document.getElementById(field.getAttribute("data-utc-time-preview"));
      if (!target) return;
      function update() {
        var parts = (field.value || "").split(":");
        if (parts.length !== 2) { target.textContent = ""; return; }
        var hour = parseInt(parts[0], 10), minute = parseInt(parts[1], 10);
        if (isNaN(hour) || isNaN(minute)) { target.textContent = ""; return; }
        var now = new Date();
        var asUtc = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hour, minute));
        target.textContent = "= " + asUtc.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) + " local (24-hour)";
      }
      field.addEventListener("input", update);
      update();
    })(fields[i]);
  }
});

// The recurring cut-off's boundary is a UTC instant computed server-side (services/changes.py,
// cutoff_boundary) from the SAVED setting - today's occurrence if it has already passed, otherwise
// yesterday's. A local calendar date can fall on either side of that decision depending on the
// browser's UTC offset (e.g. early morning in a timezone ahead of UTC is still "yesterday" in UTC
// terms), so a bare "today" label can misstate which day is actually being held back. This states the
// real boundary and the next one in the operator's own local date and time instead of guessing.
document.addEventListener("DOMContentLoaded", function () {
  var elements = document.querySelectorAll("[data-utc-instant-preview]");
  function formatLocal(date) {
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return pad(date.getDate()) + "/" + pad(date.getMonth() + 1) + "/" + String(date.getFullYear()).slice(-2)
      + " " + pad(date.getHours()) + ":" + pad(date.getMinutes());
  }
  for (var i = 0; i < elements.length; i++) {
    var el = elements[i];
    var iso = el.getAttribute("data-utc-instant-preview");
    var boundary = iso ? new Date(iso) : null;
    if (!boundary || isNaN(boundary.getTime())) continue;
    var next = new Date(boundary.getTime() + 24 * 60 * 60 * 1000);
    el.textContent = "Right now, changes are held back if made after " + formatLocal(boundary)
      + " local time. The next quiet period begins " + formatLocal(next) + " local time.";
  }
});
