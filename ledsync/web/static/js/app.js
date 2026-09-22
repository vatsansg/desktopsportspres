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

// A live "= H:MM local time today" preview next to a UTC time-of-day box (the recurring timestamp
// cut-off): converts what was typed using today's date in this browser's own time zone, so the
// operator can see at a glance what the UTC value they typed means on their own clock, without the
// application ever treating anything but UTC as authoritative.
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
        target.textContent = "= " + asUtc.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + " local time today";
      }
      field.addEventListener("input", update);
      update();
    })(fields[i]);
  }
});
