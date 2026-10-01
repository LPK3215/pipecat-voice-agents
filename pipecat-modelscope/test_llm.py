"""魔搭 ModelScope 连通性 + 时延自测脚本（不依赖 STT/TTS，可单独运行）。

作用：
    1. 连通性：列出可用模型 + 实际发一次对话请求
    2. 及时性：测量首 token 延迟（TTFT）、总耗时、输出长度

用法：
    export MODELSCOPE_API_KEY=ms-xxxx
    uv run test_llm.py
    uv run test_llm.py --model ZhipuAI/GLM-4.7-Flash --runs 5
"""

import argparse
import os
import statistics
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)

DEFAULT_BASE_URL = "https://api-inference.modelscope.cn/v1"
# 已固定为实测最快的模型（首选）。如需对比，可用 --model 临时指定其他模型。
DEFAULT_MODEL = "nex-agi/Nex-N2.5-mini"
PROMPT = "请用一句话介绍一下杭州。"


def check_connectivity(client: OpenAI) -> int:
    """测试 /models 接口连通性，返回可用模型数量。"""
    start = time.perf_counter()
    ids = [m.id for m in client.models.list().data]
    cost_ms = (time.perf_counter() - start) * 1000
    print(f"[1] 连通性 OK | 可用模型 {len(ids)} 个 | /models 耗时 {cost_ms:.0f} ms")
    return len(ids)


def measure_once(client: OpenAI, model: str) -> tuple[float, float, str]:
    """跑一次流式对话，返回 (首token延迟, 总耗时, 文本)。"""
    start = time.perf_counter()
    ttft = None
    pieces: list[str] = []

    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": PROMPT}],
        stream=True,
        max_tokens=200,
    )

    for chunk in stream:
        if not chunk.choices:
            continue
        piece = getattr(chunk.choices[0].delta, "content", None)
        if piece:
            if ttft is None:
                ttft = time.perf_counter() - start
            pieces.append(piece)

    total = time.perf_counter() - start
    return (ttft or total), total, "".join(pieces)


def main() -> None:
    parser = argparse.ArgumentParser(description="ModelScope 连通性与时延测试")
    # 注意：base_url 与 model 已写死，不读环境变量，避免被外部残留值覆盖
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default=os.getenv("MODELSCOPE_API_KEY"))
    parser.add_argument("--runs", type=int, default=3, help="时延测试次数")
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("请先设置环境变量 MODELSCOPE_API_KEY")

    client = OpenAI(base_url=args.base_url, api_key=args.api_key)

    print("=" * 64)
    print(f"base_url : {args.base_url}")
    print(f"model    : {args.model}")
    print("=" * 64)

    try:
        check_connectivity(client)
    except Exception as exc:  # noqa: BLE001
        print(f"[1] 连通性失败: {exc}")
        raise SystemExit(1)

    print(f"[2] 时延测试（{args.runs} 次流式对话）...")
    ttfts: list[float] = []
    totals: list[float] = []

    for i in range(args.runs):
        try:
            ttft, total, text = measure_once(client, args.model)
        except Exception as exc:  # noqa: BLE001
            print(f"    run{i + 1}: 失败 -> {exc}")
            continue
        ttfts.append(ttft)
        totals.append(total)
        preview = text[:36].replace("\n", " ")
        print(
            f"    run{i + 1}: 首token {ttft * 1000:6.0f} ms | "
            f"总耗时 {total * 1000:6.0f} ms | 输出 {len(text):3d} 字 | {preview}..."
        )

    if not ttfts:
        raise SystemExit("所有测试均失败")

    print("-" * 64)
    print(
        f"[3] 汇总 | 首token 平均 {statistics.mean(ttfts) * 1000:.0f} ms "
        f"(最快 {min(ttfts) * 1000:.0f} ms / 最慢 {max(ttfts) * 1000:.0f} ms)"
    )
    print(f"         | 总耗时 平均 {statistics.mean(totals) * 1000:.0f} ms")
    print("-" * 64)
    print("[4] 说明：以上只是「LLM 单环节」延迟。")
    print("    语音端到端延迟 ≈ STT + LLM(首token) + TTS(首音频) + 网络，通常再加 300~600 ms。")


if __name__ == "__main__":
    main()
