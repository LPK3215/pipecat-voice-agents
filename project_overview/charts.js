/* ==========================================================================
   pipecat-ai-test · Project Overview — chart configuration
   All data is taken from measured values recorded in the projects' own documentation
   (identical to the values in the surrounding text and tables on this page).

   - ① Voice pipeline latency (single audio probe; baseline = user finished speaking)
   - ① Chinese ASR character error rate (Whisper base / small / SenseVoice)
   - ① Retrieval quality (old default vs shipped config, hit@1 / hit@3)
   - ② Platform round-trip (platform's own first token vs voice-layer request -> first token)

   Dependency: Chart.js 4 (CDN, loaded with a <script> tag in index.html)
   ========================================================================== */
(function () {
  'use strict';

  if (typeof Chart === 'undefined') {
    console.warn('[charts] Chart.js is not loaded, skipping chart rendering');
    return;
  }

  var root = document.documentElement;

  function token(name, fallback) {
    var v = getComputedStyle(root).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }

  function palette() {
    return {
      c1: token('--c1', '#22c55e'),
      c2: token('--c2', '#3b82f6'),
      c3: token('--c3', '#a855f7'),
      text: token('--text', '#0f172a'),
      muted: token('--muted', '#64748b'),
      grid: token('--line', '#e2e8f0')
    };
  }

  Chart.defaults.font.family =
    "system-ui, -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif";
  Chart.defaults.font.size = 11;

  var registry = [];

  function make(canvasId, configBuilder) {
    var el = document.getElementById(canvasId);
    if (!el) return;
    el.style.height = '320px';
    el.style.width = '100%';

    var chart = new Chart(el, configBuilder(palette()));
    registry.push({ canvas: el, build: configBuilder, chart: chart });
  }

  /* Shared: rounded-bar base configuration */
  function baseBar(p, opts) {
    opts = opts || {};
    return {
      indexAxis: opts.horizontal ? 'y' : 'x',
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 900, easing: 'easeOutQuart' },
      plugins: {
        legend: {
          display: !!opts.legend,
          position: 'bottom',
          labels: { color: p.text, boxWidth: 12, boxHeight: 12, usePointStyle: true, padding: 14 }
        },
        tooltip: {
          backgroundColor: p.text,
          titleColor: '#fff',
          bodyColor: '#fff',
          padding: 10,
          cornerRadius: 8,
          displayColors: false,
          callbacks: opts.tooltip || {}
        }
      },
      scales: {
        x: {
          grid: { color: p.grid, drawBorder: false },
          ticks: { color: p.muted, maxRotation: opts.horizontal ? 0 : 28 },
          title: opts.xTitle ? { display: true, text: opts.xTitle, color: p.muted } : undefined
        },
        y: {
          beginAtZero: true,
          grid: { color: p.grid, drawBorder: false },
          ticks: { color: p.muted },
          title: opts.yTitle ? { display: true, text: opts.yTitle, color: p.muted } : undefined
        }
      }
    };
  }

  function bar(color, data, opts) {
    opts = opts || {};
    return {
      type: 'bar',
      data: {
        labels: opts.labels,
        datasets: [{
          label: opts.label || 'Value',
          data: data,
          backgroundColor: opts.soft || color,
          borderColor: color,
          borderWidth: 2,
          borderRadius: 8,
          maxBarThickness: opts.thickness || 46
        }]
      }
    };
  }

  /* ---------- ① Voice pipeline latency ---------- */
  make('chartLatency', function (p) {
    var cfg = baseBar(p, { horizontal: true, xTitle: 'Latency (ms)' });
    cfg = Object.assign(cfg, bar(p.c2, [934, 935, 1424, 1527, 1721], {
      labels: ['transcript received', 'LLM started', 'first answer token', 'TTS started', 'bot speaking'],
      label: 'Latency (ms)',
      soft: 'rgba(59, 130, 246, 0.55)',
      thickness: 26,
      tooltip: { label: function (c) { return c.parsed.x + ' ms'; } }
    }));
    cfg.options.scales.y.ticks.color = p.text;
    return cfg;
  });

  /* ---------- ① Chinese ASR character error rate ---------- */
  make('chartAsr', function (p) {
    var cfg = baseBar(p, { yTitle: 'Character error rate (%)' });
    cfg = Object.assign(cfg, bar(p.c1, [23.8, 13.6, 10.2], {
      labels: ['Whisper base', 'Whisper small', 'SenseVoice (default)'],
      label: 'Character error rate (%)',
      soft: ['rgba(100, 116, 139, 0.45)', 'rgba(34, 197, 94, 0.35)', 'rgba(34, 197, 94, 0.62)'],
      tooltip: { label: function (c) { return c.parsed.y + '%'; } }
    }));
    return cfg;
  });

  /* ---------- ① Retrieval quality ---------- */
  make('chartRag', function (p) {
    var cfg = baseBar(p, { legend: true, yTitle: 'Percentage (%)' });
    cfg.data = {
      labels: ['hit@1', 'hit@3'],
      datasets: [
        {
          label: 'Old default (300/0, cosine only)',
          data: [33, 40],
          backgroundColor: 'rgba(148, 163, 184, 0.5)',
          borderColor: p.muted,
          borderWidth: 2,
          borderRadius: 8,
          maxBarThickness: 46
        },
        {
          label: 'Shipped config (500/50 + lexical rerank)',
          data: [53, 80],
          backgroundColor: 'rgba(59, 130, 246, 0.55)',
          borderColor: p.c2,
          borderWidth: 2,
          borderRadius: 8,
          maxBarThickness: 46
        }
      ]
    };
    cfg.options.scales.y.title = { display: true, text: 'Percentage (%)', color: p.muted };
    cfg.options.plugins.tooltip.callbacks.label = function (c) { return c.dataset.label + ': ' + c.parsed.y + '%'; };
    return cfg;
  });

  /* ---------- ② Platform round-trip ---------- */
  make('chartBrain', function (p) {
    var cfg = baseBar(p, { horizontal: true, xTitle: 'Latency (ms)' });
    cfg = Object.assign(cfg, bar(p.c3, [740, 963, 1334], {
      labels: [
        'platform first token (low)',
        'platform first token (high)',
        'voice layer: request -> platform first token'
      ],
      label: 'Latency (ms)',
      soft: ['rgba(168, 85, 247, 0.35)', 'rgba(168, 85, 247, 0.45)', 'rgba(168, 85, 247, 0.62)'],
      thickness: 30,
      tooltip: { label: function (c) { return c.parsed.x + ' ms'; } }
    }));
    cfg.options.scales.y.ticks.color = p.text;
    return cfg;
  });

  /* ---------- Re-style when the theme changes ---------- */
  function restyle() {
    var p = palette();
    registry.forEach(function (item) {
      var fresh = item.build(p);
      item.chart.data = fresh.data;
      item.chart.options = fresh.options;
      item.chart.update('none');
    });
  }

  document.addEventListener('overview:theme', function () {
    setTimeout(restyle, 60);
  });

  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    if (mq.addEventListener) mq.addEventListener('change', function () { setTimeout(restyle, 60); });
  }
})();
