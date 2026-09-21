// Polls the running download and shows its progress; reloads the page when it has finished.
(function () {
  var box = document.getElementById("progress");
  if (!box) { return; }
  var text = document.getElementById("progress-text");
  var bar = document.getElementById("progress-bar");
  var last = "";
  function tick() {
    fetch(box.getAttribute("data-url"), { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (s.state !== "running") { window.location.reload(); return; }
        var n = Math.min(s.done + 1, s.total);
        var verb = s.phase === "Synchronising" ? "Sending" : "Downloading";
        var line = verb + " file " + n + " of " + s.total + (s.current ? ": " + s.current : "") +
          (s.percent ? " (" + (Math.floor(s.percent / 10) * 10) + "%)" : "") + (s.cancelling ? " \u2014 cancelling\u2026" : "");
        if (line !== last) {                       // announce only real changes (10% steps, next file, cancelling)
          text.textContent = line;
          last = line;
        }
        bar.value = s.percent;
        bar.setAttribute("aria-valuetext", s.percent + "% of " + (s.current || "the current file"));
        setTimeout(tick, 1000);
      })
      .catch(function () { setTimeout(tick, 3000); });
  }
  tick();
})();
