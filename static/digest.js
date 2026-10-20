/* Weekly Digest: the only JavaScript the page's interactions need.

   The page is rendered whole on the server. Without this file every section is open (a <noscript> rule in the
   template), every tab panel is visible, votes are plain forms, and footnote definitions show on hover or focus
   through CSS. With it: sections collapse and expand, the tabs switch, the sticky sub-nav follows the reader and
   opens the section it jumps to, and a link into a closed section opens it first.

   No framework, no network request. State lives in the DOM (aria-expanded, aria-selected, hidden). */
(function () {
  'use strict';
  var KEYS = ['thread', 'learning', 'stories', 'leaders'];
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var behavior = reduce ? 'auto' : 'smooth';

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return [].slice.call((root || document).querySelectorAll(sel)); }
  function secs() { return KEYS.map(function (k) { return $('#sec-' + k); }).filter(Boolean); }

  function setOpen(sec, open) {
    var btn = $('.sec-toggle', sec), body = $('.sec-body', sec);
    if (!btn || !body) return;
    sec.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    var arrow = $('.arrow', btn);
    if (arrow) arrow.textContent = open ? '▾' : '▸';
    body.setAttribute('data-open', open ? 'true' : 'false');
    var preview = $('.preview', sec);
    if (preview) preview.hidden = open;
    syncToggleAll();
  }
  function isOpen(sec) { return sec.classList.contains('open'); }

  var toggleAll = $('#toggle-all');
  function syncToggleAll() {
    if (!toggleAll) return;
    var all = secs().every(isOpen);
    toggleAll.textContent = all ? 'COLLAPSE ALL' : 'EXPAND ALL';
  }

  function scrollToEl(el, offset) {
    window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - offset, behavior: behavior });
  }
  function navHeight() {
    var nav = $('nav.subnav');
    var head = $('header');
    return (head ? head.getBoundingClientRect().bottom : 0) + (nav ? nav.offsetHeight : 0) + 6;
  }

  /* Open the section that contains `el` (if it is in a closed one), then scroll to it once it has room. */
  function reveal(el, thenScroll) {
    var host = el.closest && el.closest('[data-news-sec]');
    var wait = 0;
    if (host && !isOpen(host)) { setOpen(host, true); wait = reduce ? 0 : 170; }
    if (thenScroll) setTimeout(function () { scrollToEl(el, navHeight()); }, wait);
  }

  /* Section headings. */
  secs().forEach(function (sec) {
    var btn = $('.sec-toggle', sec);
    if (btn) btn.addEventListener('click', function () { setOpen(sec, !isOpen(sec)); });
  });

  /* Expand / collapse all, keeping the first visible heading where it is. */
  if (toggleAll) toggleAll.addEventListener('click', function () {
    var target = !secs().every(isOpen);
    var heads = secs().map(function (s) { return $('h2', s); });
    var navB = (function () { var n = $('nav.subnav'); return n ? n.getBoundingClientRect().bottom : 0; })();
    var anchor = heads.filter(function (h) { return h.getBoundingClientRect().top >= navB - 1; })[0] || null;
    var before = anchor ? anchor.getBoundingClientRect().top : 0;
    secs().forEach(function (s) { setOpen(s, target); });
    if (anchor) setTimeout(function () {
      var d = anchor.getBoundingClientRect().top - before;
      if (Math.abs(d) > 1) window.scrollBy(0, d);
    }, reduce ? 0 : 170);
  });

  /* Sub-nav: jump to a section, opening it. */
  $$('nav.subnav a[data-go]').forEach(function (a) {
    a.addEventListener('click', function (e) {
      var sec = document.getElementById('sec-' + a.getAttribute('data-go'));
      if (!sec) return;
      e.preventDefault();
      setOpen(sec, true);
      markCurrent(a.getAttribute('data-go'));
      setTimeout(function () { scrollToEl(sec, navHeight() - 2); }, reduce ? 0 : 170);
    });
  });

  function markCurrent(key) {
    $$('nav.subnav a[data-go]').forEach(function (a) {
      var on = a.getAttribute('data-go') === key;
      if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
    });
  }
  function onScroll() {
    var panel = $('#panel-news');
    if (!panel || panel.hidden) return;
    var cur = null;
    secs().forEach(function (s) { if (s.getBoundingClientRect().top <= 150) cur = s.getAttribute('data-news-sec'); });
    markCurrent(cur || (secs()[0] && secs()[0].getAttribute('data-news-sec')));
  }
  window.addEventListener('scroll', onScroll, { passive: true });

  /* In-page links (thread links, ties, "drawn from", footnote numbers) open their section before scrolling. */
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented) return;
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (!a) return;
    var id = a.getAttribute('href').slice(1);
    var el = id && document.getElementById(id);
    if (!el || !el.closest('[data-news-sec]')) return;
    e.preventDefault();
    reveal(el, true);
    if (history.replaceState) history.replaceState(null, '', '#' + id);
  });

  /* Tabs: NEWS and ARCHIVE. Arrow keys, Home and End move between them. */
  var tabs = $$('[role="tab"]');
  function selectTab(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      t.classList.toggle('on', on);
      var panel = document.getElementById(t.getAttribute('aria-controls'));
      if (panel) panel.hidden = !on;
    });
    if (focus) tab.focus();
    onScroll();
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener('click', function () { selectTab(tab, false); });
    tab.addEventListener('keydown', function (e) {
      var j = null;
      if (e.key === 'ArrowRight') j = (i + 1) % tabs.length;
      else if (e.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') j = 0;
      else if (e.key === 'End') j = tabs.length - 1;
      if (j !== null) { e.preventDefault(); selectTab(tabs[j], true); }
    });
  });

  /* A link that arrives with a #hash (a vote lands on its story) opens the section it is in. */
  if (location.hash.length > 1) {
    var target = null;
    try { target = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch (e) { /* a malformed hash is ignored */ }
    if (target) reveal(target, true);
  }

  /* Escape dismisses an open footnote definition (it stays reachable again by hover or focus). */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var a = document.activeElement;
    if (a && a.classList && a.classList.contains('fn-mark')) a.blur();
  });
  syncToggleAll();
  onScroll();
}());
