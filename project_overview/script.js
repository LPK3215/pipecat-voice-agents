/* ==========================================================================
   pipecat-ai-test · Project Overview — interaction logic
   Navigation highlight / scroll progress / tree folding / tabs / number count-up /
   theme toggle / tooltips on project cards / back-to-top
   ========================================================================== */
(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  /* ---------- 1. Theme (follows the system by default, manual toggle is remembered) ---------- */
  var THEME_KEY = 'pipecat-overview-theme';
  var root = document.documentElement;

  function applyStoredTheme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) { /* private mode */ }
    if (saved === 'dark' || saved === 'light') root.setAttribute('data-theme', saved);
  }

  function currentEffectiveTheme() {
    var attr = root.getAttribute('data-theme');
    if (attr === 'dark' || attr === 'light') return attr;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  applyStoredTheme();

  var themeToggle = $('#themeToggle');
  if (themeToggle) {
    themeToggle.addEventListener('click', function () {
      var next = currentEffectiveTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* ignore */ }
      document.dispatchEvent(new CustomEvent('overview:theme', { detail: { theme: next } }));
    });
  }

  /* ---------- 2. Scroll progress bar ---------- */
  var progress = $('#progress');
  var toTop = $('#toTop');

  function onScroll() {
    var h = document.documentElement;
    var max = h.scrollHeight - h.clientHeight;
    var pct = max > 0 ? (h.scrollTop || document.body.scrollTop) / max * 100 : 0;
    if (progress) progress.style.width = pct.toFixed(2) + '%';
    if (toTop) toTop.classList.toggle('show', (window.scrollY || h.scrollTop) > window.innerHeight * 0.9);
  }

  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  if (toTop) {
    toTop.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }

  /* ---------- 3. Active section highlight (top nav + sidebar) ---------- */
  var navLinks = $$('.nav a, .sidebar-nav a');
  var sections = $$('section[id]');

  function setActive(id) {
    navLinks.forEach(function (a) {
      a.classList.toggle('active', a.getAttribute('href') === '#' + id);
    });
  }

  if ('IntersectionObserver' in window && sections.length) {
    var visible = {};
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { visible[en.target.id] = en.isIntersecting ? en.intersectionRatio : 0; });
      var best = null, bestRatio = 0;
      sections.forEach(function (s) {
        var r = visible[s.id] || 0;
        if (r > bestRatio) { bestRatio = r; best = s.id; }
      });
      if (best) setActive(best);
    }, { rootMargin: '-72px 0px -55% 0px', threshold: [0, 0.15, 0.4, 0.75, 1] });

    sections.forEach(function (s) { io.observe(s); });
  }

  /* ---------- 4. Mobile hamburger menu ---------- */
  var nav = $('#nav');
  var hamburger = $('#hamburger');

  if (hamburger && nav) {
    hamburger.addEventListener('click', function (e) {
      e.stopPropagation();
      nav.classList.toggle('open');
    });
    nav.addEventListener('click', function (e) {
      if (e.target.tagName === 'A') nav.classList.remove('open');
    });
    document.addEventListener('click', function (e) {
      if (nav.classList.contains('open') && !nav.contains(e.target) && e.target !== hamburger) {
        nav.classList.remove('open');
      }
    });
  }

  /* ---------- 5. Tabs (supports several groups per page) ---------- */
  $$('[data-tabs]').forEach(function (group) {
    var buttons = $$('.tab-btn', group);
    var panels = $$('.tab-panel', group);

    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var target = btn.getAttribute('data-tab');
        buttons.forEach(function (b) { b.classList.toggle('active', b === btn); });
        panels.forEach(function (p) {
          p.classList.toggle('active', p.getAttribute('data-panel') === target);
        });
        // Let charts re-measure after the panel becomes visible
        window.dispatchEvent(new Event('resize'));
      });
    });
  });

  /* ---------- 6. Copy buttons on code blocks ---------- */
  $$('.copy').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var box = btn.closest('.code');
      var codeEl = box ? $('pre code', box) : null;
      if (!codeEl) return;
      var text = codeEl.textContent;

      var done = function () {
        var old = btn.textContent;
        btn.textContent = 'Copied';
        btn.classList.add('done');
        setTimeout(function () { btn.textContent = old; btn.classList.remove('done'); }, 1400);
      };

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text, done); });
      } else {
        fallbackCopy(text, done);
      }
    });
  });

  function fallbackCopy(text, cb) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); cb(); } catch (e) { /* ignore */ }
    document.body.removeChild(ta);
  }

  /* ---------- 7. Count-up animation for key metrics ---------- */
  function animateNumber(el) {
    if (el.dataset.animated === '1') return;
    el.dataset.animated = '1';

    var target = parseFloat(el.getAttribute('data-count'));
    var decimals = parseInt(el.getAttribute('data-decimals') || '0', 10);
    if (isNaN(target)) return;

    var duration = 1100;
    var start = null;

    function step(ts) {
      if (start === null) start = ts;
      var p = Math.min((ts - start) / duration, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      var value = target * eased;
      el.textContent = decimals > 0
        ? value.toFixed(decimals)
        : Math.round(value).toLocaleString('en-US');
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = decimals > 0 ? target.toFixed(decimals) : target.toLocaleString('en-US');
    }
    requestAnimationFrame(step);
  }

  var counters = $$('[data-count]');
  if (counters.length) {
    if ('IntersectionObserver' in window) {
      var cio = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { animateNumber(en.target); cio.unobserve(en.target); }
        });
      }, { threshold: 0.4 });
      counters.forEach(function (c) { cio.observe(c); });
    } else {
      counters.forEach(animateNumber);
    }
  }

  /* ---------- 8. Tooltips on cards that carry data-tip ---------- */
  var tip = $('#archTip');
  var tipNodes = $$('.tip-node');

  if (tip && tipNodes.length) {
    var hideTimer = null;

    function wrap() { return tip.parentElement; }

    function showTip(node, evt) {
      var text = node.getAttribute('data-tip');
      if (!text) return;
      clearTimeout(hideTimer);
      tip.textContent = text;
      tip.classList.add('show');
      tip.setAttribute('aria-hidden', 'false');
      positionTip(evt, node);
    }

    function positionTip(evt, node) {
      var box = wrap().getBoundingClientRect();
      var x, y;
      if (evt && typeof evt.clientX === 'number' && evt.clientX !== 0) {
        x = evt.clientX - box.left + 14;
        y = evt.clientY - box.top + 14;
      } else {
        var r = (node || tip).getBoundingClientRect();
        x = r.left - box.left + 14;
        y = r.bottom - box.top + 10;
      }
      var maxX = box.width - tip.offsetWidth - 8;
      if (x > maxX) x = Math.max(8, maxX);
      if (x < 8) x = 8;
      tip.style.left = x + 'px';
      tip.style.top = y + 'px';
    }

    function hideTip() {
      hideTimer = setTimeout(function () {
        tip.classList.remove('show');
        tip.setAttribute('aria-hidden', 'true');
      }, 80);
    }

    tipNodes.forEach(function (node) {
      node.addEventListener('mouseenter', function (e) {
        tipNodes.forEach(function (n) { n.classList.toggle('dim', n !== node); });
        showTip(node, e);
      });
      node.addEventListener('mousemove', function (e) { positionTip(e, node); });
      node.addEventListener('mouseleave', function () {
        tipNodes.forEach(function (n) { n.classList.remove('dim'); });
        hideTip();
      });
      // Keyboard reachable
      node.addEventListener('focus', function () { showTip(node, null); });
      node.addEventListener('blur', function () {
        tipNodes.forEach(function (n) { n.classList.remove('dim'); });
        hideTip();
      });
      // Touch: tap to reveal
      node.addEventListener('click', function (e) {
        var t = node.getAttribute('data-tip');
        if (!t) return;
        tip.textContent = t;
        tip.classList.add('show');
        positionTip(e, node);
        setTimeout(function () { tip.classList.remove('show'); }, 3200);
      });
    });
  }

  /* ---------- 9. Tree folding state, remembered per branch ---------- */
  $$('.tree details').forEach(function (d) {
    var summary = $('summary', d);
    if (!summary) return;
    var key = 'tree:' + summary.textContent.trim().slice(0, 24);
    var saved = null;
    try { saved = localStorage.getItem(key); } catch (e) { /* ignore */ }
    if (saved === '0') d.open = false;
    if (saved === '1') d.open = true;
    d.addEventListener('toggle', function () {
      try { localStorage.setItem(key, d.open ? '1' : '0'); } catch (e) { /* ignore */ }
    });
  });

  /* ---------- 10. Smooth anchor scrolling (compensating for the fixed header) ---------- */
  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a[href^="#"]') : null;
    if (!a) return;
    var id = a.getAttribute('href').slice(1);
    if (!id) return;
    var el = document.getElementById(id);
    if (!el) return;
    e.preventDefault();
    var top = el.getBoundingClientRect().top + window.scrollY - 74;
    window.scrollTo({ top: top, behavior: 'smooth' });
    history.replaceState(null, '', '#' + id);
    setActive(id);
  });

  /* Sync the progress bar once everything has rendered */
  window.addEventListener('load', onScroll);
})();
