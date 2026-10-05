/* ==========================================================================
   pipecat-quickstart · 项目全景观览页 图表
   数据来源（真源，与正文 / README / 实测脚本输出一致）：
     ASR   scripts/asr_bench.py          · 6 句中文测试集
     TTS   README「ASR / TTS 引擎选型」   · 同句首个音频块
     LLM   README「关键：关闭思考模式」    · 直连首 token
     E2E   scripts/verify_stack.py       · 分段延迟（基准 = 合成音频推流结束）
     RAG   scripts/kb_eval.py            · hit@1 / hit@3 / MRR
   注意：全景观览页所有动态数值须与正文同源一致 —— 修改此处请同步 index.html。
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

  var PALETTE = {
    teal: "#14b8a6", violet: "#8b5cf6", blue: "#3b82f6",
    amber: "#f59e0b", slate: "#94a3b8", rose: "#f43f5e"
  };

  function isDark() {
    var attr = document.documentElement.getAttribute("data-theme");
    if (attr) return attr === "dark";
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function axisColor() {
    return isDark()
      ? { ticks: "#a3b1c6", grid: "rgba(148,163,184,.16)" }
      : { ticks: "#4b5870", grid: "rgba(100,116,139,.16)" };
  }
  function percent(v) { return v + "%"; }

  var charts = {};

  /* --------------------------------- ASR --------------------------------- */
  function makeAsr(el) {
    var c = axisColor();
    return new Chart(el, {
      data: {
        labels: ["Whisper base", "Whisper small", "SenseVoice（默认）"],
        datasets: [
          {
            type: "bar", label: "字错率 CER (%)",
            data: [23.8, 13.6, 10.2], yAxisID: "y",
            backgroundColor: ["rgba(148,163,184,.55)", "rgba(96,165,250,.6)", "rgba(20,184,166,.85)"],
            borderRadius: 8, maxBarThickness: 46
          },
          {
            type: "line", label: "单句耗时 (ms)",
            data: [607, 874, 158], yAxisID: "y1",
            borderColor: PALETTE.violet, backgroundColor: "rgba(139,92,246,.18)",
            tension: .35, fill: true, pointRadius: 4, pointBackgroundColor: PALETTE.violet
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: "bottom", labels: { color: c.ticks } } },
        scales: {
          x: { ticks: { color: c.ticks }, grid: { display: false } },
          y: { position: "left", beginAtZero: true, ticks: { color: c.ticks, callback: percent }, grid: { color: c.grid }, title: { display: true, text: "CER (%)", color: c.ticks } },
          y1: { position: "right", beginAtZero: true, ticks: { color: c.ticks, callback: function (v) { return v + " ms"; } }, grid: { drawOnChartArea: false }, title: { display: true, text: "耗时 (ms)", color: c.ticks } }
        }
      }
    });
  }

  /* --------------------------------- TTS --------------------------------- */
  function makeTts(el) {
    var c = axisColor();
    return new Chart(el, {
      type: "bar",
      data: {
        labels: ["Piper（默认）", "Kokoro zf_xiaoxiao"],
        datasets: [{
          label: "首个音频块延迟 (ms)",
          data: [76, 709],
          backgroundColor: ["rgba(20,184,166,.85)", "rgba(139,92,246,.75)"],
          borderRadius: 10, maxBarThickness: 90
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, indexAxis: "y",
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: function (ctx) { return ctx.parsed.x + " ms"; } } }
        },
        scales: {
          x: { beginAtZero: true, ticks: { color: c.ticks, callback: function (v) { return v + " ms"; } }, grid: { color: c.grid } },
          y: { ticks: { color: c.ticks }, grid: { display: false } }
        }
      }
    });
  }

  /* --------------------------------- LLM --------------------------------- */
  function makeLlm(el) {
    var c = axisColor();
    return new Chart(el, {
      type: "bar",
      data: {
        labels: ["Nex-N2.5-mini", "Qwen3.8-Flash-Next", "DeepSeek-V4.1-Flash"],
        datasets: [
          { label: "默认（带思考）", data: [830, 2869, 2447], backgroundColor: "rgba(148,163,184,.55)", borderRadius: 8, maxBarThickness: 40 },
          { label: "关闭思考后", data: [710, 787, 817], backgroundColor: "rgba(20,184,166,.85)", borderRadius: 8, maxBarThickness: 40 }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { color: c.ticks } },
          tooltip: { callbacks: { label: function (ctx) { return ctx.dataset.label + "：" + ctx.parsed.y + " ms"; } } }
        },
        scales: {
          x: { ticks: { color: c.ticks }, grid: { display: false } },
          y: { beginAtZero: true, ticks: { color: c.ticks, callback: function (v) { return v + " ms"; } }, grid: { color: c.grid }, title: { display: true, text: "首 token (ms)", color: c.ticks } }
        }
      }
    });
  }

  /* --------------------------------- E2E --------------------------------- */
  function makeE2e(el) {
    var c = axisColor();
    return new Chart(el, {
      type: "line",
      data: {
        labels: ["VAD 判定", "STT 文本", "LLM 首 token", "TTS 首次出声"],
        datasets: [
          {
            label: "2026-10-01（魔搭 nex）", data: [454, 1012, 1545, 1705],
            borderColor: PALETTE.teal, backgroundColor: "rgba(20,184,166,.16)",
            tension: .35, fill: true, pointRadius: 4, pointBackgroundColor: PALETTE.teal
          },
          {
            label: "2026-10-05（商汤）", data: [453, 859, 2038, 2071],
            borderColor: PALETTE.violet, backgroundColor: "rgba(139,92,246,.14)",
            tension: .35, fill: true, pointRadius: 4, pointBackgroundColor: PALETTE.violet
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { color: c.ticks } },
          tooltip: { callbacks: { label: function (ctx) { return ctx.dataset.label + "：" + ctx.parsed.y + " ms"; } } }
        },
        scales: {
          x: { ticks: { color: c.ticks }, grid: { display: false } },
          y: { beginAtZero: true, ticks: { color: c.ticks, callback: function (v) { return v + " ms"; } }, grid: { color: c.grid }, title: { display: true, text: "相对「说完」的累计延迟", color: c.ticks } }
        }
      }
    });
  }

  /* --------------------------------- RAG --------------------------------- */
  function makeRag(el) {
    var c = axisColor();
    return new Chart(el, {
      type: "bar",
      data: {
        labels: ["hit@1", "hit@3", "MRR ×100"],
        datasets: [
          { label: "旧默认（300/0 纯余弦）", data: [33, 40, 41], backgroundColor: "rgba(148,163,184,.55)", borderRadius: 8, maxBarThickness: 40 },
          { label: "出厂配置（500/50 + 重排）", data: [53, 80, 69], backgroundColor: "rgba(245,158,11,.85)", borderRadius: 8, maxBarThickness: 40 }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: "bottom", labels: { color: c.ticks } } },
        scales: {
          x: { ticks: { color: c.ticks }, grid: { display: false } },
          y: { beginAtZero: true, max: 100, ticks: { color: c.ticks }, grid: { color: c.grid }, title: { display: true, text: "百分比 / MRR ×100", color: c.ticks } }
        }
      }
    });
  }

  var factories = { asr: makeAsr, tts: makeTts, llm: makeLlm, e2e: makeE2e, rag: makeRag };

  function activate(name) {
    if (charts[name]) return;
    var id = "chart" + name.charAt(0).toUpperCase() + name.slice(1);
    var el = document.getElementById(id);
    if (!el || !factories[name]) return;
    charts[name] = factories[name](el);
  }

  // 懒初始化：只在面板可见时创建（隐藏容器尺寸为 0 会让图表塌陷）
  activate("asr");
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
        if (sc.title) sc.title.color = c.ticks;
      });
      if (ch.options.plugins && ch.options.plugins.legend && ch.options.plugins.legend.labels) {
        ch.options.plugins.legend.labels.color = c.ticks;
      }
      ch.update("none");
    });
  });
})();
