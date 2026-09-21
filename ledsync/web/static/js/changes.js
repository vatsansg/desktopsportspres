// Polls the running download and shows its progress; reloads the page when it has finished.
(function () {
  var box = document.getElementById("progress");
  if (!box) { return; }
  var text = document.getElementById("progress-text");
  var bar = document.getElementById("progress-bar");
  function tick() {
    fetch(box.getAttribute("data-url"), { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (s.state !== "running") { window.location.reload(); return; }
        var n = Math.min(s.done + 1, s.total);
        text.textContent = "Downloading file " + n + " of " + s.total + (s.current ? ": " + s.current : "") +
          (s.percent ? " (" + s.percent + "%)" : "") + (s.cancelling ? " \u2014 cancelling\u2026" : "");
        bar.value = s.percent;
        setTimeout(tick, 1000);
      })
      .catch(function () { setTimeout(tick, 3000); });
  }
  tick();
})();
