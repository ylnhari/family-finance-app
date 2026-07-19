/* Shared theme controller — the SINGLE source of truth for the app's light/dark
   preference across index.html and the /invest dashboard (including the embedded
   Live Investments iframe). One localStorage key, one applied <html data-theme>
   attribute; every open page/tab stays in sync via the storage event. Loaded
   blocking in <head> so the theme is set before first paint (no flash). */
(function () {
  "use strict";
  var KEY = "ffa_theme";
  var root = document.documentElement;

  // one-time migration from the old invest-page-only key so a saved pref carries over
  if (!localStorage.getItem(KEY)) {
    var legacy = localStorage.getItem("inv-theme");
    if (legacy === "dark" || legacy === "light") localStorage.setItem(KEY, legacy);
  }

  function resolved() {
    var v = localStorage.getItem(KEY);
    if (v === "dark" || v === "light") return v;               // explicit user choice
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";  // else follow OS
  }
  function apply() { root.setAttribute("data-theme", resolved()); }
  apply();

  window.FFATheme = {
    KEY: KEY,
    current: function () { return root.getAttribute("data-theme"); },
    toggle: function () {
      var next = this.current() === "dark" ? "light" : "dark";
      localStorage.setItem(KEY, next);
      apply();
      return next;
    },
  };

  // another page/tab (or the parent<->iframe pair) changed the pref — re-apply
  window.addEventListener("storage", function (e) {
    if (e.key === KEY || e.key === null) apply();
  });
})();
