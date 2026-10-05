#!/usr/bin/env node
/**
 * Purpose: generate the repository relationship diagram (SVG) — the positioning and call direction of
 *          the three projects, seen from the repository root.
 * Deps:    Node built-ins only (node:fs / node:path / node:url). No third-party packages.
 * Usage:   node scripts/visualization/generate_repo_map.mjs
 * Output:  docs/repo-map.svg
 *
 * Notes:
 * - All diagram text is English on purpose: technical and architecture terms are English, and Chinese
 *   glyphs inside SVG are not guaranteed to render on GitHub. Chinese explanation lives in the prose of
 *   README.zh-CN.md instead of a second translated image.
 * - Project versions are read from each project's own source of truth at run time, never hard-coded:
 *     pipecat-quickstart  -> pipecat-quickstart/server/pyproject.toml  ([project].version)
 *     voice-module-dify   -> voice-module-dify/pyproject.toml          ([project].version)
 *     pipecat-open-webui  -> pipecat-open-webui/package.json           (version, i.e. the upstream baseline)
 * - The generated SVG belongs to docs/; this script belongs to scripts/visualization/ (same convention
 *   as the individual projects).
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..', '..'); // -> repository root
const OUT_DIR = path.join(ROOT, 'docs'); // -> docs/
const OUT_FILE = path.join(OUT_DIR, 'repo-map.svg');

// --- Dynamic values: read from the source of truth at run time, never hard-coded ---
function pyprojectVersion(relPath) {
  try {
    const txt = fs.readFileSync(path.join(ROOT, relPath), 'utf8');
    const start = txt.indexOf('[project]');
    const region = start >= 0 ? txt.slice(start) : txt;
    const m = region.match(/^\s*version\s*=\s*"([^"]+)"/m);
    return m ? m[1] : 'unknown';
  } catch {
    console.warn(`[warn] cannot read version from ${relPath}, using unknown`);
    return 'unknown';
  }
}

function packageJsonVersion(relPath) {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, relPath), 'utf8'));
    return pkg.version || 'unknown';
  } catch {
    console.warn(`[warn] cannot read version from ${relPath}, using unknown`);
    return 'unknown';
  }
}

const vQuickstart = pyprojectVersion('pipecat-quickstart/server/pyproject.toml');
const vVoiceModule = pyprojectVersion('voice-module-dify/pyproject.toml');
const vOpenWebui = packageJsonVersion('pipecat-open-webui/package.json');

const esc = (s) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// --- The three projects: from the repository root they differ in exactly three things ---
const CARDS = [
  {
    x: 35,
    accent: '#16a34a',
    fill: '#f0fdf4',
    border: '#86efac',
    title: 'pipecat-quickstart',
    tag: 'Full stack',
    sub: `v${vQuickstart} · MIT`,
    rows: [
      ['In one line', 'Voice is the product itself'],
      ['Who thinks', 'Itself (LLM in the pipeline)'],
      ['Voice lives', 'Core (the pipeline)'],
      ['Direction', '- (self-contained)'],
    ],
    flow: 'VAD -> ASR -> LLM -> TTS',
  },
  {
    x: 360,
    accent: '#2563eb',
    fill: '#eff6ff',
    border: '#93c5fd',
    title: 'voice-module-dify',
    tag: 'Add-on',
    sub: `v${vVoiceModule} · MIT`,
    rows: [
      ['In one line', 'Voice as a part, it calls out'],
      ['Who thinks', 'External platform (Dify, ...)'],
      ['Voice lives', 'Standalone service'],
      ['Direction', 'me -> platform'],
    ],
    flow: 'web (yours) -> module -> platform (theirs)',
  },
  {
    x: 685,
    accent: '#7c3aed',
    fill: '#f5f3ff',
    border: '#c4b5fd',
    title: 'pipecat-open-webui',
    tag: 'Symbiont',
    sub: `v${vOpenWebui} baseline · dual license`,
    rows: [
      ['In one line', 'Voice as a part, called by host'],
      ['Who thinks', 'Host system (Open WebUI)'],
      ['Voice lives', 'A part inside the host'],
      ['Direction', 'host -> me'],
    ],
    flow: 'host (Open WebUI) -> voice part (me)',
  },
];

const CARD_W = 300;
const CARD_Y = 110;
const CARD_H = 300;

function cardSvg(c) {
  const cx = c.x + CARD_W / 2;
  let out = '';

  out += `  <rect x="${c.x}" y="${CARD_Y}" width="${CARD_W}" height="${CARD_H}" rx="14" fill="#ffffff" stroke="${c.border}" stroke-width="2.5"/>\n`;
  out += `  <rect x="${c.x}" y="${CARD_Y}" width="${CARD_W}" height="8" rx="4" fill="${c.accent}"/>\n`;

  out += `  <text x="${cx}" y="${CARD_Y + 44}" text-anchor="middle" font-size="16" font-weight="700" fill="${c.accent}" font-family="ui-monospace, Menlo, Consolas, monospace">${esc(c.title)}</text>\n`;
  out += `  <text x="${cx}" y="${CARD_Y + 70}" text-anchor="middle" font-size="12" fill="#64748b">${esc(c.sub)}</text>\n`;

  // Shape badge: Full stack / Add-on / Symbiont
  out += `  <rect x="${cx - 48}" y="${CARD_Y + 84}" width="96" height="24" rx="12" fill="${c.fill}" stroke="${c.border}"/>\n`;
  out += `  <text x="${cx}" y="${CARD_Y + 101}" text-anchor="middle" font-size="12" font-weight="600" fill="${c.accent}">${esc(c.tag)}</text>\n`;

  out += `  <line x1="${c.x + 24}" y1="${CARD_Y + 128}" x2="${c.x + CARD_W - 24}" y2="${CARD_Y + 128}" stroke="#e2e8f0" stroke-width="1"/>\n`;

  let ry = CARD_Y + 158;
  for (const [label, value] of c.rows) {
    out += `  <text x="${c.x + 24}" y="${ry}" font-size="11.5" fill="#94a3b8">${esc(label)}</text>\n`;
    out += `  <text x="${c.x + CARD_W - 24}" y="${ry}" text-anchor="end" font-size="12" fill="#1e293b">${esc(value)}</text>\n`;
    ry += 30;
  }

  out += `  <rect x="${c.x + 24}" y="${CARD_Y + 232}" width="${CARD_W - 48}" height="36" rx="8" fill="${c.fill}" stroke="${c.border}"/>\n`;
  out += `  <text x="${cx}" y="${CARD_Y + 255}" text-anchor="middle" font-size="11" font-weight="600" fill="${c.accent}" font-family="ui-monospace, Menlo, Consolas, monospace">${esc(c.flow)}</text>\n`;

  return out;
}

const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1020" height="600" viewBox="0 0 1020 600" font-family="Segoe UI, Helvetica, Arial, sans-serif">
  <rect width="1020" height="600" fill="#f8fafc"/>

  <text x="510" y="46" text-anchor="middle" font-size="21" font-weight="700" fill="#0f172a">pipecat-voice-agents - positioning and call direction of the three projects</text>
  <text x="510" y="72" text-anchor="middle" font-size="13" fill="#64748b">one repository · three projects · two opposite call directions · plus one framework survey</text>

${CARDS.map(cardSvg).join('\n')}

  <text x="510" y="466" text-anchor="middle" font-size="13" font-weight="600" fill="#334155">② and ③ point in opposite directions - that is why neither can replace the other, and why both exist.</text>
  <text x="510" y="500" text-anchor="middle" font-size="11" fill="#64748b">License: root MIT | pipecat-quickstart MIT | voice-module-dify MIT | pipecat-open-webui = Open WebUI License (branding kept) + MIT for new files</text>
  <text x="510" y="528" text-anchor="middle" font-size="11" fill="#64748b">A separate framework survey lives under research/ (a document, not a project)</text>
  <text x="510" y="566" text-anchor="middle" font-size="11" fill="#94a3b8">https://github.com/LPK3215/pipecat-voice-agents</text>
</svg>
`;

fs.mkdirSync(OUT_DIR, { recursive: true });
fs.writeFileSync(OUT_FILE, svg, 'utf8');
console.log(
  `[ok] wrote ${path.relative(ROOT, OUT_FILE)} ` +
    `(quickstart v${vQuickstart} / voice-module v${vVoiceModule} / open-webui v${vOpenWebui})`
);
