/* ==========================================================================
   voice-module-dify · 项目全景观览页 图表
   数据来源（真源，与正文 / README / 实测输出一致）：
     端到端时序  server/app.py 运行时日志的 [TURN n] 行（自托管 Dify + 魔搭，2026-10-05）
     平台延迟    同上；「平台自身」为绕开语音层直接调用平台接口的首字延迟
     ASR 取舍    faster_whisper 直接对比 base vs small（同音频 + 同语言/提示词）
   注意：本页所有动态数值须与正文同源一致 —— 修改此处请同步 index.html。
   ========================================================================== */
(function () {
  "use strict";
  if (typeof Chart === "undefined") return;

  Chart.defaults.font.family =
    '"Inter", system-ui, -apple-system, "Segoe UI", "PingFang SC", sans-serif';
  Chart.defaults.font.size = 11;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.boxHeight = 12;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;

  var PALETTE = { violet: "#8b5cf6", teal: "#14b8a6", blue: "#3b82f6", amber: "#f59e0b", slate: "#94a3b8" };

  function isDark() {
    var attr = document.documentElement.getAttribute("data-theme");
    if (attr) return attr === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function axisColor() {
    return isDark()
      ? { ticks: "#a9a3c2", grid: "rgba(148,163,184,.16)" }
      : { ticks: "#4d4763", grid: "rgba(100,116,139,.16)" };
  }

  var charts = {};

  /* ------------------------------ 端到端时序 ------------------------------ */
  function makeE2e(el) {
    var c = axisColor();
    return new Chart(el, {
      type: "line",
      data: {
        labels: ["请求发出", "平台首字", "首个完整句子", "交给 TTS"],
        datasets: [{
          label: "相对「请求发出」的累计延迟 (ms)",
          data: [0, 1334, 1346, 1346],
          borderColor: PALETTE.violet,
          backgroundColor: "rgba(139,92,246,.18)",
          tension: .35, fill: true, pointRadius: 4, pointBackgroundColor: PALETTE.violet
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { color: c.ticks } },
          tooltip: { callbacks: { label: function (ctx) { return ctx.parsed.y + " ms"; } } }
        },
        scales: {
          x: { ticks: { color: c.ticks }, grid: { display: false } },
          y: { beginAtZero: true, ticks: { color: c.ticks, callback: function (v) { return v + " ms"; } }, grid: { color: c.grid } }
        }
      }
    });
  }

  /* ------------------------------ 平台延迟 ------------------------------ */
  function makeBrain(el) {
    var c = axisColor();
    return new Chart(el, {
      type: "bar",
      data: {
        labels: ["本地 LLM\n（第一阶段）", "平台自身\n（绕开语音层）", "经模块\n（含音频→文本）"],
        datasets: [{
          label: "首字延迟 (ms)",
          // [min, max] -- the platform's own first token is a measured range (740-963ms)
          data: [[800, 800], [740, 963], [1334, 1334]],
          backgroundColor: ["rgba(148,163,184,.6)", "rgba(20,184,166,.8)", "rgba(139,92,246,.85)"],
          borderRadius: 8, maxBarThickness: 64
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var v = ctx.raw;
                if (Array.isArray(v)) return v[0] === v[1] ? v[0] + " ms" : v[0] + "–" + v[1] + " ms";
                return ctx.parsed.y + " ms";
              }
            }
          }
        },
        scales: {
          x: { ticks: { color: c.ticks }, grid: { display: false } },
          y: { beginAtZero: true, ticks: { color: c.ticks, callback: function (v) { return v + " ms"; } }, grid: { color: c.grid } }
        }
      }
    });
  }

  /* ------------------------------ ASR 取舍 ------------------------------ */
  function makeAsr(el) {
    var c = axisColor();
    return new Chart(el, {
      type: "bar",
      data: {
        labels: ["base", "small", "base + 业务词提示词"],
        datasets: [{
          label: "单句耗时 (ms)",
          data: [600, 1600, 600],
          backgroundColor: ["rgba(20,184,166,.8)", "rgba(244,63,94,.75)", "rgba(139,92,246,.8)"],
          borderRadius: 8, maxBarThickness: 60
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              afterLabel: function (ctx) {
                if (ctx.dataIndex === 0) return "基准";
                if (ctx.dataIndex === 1) return "没变准（慢 2.7×）";
                return "修好「保修期」，耗时不变";
              }
            }
          }
        },
        scales: {
          x: { ticks: { color: c.ticks }, grid: { display: false } },
          y: { beginAtZero: true, ticks: { color: c.ticks, callback: function (v) { return v + " ms"; } }, grid: { color: c.grid } }
        }
      }
    });
  }

  var factories = { e2e: makeE2e, brain: makeBrain, asr: makeAsr };

  function activate(name) {
    if (charts[name]) return;
    var id = "chart" + name.charAt(0).toUpperCase() + name.slice(1);
    var el = document.getElementById(id);
    if (!el || !factories[name]) return;
    charts[name] = factories[name](el);
  }

  // 懒初始化：隐藏面板尺寸为 0，会让图表塌陷
  activate("e2e");
  Array.prototype.forEach.call(document.querySelectorAll("[data-tabs] .tab"), function (tab) {
    tab.addEventListener("click", function () {
      var name = tab.getAttribute("data-tab");
      requestAnimationFrame(function () { activate(name); });
    });
  });

  // 主题切换：更新坐标轴颜色
  document.addEventListener("themechange", function () {
    var c = axisColor();
    Object.keys(charts).forEach(function (k) {
      var ch = charts[k];
      Object.keys(ch.options.scales || {}).forEach(function (s) {
        var sc = ch.options.scales[s];
        if (sc.ticks) sc.ticks.color = c.ticks;
        if (sc.grid && sc.grid.color) sc.grid.color = c.grid;
      });
      if (ch.options.plugins && ch.options.plugins.legend && ch.options.plugins.legend.labels) {
        ch.options.plugins.legend.labels.color = c.ticks;
      }
      ch.update("none");
    });
  });
})();
