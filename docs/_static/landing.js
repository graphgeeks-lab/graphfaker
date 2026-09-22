// The landing page terminal shows the same command against three sinks and
// rotates between them. Clicking a tab stops the rotation: once someone has
// chosen, the page should not move under them. Hovering pauses it for the
// same reason. With prefers-reduced-motion the first panel simply stays.
(function () {
  "use strict";

  function start(terminal) {
    var tabs = Array.prototype.slice.call(terminal.querySelectorAll("[data-sink-tab]"));
    var panels = Array.prototype.slice.call(terminal.querySelectorAll("[data-sink-panel]"));
    if (tabs.length < 2 || tabs.length !== panels.length) return;

    var current = 0;
    var timer = null;
    var stopped = false;

    function show(index) {
      current = index;
      tabs.forEach(function (tab, i) {
        tab.classList.toggle("current", i === index);
        tab.setAttribute("aria-selected", i === index ? "true" : "false");
      });
      panels.forEach(function (panel, i) {
        panel.classList.toggle("current", i === index);
        panel.setAttribute("aria-hidden", i === index ? "false" : "true");
      });
    }

    function play() {
      if (stopped || timer) return;
      timer = window.setInterval(function () {
        show((current + 1) % panels.length);
      }, 5000);
    }

    function pause() {
      window.clearInterval(timer);
      timer = null;
    }

    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () {
        stopped = true;
        pause();
        show(i);
      });
    });
    terminal.addEventListener("mouseenter", pause);
    terminal.addEventListener("mouseleave", play);

    show(0);
    if (!window.matchMedia || !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      play();
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(document.querySelectorAll(".gf-terminal"), start);
  });
})();
