/* site.js — plain-JS replacement for the Claude Design runtime.
 *
 * WHY THIS EXISTS: pages exported from Claude Design carry their behaviour in a
 * <script type="text/x-dc"> block whose body subclasses DCLogic, a class that only
 * exists inside Design's own viewer. Browsers do not execute a script with an
 * unrecognised type, so the block is skipped in silence: no console error, no
 * behaviour. Served as a template, the page looks right and does nothing.
 *
 * Every hook below is an attribute the exported markup already carries. Nothing here
 * invents new markup, and every lookup is guarded, so a page missing a hook degrades
 * to a static page rather than throwing.
 *
 * All state in these bundles lives in INLINE styles, not classes, so this file writes
 * inline styles too. Any original value it overwrites is stashed on the element first
 * and restored on the way back, so a design change in Claude Design does not need a
 * matching edit here.
 */
(function () {
  "use strict";

  var $  = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ------------------------------------------------------------------ *
   * 1. Accent colour, handed in through data-props on the dead script.
   *    Read it if present so a palette change in Design still lands.
   * ------------------------------------------------------------------ */
  (function accent() {
    var el = $("[data-props]");
    if (!el) return;
    try {
      var p = JSON.parse(el.getAttribute("data-props"));
      var c = p && p.accentColor;
      if (typeof c === "string" && /^#[0-9a-f]{3,8}$/i.test(c)) {
        document.documentElement.style.setProperty("--accent", c);
      }
    } catch (e) { /* malformed props are not worth breaking the page over */ }
  })();

  /* ------------------------------------------------------------------ *
   * 2. Rail width. The sidebar is position:fixed at width:var(--rail),
   *    and the article is offset by the same variable, so one value
   *    moves both. Collapsing sets it narrow rather than hiding the
   *    sidebar, which would leave the article's margin stranded.
   * ------------------------------------------------------------------ */
  var RAIL_OPEN = 288, RAIL_SHUT = 0, NARROW = 900;
  var root = document.documentElement;
  var railOpen = true;

  function setRail(px) { root.style.setProperty("--rail", px + "px"); }

  function syncRail() {
    if (window.innerWidth < NARROW) { setRail(RAIL_SHUT); return; }
    setRail(railOpen ? RAIL_OPEN : RAIL_SHUT);
  }

  var toggle = $("[data-toggle-toc]");
  if (toggle) {
    toggle.setAttribute("role", "button");
    toggle.setAttribute("tabindex", "0");
    toggle.setAttribute("aria-expanded", "true");
    var flip = function () {
      railOpen = !railOpen;
      toggle.setAttribute("aria-expanded", String(railOpen));
      syncRail();
    };
    toggle.addEventListener("click", flip);
    toggle.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); flip(); }
    });
  }
  window.addEventListener("resize", syncRail);
  syncRail();

  /* ------------------------------------------------------------------ *
   * 3. Collapsible phase section. The head is the click target, the body
   *    carries data-open, and the chevron rotates. Height is animated off
   *    scrollHeight so the transition works without a fixed max-height.
   * ------------------------------------------------------------------ */
  $$("[data-phase-head]").forEach(function (head) {
    var phase = head.closest("[data-phase]") || head.parentNode;
    var body  = $("[data-phase-body]", phase);
    var chev  = $("[data-chev]", phase);
    if (!body) return;

    head.setAttribute("role", "button");
    head.setAttribute("tabindex", "0");
    head.setAttribute("aria-expanded", "true");

    var open = body.getAttribute("data-open") !== "0";

    var paint = function (animate) {
      body.setAttribute("data-open", open ? "1" : "0");
      head.setAttribute("aria-expanded", String(open));
      if (chev) chev.style.transform = "rotate(" + (open ? 0 : -90) + "deg)";
      if (!animate) { body.style.display = open ? "block" : "none"; return; }
      if (open) {
        body.style.display = "block";
        var h = body.scrollHeight;
        body.style.overflow = "hidden";
        body.style.height = "0px";
        requestAnimationFrame(function () {
          body.style.transition = "height .22s ease";
          body.style.height = h + "px";
          setTimeout(function () {
            body.style.transition = ""; body.style.height = ""; body.style.overflow = "";
          }, 240);
        });
      } else {
        body.style.overflow = "hidden";
        body.style.height = body.scrollHeight + "px";
        requestAnimationFrame(function () {
          body.style.transition = "height .22s ease";
          body.style.height = "0px";
          setTimeout(function () {
            body.style.display = "none";
            body.style.transition = ""; body.style.height = ""; body.style.overflow = "";
          }, 240);
        });
      }
    };

    paint(false);
    var hit = function (e) { e.preventDefault(); open = !open; paint(true); };
    head.addEventListener("click", hit);
    head.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") hit(e);
    });
  });

  /* ------------------------------------------------------------------ *
   * 4. Table-of-contents filter. Matches on visible text. Entries that
   *    fail the match are hidden, not removed, so scrollspy keeps its
   *    references and clearing the box restores the list intact.
   * ------------------------------------------------------------------ */
  var search = $("[data-search]");
  var entries = $$("[data-toc]");

  if (search && entries.length) {
    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      entries.forEach(function (a) {
        var hit = !q || (a.textContent || "").toLowerCase().indexOf(q) > -1;
        var row = a.closest("li") || a;
        row.style.display = hit ? "" : "none";
      });
    });
    search.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { search.value = ""; search.dispatchEvent(new Event("input")); }
    });
  }

  /* ------------------------------------------------------------------ *
   * 5. Scrollspy. No active class exists in these bundles, so the active
   *    entry is styled inline and its original values are stashed first.
   *    IntersectionObserver reports what is on screen; the topmost
   *    intersecting section wins, which keeps a short trailing section
   *    from stealing the highlight from a long one above it.
   * ------------------------------------------------------------------ */
  var targets = entries
    .map(function (a) {
      var id = a.getAttribute("data-toc");
      var el = id && document.getElementById(id);
      return el ? { link: a, el: el } : null;
    })
    .filter(Boolean);

  if (targets.length && "IntersectionObserver" in window) {
    targets.forEach(function (t) {
      t.link.dataset.c0 = t.link.style.color || "";
      t.link.dataset.w0 = t.link.style.fontWeight || "";
    });

    var mark = function (active) {
      targets.forEach(function (t) {
        var on = t.link === active;
        t.link.style.color = on ? "var(--accent)" : t.link.dataset.c0;
        t.link.style.fontWeight = on ? "600" : t.link.dataset.w0;
        if (on) t.link.setAttribute("aria-current", "true");
        else t.link.removeAttribute("aria-current");
      });
    };

    var visible = new Set();
    var io = new IntersectionObserver(function (recs) {
      recs.forEach(function (r) {
        if (r.isIntersecting) visible.add(r.target); else visible.delete(r.target);
      });
      var best = null, bestTop = Infinity;
      visible.forEach(function (el) {
        var top = el.getBoundingClientRect().top;
        if (top < bestTop) { bestTop = top; best = el; }
      });
      if (best) {
        var t = targets.filter(function (x) { return x.el === best; })[0];
        if (t) mark(t.link);
      }
    }, { rootMargin: "-12% 0px -70% 0px", threshold: 0 });

    targets.forEach(function (t) { io.observe(t.el); });

    targets.forEach(function (t) {
      t.link.addEventListener("click", function () { mark(t.link); });
    });
  }

  /* ------------------------------------------------------------------ *
   * 6. Scroll progress bar. Guarded against a zero-length scroll, which
   *    is what a short page or a collapsed phase produces, and which
   *    would otherwise divide by zero and paint NaN%.
   * ------------------------------------------------------------------ */
  var fill = $("[data-scrollfill]");
  if (fill) {
    var ticking = false;
    var paintFill = function () {
      var d = document.documentElement;
      var span = d.scrollHeight - d.clientHeight;
      var pct = span > 0 ? (d.scrollTop / span) * 100 : 0;
      fill.style.width = Math.max(0, Math.min(100, pct)).toFixed(2) + "%";
      ticking = false;
    };
    var onScroll = function () {
      if (!ticking) { ticking = true; requestAnimationFrame(paintFill); }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    paintFill();
  }

  /* ------------------------------------------------------------------ *
   * 7. Smooth anchor scrolling for the ToC, respecting a user who has
   *    asked for reduced motion.
   * ------------------------------------------------------------------ */
  var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  entries.forEach(function (a) {
    a.addEventListener("click", function (e) {
      var id = a.getAttribute("data-toc");
      var el = id && document.getElementById(id);
      if (!el) return;
      e.preventDefault();
      el.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
      history.replaceState(null, "", "#" + id);
      if (window.innerWidth < NARROW && railOpen && toggle) toggle.click();
    });
  });
})();
