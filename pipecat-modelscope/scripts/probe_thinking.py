"""定位延迟来源：模型「思考」到底占了多少，能不能关掉。

对比 4 种请求方式，payload 完全复刻 bot.py / test_e2e.py 的管线请求
（同样的 system instruction + 同样的用户输入），只改「是否关闭思考」。

输出每组的：首帧 / 首个思考 token / 首个答案 token / 总耗时。

用法：
    uv run scripts/probe_thinking.py
"""

import os
import time

from dotenv import load_dotenv
from loguru import logger
from openai import OpenAI

load_dotenv(override=True)

MODEL = os.getenv("MODELSCOPE_MODEL", "nex-agi/Nex-N2.5-mini")
BASE_URL = os.getenv("MODELSCOPE_BASE_URL", "https://api-inference.modelscope.cn/v1")

SYSTEM = (
    "你是一个语音助手。你的回答会被朗读出来，所以请口语化、简短，"
    "不要使用 emoji、markdown、列表等无法朗读的格式。"
)
USER = "你好請用一句話介紹一下,你自己。"

VARIANTS: list[tuple[str, dict]] = [
    ("baseline（不干预）", {}),
    ("enable_thinking=False", {"extra_body": {"enable_thinking": False}}),
    ("thinking=disabled", {"extra_body": {"thinking": {"type": "disabled"}}}),
]

RUNS = 3


def ms(v: float | None) -> str:
    return f"{v * 1000:7.0f} ms" if v is not None else "    N/A"


def probe(client: OpenAI, label: str, extra: dict) -> dict | None:
    t0 = time.perf_counter()
    first_frame = first_think = first_answer = None
    answer: list[str] = []
    thinking_chars = 0

    try:
        stream = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": USER},
            ],
            stream=True,
            max_tokens=300,
            **extra,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            now = time.perf_counter() - t0
            if first_frame is None:
                first_frame = now
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                if first_think is None:
                    first_think = now
                thinking_chars += len(reasoning)
            if delta.content:
                if first_answer is None:
                    first_answer = now
                answer.append(delta.content)
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label}] 失败: {type(exc).__name__}: {str(exc)[:150]}")
        return None

    total = time.perf_counter() - t0
    text = "".join(answer)
    print(f"  [{label}]")
    print(
        f"      首帧 {ms(first_frame)} | 首思考 {ms(first_think)} | "
        f"首答案 {ms(first_answer)} | 总 {ms(total)}"
    )
    print(f"      思考字符 {thinking_chars:4d} | 答案 {len(text):3d} 字 | {text[:50]!r}")
    return {"first_answer": first_answer or total, "thinking_chars": thinking_chars}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()
    globals()["MODEL"] = a.model

    client = OpenAI(base_url=BASE_URL, api_key=os.getenv("MODELSCOPE_API_KEY"))
    print("=" * 78)
    print(f"思考模式探测 | model = {MODEL}")
    print(f"payload 复刻管线：system({len(SYSTEM)} 字) + user({len(USER)} 字)")
    print("=" * 78)

    summary: dict[str, list[float]] = {}
    for label, extra in VARIANTS:
        vals = []
        for i in range(RUNS):
            r = probe(client, f"{label}  run{i + 1}", extra)
            if r:
                vals.append(r["first_answer"])
        if vals:
            summary[label] = vals

    print()
    print("=" * 78)
    print("汇总：首个答案 token（这才是用户等到的时刻）")
    print("=" * 78)
    for label, vals in summary.items():
        avg = sum(vals) / len(vals)
        print(f"  {label:<26} 平均 {avg * 1000:7.0f} ms   各次 {[round(v * 1000) for v in vals]}")


if __name__ == "__main__":
    main()
