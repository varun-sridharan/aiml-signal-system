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
    /* Stash each row's original display before the first filter. These entries carry
     * an inline display:flex from the exported markup and nothing in the stylesheet
     * repeats it, so restoring with "" would delete the only rule setting it: the row
     * reappears as a default inline anchor, its bullet and label lose the flex gap,
     * and the list reflows onto one line. Same stash-and-restore rule the scrollspy
     * uses, applied to the one property this section overwrites. */
    entries.forEach(function (a) {
      var row = a.closest("li") || a;
      if (row.dataset.d0 === undefined) row.dataset.d0 = row.style.display || "";
    });

    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      entries.forEach(function (a) {
        var hit = !q || (a.textContent || "").toLowerCase().indexOf(q) > -1;
        var row = a.closest("li") || a;
        row.style.display = hit ? row.dataset.d0 : "none";
      });
    });
    search.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { search.value = ""; search.dispatchEvent(new Event("input")); }
    });
  }

  /* ------------------------------------------------------------------ *
   * 5. Scrollspy. No active class exists in these bundles, so the active
   *    entry is styled inline and its original values are stashed first.
   *    IntersectionObserver reports what is on screen; among the sections
   *    intersecting, the most deeply nested one wins.
   *
   *    Depth, not position, and the reason is parity. /plan and
   *    /plan/Plan.html render the same content, and the bundle that
   *    produces the second one highlights the innermost section. Picking
   *    the topmost instead made the two pages disagree through the first
   *    half of the scroll: the bundle marked m1-plan while this marked its
   *    parent m1. The bundle is what Design decided, so it is the rule.
   *
   *    The trade being accepted: a nested section now takes the highlight
   *    from its parent whenever both are on screen, which they usually are,
   *    since the child lives inside the parent. So a long parent section
   *    rarely holds the highlight once any child of it comes into view.
   *    That is the behaviour being matched, not a side effect of it.
   *
   *    Depth is read from the DOM rather than inferred from coordinates.
   *    Nesting is structural, and a geometric proxy would get it wrong the
   *    moment a child were positioned above its parent's box.
   * ------------------------------------------------------------------ */
  var targets = entries
    .map(function (a) {
      var id = a.getAttribute("data-toc");
      var el = id && document.getElementById(id);
      return el ? { link: a, el: el } : null;
    })
    .filter(Boolean);

  targets.forEach(function (t) {
    var d = 0, n = t.el;
    while ((n = n.parentElement)) d++;
    t.depth = d;
  });

  if (targets.length && "IntersectionObserver" in window) {
    /* Stash what mark() overwrites, per the rule at the top of this file. Note it is
     * `style.background`, the shorthand, not `style.backgroundColor`: the bundle sets
     * `background: var(--accent-soft)`, and a var() inside a shorthand cannot be
     * decomposed into longhands by the CSSOM, so backgroundColor reads empty while the
     * value is plainly there and computes to rgb(244, 231, 223). Reading the wrong one
     * is how this treatment looked like it had no background at all. */
    targets.forEach(function (t) {
      t.link.dataset.c0 = t.link.style.color || "";
      t.link.dataset.b0 = t.link.style.background || "";
    });

    /* These values are measured from the rendered bundle, not read off its source.
     * Active is --accent text on an --accent-soft tint; inactive is --soft text on
     * transparent. Two things that look like omissions are deliberate:
     *
     *  - Weight is never touched. The bundle leaves each entry at its authored weight,
     *    so an active sub-entry stays 400 and an active top-level entry stays 500.
     *    Forcing 600 here made the highlight heavier than the reference.
     *  - Inactive is --soft, not the stashed original. The bundle normalises every
     *    inactive entry to --soft, including sub-entries the markup authored as
     *    --faint, so restoring the stash would leave the sub-entries too pale. The
     *    stash is still recorded: once mark() has run it is the only copy of what the
     *    markup actually said. */
    var mark = function (active) {
      targets.forEach(function (t) {
        var on = t.link === active;
        t.link.style.color = on ? "var(--accent)" : "var(--soft)";
        t.link.style.background = on ? "var(--accent-soft)" : "transparent";
        if (on) t.link.setAttribute("aria-current", "true");
        else t.link.removeAttribute("aria-current");
      });
    };

    var visible = new Set();
    var io = new IntersectionObserver(function (recs) {
      recs.forEach(function (r) {
        if (r.isIntersecting) visible.add(r.target); else visible.delete(r.target);
      });
      /* Deepest wins; topmost breaks a tie between siblings at equal depth, which
       * keeps the choice stable rather than dependent on observer callback order. */
      var best = null, bestDepth = -1, bestTop = Infinity;
      visible.forEach(function (el) {
        var t = targets.filter(function (x) { return x.el === el; })[0];
        if (!t) return;
        var top = el.getBoundingClientRect().top;
        if (t.depth > bestDepth || (t.depth === bestDepth && top < bestTop)) {
          bestDepth = t.depth; bestTop = top; best = el;
        }
      });
      if (best) {
        var t = targets.filter(function (x) { return x.el === best; })[0];
        if (t) mark(t.link);
      }
    }, { rootMargin: "-12% 0px -70% 0px", threshold: 0 });

    targets.forEach(function (t) { io.observe(t.el); });

    /* Landing state. The observer's band runs from 12% to 30% of the viewport, and at
     * scroll 0 the first section starts below it, so nothing intersects and nothing is
     * marked. The bundle marks the first entry on load, and scroll 0 is not an edge
     * case: it is the view every visitor gets before touching the wheel, so an unmarked
     * ToC there is the most-seen state of the page rather than the least.
     *
     * Deferred a frame so the observer's own first callback wins if it has one. The
     * guard is `visible.size`, not a timer: if any section did intersect, the observer
     * has already chosen and this must not overrule it. */
    requestAnimationFrame(function () {
      if (!visible.size && targets.length) mark(targets[0].link);
    });

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
