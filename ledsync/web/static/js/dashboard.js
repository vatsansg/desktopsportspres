// Dashboard Operation Status: shows the progress of running Download & Sync jobs; reloads when one starts or ends.
(function () {
  var box = document.getElementById("operations");
  if (!box) { return; }
  var initial = Number(box.getAttribute("data-active")) > 0;
  var last = {};
  function line(op) {
    var verb = op.phase === "Synchronising" ? "Sending" : (op.phase === "Downloading" ? "Downloading" : "Working");
    if (!op.total) { return op.phase ? op.phase + "\u2026" : "Working\u2026"; }
    return verb + " file " + Math.min(op.done + 1, op.total) + " of " + op.total + (op.current ? ": " + op.current : "") +
      (op.percent ? " (" + (Math.floor(op.percent / 10) * 10) + "%)" : "") + (op.cancelling ? " \u2014 cancelling\u2026" : "");
  }
  function tick() {
    fetch(box.getAttribute("data-url"), { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var jobs = data.jobs || [];
        if ((jobs.length > 0) !== initial) { window.location.reload(); return; }
        jobs.forEach(function (op) {
          var panel = box.querySelector('[data-event="' + String(op.event_id).replace(/"/g, "") + '"]');
          if (!panel) { window.location.reload(); return; }
          var text = line(op);
          if (last[op.event_id] !== text) {                    // announce real changes only
            panel.querySelector(".op-text").textContent = text;
            panel.querySelector(".op-phase").textContent = op.phase || "Working";
            last[op.event_id] = text;
          }
          panel.querySelector(".op-counts").textContent = "Identified " + op.identified + " \u00b7 Downloaded " + op.downloaded +
            " \u00b7 Synchronised " + op.synchronised + " \u00b7 Errors " + op.errors;
          var bar = panel.querySelector(".op-bar");
          bar.value = op.percent;
          bar.setAttribute("aria-valuetext", op.percent + "% of " + (op.current || "the current file"));
        });
        setTimeout(tick, initial ? 1000 : 3000);
      })
      .catch(function () { setTimeout(tick, 4000); });
  }
  tick();
})();
