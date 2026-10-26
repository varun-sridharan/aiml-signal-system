/* gloss.js: the System Design page's glossary. A term in the text is <span class="gloss" data-term="key">; its definition is in the
   page's JSON glossary (<script type="application/json" id="glossary">). Hover or focus shows it; Escape or leaving hides it.
   Without this script the terms read as plain text. No network request. */
(function () {
  "use strict";
  var node = document.getElementById("glossary");
  var terms = {};
  if (node) { try { terms = JSON.parse(node.textContent); } catch (e) { terms = {}; } }
  var tip = null;
  function hide() { if (tip) { tip.remove(); tip = null; } }
  function show(el) {
    var entry = terms[el.getAttribute("data-term")];
    var card = el.querySelector(".gloss-card");
    if (!entry && card) entry = { term: (el.firstChild && el.firstChild.textContent || "").trim(), def: card.innerHTML };     // a definition written into a figure
    if (!entry) return;
    hide();
    tip = document.createElement("div");
    tip.className = "gloss-tip";
    tip.setAttribute("role", "tooltip");
    var head = document.createElement("b");
    head.className = "term";
    head.textContent = entry.term;
    var body = document.createElement("span");
    body.innerHTML = entry.def;           // definitions are our own content (design/System_Design.json), as the page's text is
    tip.appendChild(head);
    tip.appendChild(body);
    document.body.appendChild(tip);
    var r = el.getBoundingClientRect(), w = tip.offsetWidth;
    tip.style.left = Math.max(12, Math.min(window.innerWidth - w - 12, r.left)) + "px";
    var below = r.bottom + 8 + tip.offsetHeight < window.innerHeight;
    tip.style.top = (below ? r.bottom + 8 : Math.max(12, r.top - 8 - tip.offsetHeight)) + "px";
  }
  document.querySelectorAll(".gloss").forEach(function (el) {
    if (!el.getAttribute("data-term") && !el.querySelector(".gloss-card")) return;
    el.setAttribute("tabindex", "0");
    el.setAttribute("role", "button");
    el.setAttribute("aria-label", "Definition of " + (el.getAttribute("data-term") || (el.firstChild && el.firstChild.textContent || "this term").trim()));
    el.addEventListener("mouseenter", function () { show(el); });
    el.addEventListener("focus", function () { show(el); });
    el.addEventListener("mouseleave", hide);
    el.addEventListener("blur", hide);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });

  /* the decision log: every entry is a <details>; one button opens or closes them all */
  var all = document.querySelector("[data-log-all]");
  if (all) all.addEventListener("click", function () {
    var items = Array.prototype.slice.call(document.querySelectorAll(".decision-log details"));
    var open = !items.every(function (d) { return d.open; });
    items.forEach(function (d) { d.open = open; });
    all.textContent = open ? "COLLAPSE ALL" : "EXPAND ALL";
    all.setAttribute("aria-pressed", String(open));
  });
}());
