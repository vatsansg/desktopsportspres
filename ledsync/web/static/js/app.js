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
