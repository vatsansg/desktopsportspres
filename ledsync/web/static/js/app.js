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
    if (label) { submitter.textContent = label; }
    else if (submitter && submitter.classList.contains("btn-primary")) { submitter.textContent = text; }
  }, 0);
});

// Back/forward can restore this page from the browser's cache with the busy state still applied.
window.addEventListener("pageshow", function (event) {
  if (!event.persisted) return;
  var forms = document.querySelectorAll("form[data-busy-text]");
  for (var i = 0; i < forms.length; i++) {
    forms[i].removeAttribute("aria-busy");
    var buttons = forms[i].querySelectorAll('button[type="submit"]');
    for (var j = 0; j < buttons.length; j++) { buttons[j].disabled = false; }
  }
});
