/* ==========================================================================
   pipecat-quickstart · 项目全景观览页 交互逻辑
   无第三方依赖（Chart.js 由 charts.js 单独使用）
   ========================================================================== */
(function () {
  "use strict";

  var doc = document;
  var root = doc.documentElement;

  /* ---------------- 主题切换 ---------------- */
  var THEME_KEY = "po-theme";
  var stored = null;
  try { stored = localStorage.getItem(THEME_KEY); } catch (e) { stored = null; }
  if (stored === "dark" || stored === "light") root.setAttribute("data-theme", stored);

  function currentTheme() {
    var attr = root.getAttribute("data-theme");
    if (attr) return attr;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark" : "light";
  }
  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
    doc.dispatchEvent(new CustomEvent("themechange", { detail: { theme: theme } }));
  }
  var themeToggle = doc.getElementById("themeToggle");
  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      applyTheme(currentTheme() === "dark" ? "light" : "dark");
    });
  }

  /* ---------------- 汉堡菜单 ---------------- */
  var hamburger = doc.getElementById("hamburger");
  var navLinks = doc.getElementById("navLinks");
  if (hamburger && navLinks) {
    hamburger.addEventListener("click", function () { navLinks.classList.toggle("open"); });
    navLinks.addEventListener("click", function (e) {
      if (e.target.closest(".nav__link")) navLinks.classList.remove("open");
    });
  }

  /* ---------------- 滚动进度条 + 返回顶部 ---------------- */
  var progressBar = doc.getElementById("progressBar");
  var toTop = doc.getElementById("toTop");
  function onScroll() {
    var h = doc.documentElement;
    var max = h.scrollHeight - h.clientHeight;
    var pct = max > 0 ? (h.scrollTop / max) * 100 : 0;
    if (progressBar) progressBar.style.width = pct.toFixed(2) + "%";
    if (toTop) toTop.classList.toggle("show", h.scrollTop > h.clientHeight * 0.8);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  onScroll();

  if (toTop) {
    toTop.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  /* ---------------- 章节高亮（导航 + 侧边栏） ---------------- */
  var sections = Array.prototype.slice.call(doc.querySelectorAll("main section[id]"));
  var navItems = Array.prototype.slice.call(doc.querySelectorAll(".nav__link"));
  var sideItems = Array.prototype.slice.call(doc.querySelectorAll(".sidebar__link"));

  function setActive(id) {
    navItems.forEach(function (a) {
      a.classList.toggle("is-active", a.getAttribute("href") === "#" + id);
    });
    sideItems.forEach(function (a) {
      a.classList.toggle("is-active", a.getAttribute("href") === "#" + id);
    });
  }

  if ("IntersectionObserver" in window && sections.length) {
    var visible = {};
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        visible[en.target.id] = en.isIntersecting ? en.intersectionRatio : 0;
      });
      var best = null, bestRatio = -1;
      sections.forEach(function (s) {
        var r = visible[s.id] || 0;
        if (r > bestRatio) { bestRatio = r; best = s.id; }
      });
      if (best) setActive(best);
    }, { rootMargin: "-72px 0px -55% 0px", threshold: [0, 0.25, 0.5, 0.75, 1] });
    sections.forEach(function (s) { io.observe(s); });
  }

  /* ---------------- Tabs ---------------- */
  Array.prototype.forEach.call(doc.querySelectorAll("[data-tabs]"), function (group) {
    var tabs = Array.prototype.slice.call(group.querySelectorAll(".tab"));
    var panels = Array.prototype.slice.call(group.querySelectorAll(".tab-panel"));
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        var name = tab.getAttribute("data-tab");
        tabs.forEach(function (t) { t.classList.toggle("is-active", t === tab); });
        panels.forEach(function (p) {
          p.classList.toggle("is-active", p.getAttribute("data-panel") === name);
        });
        // 面板切换后通知图表重算尺寸
        window.dispatchEvent(new Event("resize"));
      });
    });
  });

  /* ---------------- 数字计数动画 ---------------- */
  function animateCount(el) {
    var target = parseFloat(el.getAttribute("data-count") || el.textContent);
    if (isNaN(target)) return;
    var suffix = el.getAttribute("data-suffix") || "";
    var dur = 1100, start = null;
    function step(ts) {
      if (!start) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased) + suffix;
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = target + suffix;
    }
    requestAnimationFrame(step);
  }
  var counters = Array.prototype.slice.call(doc.querySelectorAll("[data-count]"));
  if ("IntersectionObserver" in window && counters.length) {
    var cio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { animateCount(en.target); cio.unobserve(en.target); }
      });
    }, { threshold: 0.4 });
    counters.forEach(function (c) { cio.observe(c); });
  } else {
    counters.forEach(animateCount);
  }

  /* ---------------- 入场动画 ---------------- */
  var reveals = Array.prototype.slice.call(
    doc.querySelectorAll(".section, .hero, .card, .stat-card")
  );
  reveals.forEach(function (el) { el.classList.add("reveal"); });
  if ("IntersectionObserver" in window) {
    var rio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); rio.unobserve(en.target); }
      });
    }, { threshold: 0.06, rootMargin: "0px 0px -40px 0px" });
    reveals.forEach(function (el) { rio.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add("in"); });
  }

  /* ---------------- 架构图 tooltip ---------------- */
  var arch = doc.querySelector(".arch");
  var tip = doc.getElementById("archTip");
  if (arch && tip) {
    var host = arch.parentElement; // .card--flush（position: relative）
    function showTip(target) {
      var text = target.getAttribute("data-tip");
      if (!text) return;
      var r = target.getBoundingClientRect();
      var hr = host.getBoundingClientRect();
      tip.textContent = text;
      tip.style.left = (r.left - hr.left + r.width / 2) + "px";
      tip.style.top = (r.top - hr.top) + "px";
      tip.hidden = false;
      arch.classList.add("dim");
    }
    function hideTip() { tip.hidden = true; arch.classList.remove("dim"); }
    Array.prototype.forEach.call(arch.querySelectorAll("[data-tip]"), function (node) {
      node.addEventListener("mouseenter", function () { showTip(node); });
      node.addEventListener("mouseleave", hideTip);
      node.addEventListener("focus", function () { showTip(node); });
      node.addEventListener("blur", hideTip);
    });
    window.addEventListener("scroll", function () { if (!tip.hidden) hideTip(); }, { passive: true });
  }

  /* ---------------- 代码复制 ---------------- */
  Array.prototype.forEach.call(doc.querySelectorAll("[data-copy]"), function (btn) {
    btn.addEventListener("click", function () {
      var block = btn.closest(".code-block");
      var code = block && block.querySelector("code");
      if (!code) return;
      var text = code.innerText;
      var done = function () {
        var old = btn.textContent;
        btn.textContent = "已复制";
        btn.classList.add("done");
        setTimeout(function () { btn.textContent = old; btn.classList.remove("done"); }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { fallback(text, done); });
      } else {
        fallback(text, done);
      }
    });
  });
  function fallback(text, cb) {
    var ta = doc.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    doc.body.appendChild(ta); ta.select();
    try { doc.execCommand("copy"); cb(); } catch (e) {}
    doc.body.removeChild(ta);
  }

  /* ---------------- 键盘：/ 聚焦搜索式跳转（Esc 关闭菜单） ---------------- */
  doc.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && navLinks) navLinks.classList.remove("open");
  });
})();
