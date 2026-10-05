#!/usr/bin/env node
/**
 * 用途：生成 pipecat-open-webui（Open WebUI 二次开发分支）的架构图 SVG
 * 依赖：仅 Node 内置模块（node:fs / node:path / node:url），无需第三方包
 * 运行：node scripts/visualization/generate_voice_architecture.mjs
 * 输出：docs/pipecat-open-webui-architecture.svg
 *
 * 说明：
 * - 图中"基线版本"等动态数值在运行时从 package.json 读取，不写死
 * - 产物 SVG 归 docs/，本脚本归 scripts/visualization/
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..');           // -> 项目根
const OUT_DIR = path.join(ROOT, 'docs');                    // -> docs/
const OUT_FILE = path.join(OUT_DIR, 'pipecat-open-webui-architecture.svg');

// --- 动态数值：从真源读取，不写死 ---
let upstreamVersion = 'unknown';
try {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
  upstreamVersion = pkg.version || upstreamVersion;
} catch (e) {
  console.warn('[warn] 无法读取 package.json 版本，使用 unknown');
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="960" height="470" viewBox="0 0 960 470" font-family="Segoe UI, Helvetica, Arial, sans-serif">
  <defs>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L8,3 L0,6 z" fill="#4b5563"/>
    </marker>
    <marker id="arrowDashed" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
      <path d="M0,0 L8,3 L0,6 z" fill="#9ca3af"/>
    </marker>
  </defs>

  <rect width="960" height="470" fill="#f8fafc"/>
  <text x="480" y="42" text-anchor="middle" font-size="22" font-weight="700" fill="#0f172a">pipecat-open-webui — Open WebUI 二次开发分支</text>
  <text x="480" y="68" text-anchor="middle" font-size="13" fill="#64748b">实时语音能力实验 · 基于 Open WebUI v${esc(upstreamVersion)} · 沿用 Open WebUI License</text>

  <!-- 浏览器 -->
  <rect x="40" y="150" width="200" height="120" rx="12" fill="#ffffff" stroke="#cbd5e1" stroke-width="2"/>
  <text x="140" y="195" text-anchor="middle" font-size="16" font-weight="600" fill="#0f172a">浏览器</text>
  <text x="140" y="220" text-anchor="middle" font-size="12" fill="#64748b">Svelte 前端产物</text>
  <text x="140" y="242" text-anchor="middle" font-size="12" fill="#64748b">(build/)</text>

  <!-- Open WebUI 后端 -->
  <rect x="330" y="120" width="300" height="180" rx="12" fill="#ffffff" stroke="#3b82f6" stroke-width="2.5"/>
  <text x="480" y="150" text-anchor="middle" font-size="16" font-weight="700" fill="#1d4ed8">Open WebUI 后端</text>
  <text x="480" y="172" text-anchor="middle" font-size="12" fill="#64748b">Python / FastAPI (backend/)</text>

  <rect x="352" y="190" width="256" height="40" rx="8" fill="#eff6ff" stroke="#93c5fd"/>
  <text x="480" y="215" text-anchor="middle" font-size="12.5" fill="#1e3a8a">扩展点：Functions / Tools / Pipelines</text>

  <rect x="352" y="240" width="256" height="44" rx="8" fill="#fefce8" stroke="#fde047" stroke-dasharray="6 4"/>
  <text x="480" y="260" text-anchor="middle" font-size="12.5" font-weight="600" fill="#854d0e">[ 语音模块 ] 计划中</text>
  <text x="480" y="277" text-anchor="middle" font-size="11" fill="#a16207">实时语音 · VAD · 打断 · 主动播报</text>

  <!-- LLM API -->
  <rect x="720" y="150" width="200" height="120" rx="12" fill="#ffffff" stroke="#cbd5e1" stroke-width="2"/>
  <text x="820" y="195" text-anchor="middle" font-size="16" font-weight="600" fill="#0f172a">大模型 API</text>
  <text x="820" y="220" text-anchor="middle" font-size="12" fill="#64748b">OpenAI 兼容</text>
  <text x="820" y="242" text-anchor="middle" font-size="12" fill="#64748b">(外部服务)</text>

  <!-- 箭头：浏览器 <-> 后端 -->
  <line x1="240" y1="195" x2="326" y2="195" stroke="#4b5563" stroke-width="2" marker-end="url(#arrow)"/>
  <line x1="330" y1="230" x2="244" y2="230" stroke="#4b5563" stroke-width="2" marker-end="url(#arrow)"/>
  <text x="283" y="185" text-anchor="middle" font-size="11" fill="#475569">HTTP / WS</text>

  <!-- 箭头：后端 <-> LLM -->
  <line x1="634" y1="195" x2="716" y2="195" stroke="#4b5563" stroke-width="2" marker-end="url(#arrow)"/>
  <line x1="720" y1="230" x2="638" y2="230" stroke="#4b5563" stroke-width="2" marker-end="url(#arrow)"/>
  <text x="677" y="185" text-anchor="middle" font-size="11" fill="#475569">API</text>

  <text x="480" y="360" text-anchor="middle" font-size="12" fill="#94a3b8">虚线框 = 本分支要新增的部分；其余为上游 Open WebUI 原样保留</text>
  <text x="480" y="392" text-anchor="middle" font-size="12.5" font-weight="600" fill="#334155">许可证：Open WebUI License（保留品牌） · 本分支仅新增 voice-docs/ 与 docs/</text>
  <text x="480" y="416" text-anchor="middle" font-size="11.5" fill="#94a3b8">https://github.com/open-webui/open-webui</text>
</svg>
`;

fs.mkdirSync(OUT_DIR, { recursive: true });
fs.writeFileSync(OUT_FILE, svg, 'utf8');
console.log(`[ok] 已生成 ${path.relative(ROOT, OUT_FILE)} (upstream v${upstreamVersion})`);
