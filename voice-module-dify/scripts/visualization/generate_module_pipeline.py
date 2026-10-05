#!/usr/bin/env python3
"""Generate docs/module-pipeline.svg -- the voice module's internal pipeline.

Purpose
    Visualize what happens inside the module between the transport and the
    platform: input -> VAD -> STT -> turn detection -> [brain adapter] -> TTS ->
    output, plus the three responsibilities the adapter owns (ask only after the
    user stops, feed TTS sentence by sentence, filler / interrupt while waiting)
    and the out-of-band observability timeline.

Dependencies
    Python 3.12+ standard library only (tomllib). No third-party packages.

Run
    python3 scripts/visualization/generate_module_pipeline.py
    (paths are resolved from __file__, so the working directory does not matter)

Output
    docs/module-pipeline.svg   (relative to the project root)

Dynamic values
    The version shown in the subtitle is read at run time from pyproject.toml.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
OUT = ROOT / "docs" / "module-pipeline.svg"
FONT = "system-ui,-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"

# (component, role, colour)
STAGES = [
    ("传输入口", "WebSocket / WebRTC", "#3b82f6"),
    ("VAD", "说完判定", "#06b6d4"),
    ("STT", "本地 Whisper", "#06b6d4"),
    ("轮次判定", "用户说完没", "#64748b"),
    ("brain 适配器", "平台桥接", "#8b5cf6"),
    ("TTS", "本地 Piper", "#14b8a6"),
    ("传输输出", "回传音频", "#3b82f6"),
]

DUTIES = [
    ("说完才问", "平台只拿到文本，听不到音频"),
    ("边收边按句喂 TTS", "首句就开口，不等全文"),
    ("平台慢时填场 · 插话则停", "FILLER_DELAY_SECS / interrupt"),
]


def read_version() -> str:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def build_svg(version: str) -> str:
    w, h = 1200, 470
    bw, bh, gap = 140, 76, 22
    total = len(STAGES) * bw + (len(STAGES) - 1) * gap
    x0 = (w - total) / 2
    by = 130

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

    # header
    p.append(
        '<text x="60" y="48" font-size="24" font-weight="700" fill="#0f172a">'
        "模块内部管线 · What happens between transport and platform</text>"
    )
    p.append(
        f'<text x="60" y="74" font-size="13" fill="#64748b">'
        f"v{version} · 模块只负责听与说；标紫的「brain 适配器」是它与平台之间唯一的桥</text>"
    )

    # stages
    p.append(
        '<text x="60" y="112" font-size="13" font-weight="600" fill="#475569">'
        "Pipeline（顺序即职责链）</text>"
    )
    for i, (name, role, color) in enumerate(STAGES):
        x = x0 + i * (bw + gap)
        p.append(
            f'<g filter="url(#soft)">'
            f'<rect x="{x}" y="{by}" width="{bw}" height="{bh}" rx="12" fill="{color}"/>'
            f"</g>"
        )
        p.append(
            f'<text x="{x + bw / 2}" y="{by + 32}" text-anchor="middle" font-size="13" '
            f'font-weight="700" fill="#ffffff">{name}</text>'
        )
        p.append(
            f'<text x="{x + bw / 2}" y="{by + 54}" text-anchor="middle" font-size="10.5" '
            f'fill="#ffffff" opacity="0.9">{role}</text>'
        )
        if i < len(STAGES) - 1:
            cy = by + bh / 2
            p.append(
                f'<line x1="{x + bw + 2}" y1="{cy}" x2="{x + bw + gap - 2}" y2="{cy}" '
                f'stroke="#94a3b8" stroke-width="2" marker-end="url(#arrow)"/>'
            )

    # adapter duties
    p.append(
        '<text x="60" y="270" font-size="13" font-weight="600" fill="#475569">'
        "brain 适配器（server/brain.py）负责的三件事</text>"
    )
    dw = 340
    dgap = 20
    dx0 = (w - (3 * dw + 2 * dgap)) / 2
    for i, (title, desc) in enumerate(DUTIES):
        x = dx0 + i * (dw + dgap)
        p.append(
            f'<g filter="url(#soft)">'
            f'<rect x="{x}" y="288" width="{dw}" height="72" rx="12" fill="#ffffff"/></g>'
        )
        p.append(f'<rect x="{x}" y="288" width="6" height="72" rx="3" fill="#8b5cf6"/>')
        p.append(
            f'<text x="{x + 22}" y="318" font-size="13.5" font-weight="700" '
            f'fill="#8b5cf6">{title}</text>'
        )
        p.append(
            f'<text x="{x + 22}" y="342" font-size="11.5" fill="#475569">{desc}</text>'
        )

    # observability note
    p.append(
        '<text x="60" y="408" font-size="12" fill="#64748b">'
        "旁路观测：server/observability.py 记录分阶段时间线"
        "（听到 / 平台首字 / 首句 / 交给 TTS），不改变数据方向。"
        "</text>"
    )
    p.append(
        '<text x="60" y="438" font-size="11" fill="#94a3b8">'
        "Generated by scripts/visualization/generate_module_pipeline.py "
        "— re-run it after changing the pipeline."
        "</text>"
    )
    p.append("</svg>")
    return "".join(p)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_svg(read_version()), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
