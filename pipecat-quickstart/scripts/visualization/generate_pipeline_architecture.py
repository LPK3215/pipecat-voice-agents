#!/usr/bin/env python3
"""Generate docs/pipeline-architecture.svg -- the cascade voice-pipeline diagram.

Purpose
    Visualize the runtime pipeline that server/bot.py assembles for each session
    (transport.input() -> STT -> user aggregator -> LLM -> TTS -> transport.output()
    -> assistant aggregator), together with the observers attached to the worker
    and the capabilities the LLM can call.

Dependencies
    Python 3.11+ standard library only (tomllib). No third-party packages.

Run
    python3 scripts/visualization/generate_pipeline_architecture.py
    (paths are resolved from __file__, so the working directory does not matter)

Output
    docs/pipeline-architecture.svg   (relative to the project root)

Dynamic values
    The version shown in the subtitle is read at run time from server/pyproject.toml
    -- it is never hard-coded here.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "server" / "pyproject.toml"
OUT = ROOT / "docs" / "pipeline-architecture.svg"

FONT = "system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"

# (component, role shown under it, fill colour)
PIPELINE = [
    ("transport.input()", "接收用户音频", "#3b82f6"),
    ("STT", "语音识别 · 本地", "#06b6d4"),
    ("user_aggregator", "用户上下文聚合", "#64748b"),
    ("LLM", "大模型 · OpenAI 兼容", "#8b5cf6"),
    ("TTS", "语音合成 · 本地", "#14b8a6"),
    ("transport.output()", "回传音频", "#3b82f6"),
    ("assistant_aggregator", "助手上下文回填", "#64748b"),
]

OBSERVERS = [
    "ConversationLogger",
    "MetricsLogObserver",
    "ErrorObserver",
    "FunctionCallObserver",
    "TurnRecorder",
    "HallucinationGuard",
]

CAPABILITIES = [
    ("Tools", "12 个工具 · function calling", "#8b5cf6"),
    ("Memory", "短期会话 + 长期记忆", "#0ea5e9"),
    ("Knowledge", "知识库 / RAG 检索", "#14b8a6"),
    ("Data", "SQLite 业务表查询", "#f59e0b"),
]


def read_version() -> str:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def build_svg(version: str) -> str:
    w, h = 1280, 570
    bw, bh, gap = 152, 72, 20
    x0, by = 48, 150

    p: list[str] = []
    p.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="{FONT}">'
    )
    p.append(
        "<defs>"
        '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#f8fafc"/><stop offset="1" stop-color="#eef2f7"/>'
        "</linearGradient>"
        '<marker id="arrow" markerWidth="10" markerHeight="10" refX="7" refY="3" orient="auto">'
        '<path d="M0,0 L7,3 L0,6 Z" fill="#94a3b8"/></marker>'
        '<filter id="soft" x="-20%" y="-20%" width="140%" height="140%">'
        '<feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#0f172a" flood-opacity="0.10"/>'
        "</filter>"
        "</defs>"
    )
    p.append(f'<rect width="{w}" height="{h}" fill="url(#bg)"/>')

    # ---- header ----
    p.append(
        '<text x="48" y="46" font-size="24" font-weight="700" fill="#0f172a">'
        "级联语音管线 · Cascade Voice Pipeline</text>"
    )
    p.append(
        f'<text x="48" y="72" font-size="13" fill="#64748b">'
        f"v{version} · 由 server/bot.py 每会话装配 · 本地 STT/TTS + 单个 LLM 密钥</text>"
    )

    # ---- pipeline row ----
    p.append(
        '<text x="48" y="128" font-size="13" font-weight="600" fill="#475569">'
        "Pipeline（顺序即职责链）</text>"
    )
    for i, (name, role, color) in enumerate(PIPELINE):
        x = x0 + i * (bw + gap)
        p.append(
            f'<g filter="url(#soft)">'
            f'<rect x="{x}" y="{by}" width="{bw}" height="{bh}" rx="12" fill="{color}"/>'
            f"</g>"
        )
        p.append(
            f'<text x="{x + bw / 2}" y="{by + 30}" text-anchor="middle" font-size="13.5" '
            f'font-weight="700" fill="#ffffff">{name}</text>'
        )
        p.append(
            f'<text x="{x + bw / 2}" y="{by + 52}" text-anchor="middle" font-size="10.5" '
            f'fill="#ffffff" opacity="0.9">{role}</text>'
        )
        if i < len(PIPELINE) - 1:
            cy = by + bh / 2
            p.append(
                f'<line x1="{x + bw + 2}" y1="{cy}" x2="{x + bw + gap - 2}" y2="{cy}" '
                f'stroke="#94a3b8" stroke-width="2" marker-end="url(#arrow)"/>'
            )

    # ---- observers row ----
    oy = 300
    p.append(
        f'<text x="48" y="{oy - 18}" font-size="13" font-weight="600" fill="#475569">'
        "Observers（挂载于 PipelineWorker；位置无关，只看每帧首次推送）</text>"
    )
    n, pw, pgap = len(OBSERVERS), 176, 20
    px0 = (w - (n * pw + (n - 1) * pgap)) / 2
    for i, name in enumerate(OBSERVERS):
        x = px0 + i * (pw + pgap)
        p.append(
            f'<rect x="{x}" y="{oy}" width="{pw}" height="36" rx="18" '
            f'fill="#ffffff" stroke="#cbd5e1"/>'
        )
        p.append(
            f'<text x="{x + pw / 2}" y="{oy + 23}" text-anchor="middle" font-size="11.5" '
            f'fill="#334155">{name}</text>'
        )

    # ---- capabilities row ----
    cy2 = 430
    p.append(
        f'<text x="48" y="{cy2 - 18}" font-size="13" font-weight="600" fill="#475569">'
        "LLM 侧可调用能力（工具 / 记忆 / 知识库 / 业务数据）</text>"
    )
    m, cw, cgap = len(CAPABILITIES), 272, 24
    cx0 = (w - (m * cw + (m - 1) * cgap)) / 2
    for i, (name, desc, color) in enumerate(CAPABILITIES):
        x = cx0 + i * (cw + cgap)
        p.append(
            f'<g filter="url(#soft)">'
            f'<rect x="{x}" y="{cy2}" width="{cw}" height="66" rx="12" fill="#ffffff"/>'
            f"</g>"
        )
        p.append(f'<rect x="{x}" y="{cy2}" width="6" height="66" rx="3" fill="{color}"/>')
        p.append(
            f'<text x="{x + 22}" y="{cy2 + 28}" font-size="14" font-weight="700" '
            f'fill="{color}">{name}</text>'
        )
        p.append(
            f'<text x="{x + 22}" y="{cy2 + 50}" font-size="11.5" fill="#475569">{desc}</text>'
        )

    # ---- footer ----
    p.append(
        '<text x="48" y="548" font-size="11" fill="#94a3b8">'
        "Generated by scripts/visualization/generate_pipeline_architecture.py "
        "— re-run it after changing the pipeline.</text>"
    )
    p.append("</svg>")
    return "".join(p)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_svg(read_version()), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
