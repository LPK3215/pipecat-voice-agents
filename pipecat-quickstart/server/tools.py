"""LLM 可调用的工具（function calling）。

为什么单独成模块：
    工具是后端能力的主要扩展点 —— 换模型、换传输、甚至换前端都不影响它们。
    把它们从 ``bot.py`` 的管线装配里挪出来，装配代码才不会被工具的实现细节淹没，
    新增能力时也只改这一个文件。

前端无需任何改动：
    pipecat 会把工具调用以 ``llm-function-call``/``llm-function-call-started``/
    ``llm-function-call-result`` 等消息推送给官方 Prebuilt 前端，
    调用过程与结果由前端自己渲染（该系列消息已在前端的 RTVI 消息表中）。

添加一个新工具的流程：
    1. 写一个 ``async def xxx(params: FunctionCallParams) -> None`` 处理函数，
       结束时调用 ``await params.result_callback(结果, properties=...)``。
    2. 定义对应的 ``FunctionSchema``，把 handler 指向它。
    3. 加进 ``build_tools()`` 的列表。
    ``FunctionSchema`` 带 handler 时，LLM 服务会自动注册，
    不需要再手工调用 ``llm.register_function``。

⚠️ 必须显式传 ``run_llm=True``（见 ``_RESULT_PROPS``）：
    ``FunctionCallResultProperties.run_llm`` 默认是 ``None``，而 None 是假值，
    不传就**不会触发工具结果之后的那一次 LLM 生成** —— 表现是工具正常执行、
    日志里有结果，但机器人永远不回答，直到超时。这个失败是**静默**的：
    没有报错、没有异常，只能靠「一直等不到回答」发现。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from loguru import logger
import memory
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import (
    FunctionCallParams,
    FunctionCallResultProperties,
)

# 每个工具的 result_callback 都要带上它，否则工具执行完不会触发下一轮 LLM 生成
# （详见本模块文档字符串里的说明）。
_RESULT_PROPS = FunctionCallResultProperties(run_llm=True)

# 语音助手默认按中国时区报时；容器里没有 tzdata 时会退到下面的固定偏移
DEFAULT_TZ = "Asia/Shanghai"
FALLBACK_OFFSET = timedelta(hours=8)

# weekday() 返回 0=周一，这里按中文习惯排列
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _resolve_tz(name: str) -> tuple[datetime, str]:
    """解析时区并取当前时间；时区不可用时退到 UTC+8。"""
    try:
        return datetime.now(ZoneInfo(name)), name
    except Exception:  # noqa: BLE001 - 缺 tzdata 或时区名不合法都应降级而非报错
        logger.warning(f"[TOOL] 时区 {name!r} 不可用，退化为 UTC+8")
        return datetime.now(timezone(FALLBACK_OFFSET)), "UTC+8"


async def get_current_time(params: FunctionCallParams) -> None:
    """回答「现在几点 / 今天几号 / 今天星期几」。

    结果里同时给出 ``spoken``（可直接朗读的中文）与 ``iso``（结构化值）：
    让模型直接照读 ``spoken``，可避免它把 ISO 时间串念成一串数字。
    """
    requested = (params.arguments or {}).get("timezone") or DEFAULT_TZ
    now, tz_name = _resolve_tz(requested)

    spoken = (
        f"现在是 {now.year}年{now.month}月{now.day}日 "
        f"{WEEKDAYS[now.weekday()]} {now.hour}点{now.minute:02d}分"
    )
    logger.info(f"[TOOL] get_current_time(timezone={tz_name}) -> {spoken}")

    await params.result_callback(
        {
            "timezone": tz_name,
            "spoken": spoken,
            "iso": now.isoformat(timespec="seconds"),
            "weekday": WEEKDAYS[now.weekday()],
        },
        properties=_RESULT_PROPS,
    )


CURRENT_TIME_SCHEMA = FunctionSchema(
    name="get_current_time",
    description=(
        "查询当前的日期和时间。"
        "当用户问「现在几点」「今天几号」「今天星期几」「现在是什么时候」时调用。"
    ),
    properties={
        "timezone": {
            "type": "string",
            "description": "IANA 时区名，例如 Asia/Shanghai。用户没有指定时区时留空。",
        }
    },
    required=[],
    handler=get_current_time,
)


# ---------------------------------------------------------------------------
# 记忆类工具：让「长期记忆」不只是存下来，而是模型能主动读写
#
# 为什么做成工具而不是无条件塞进上下文：
#     记忆会越攒越多，全塞进去会挤爆上下文窗口（且每轮都要付 token）。
#     做成工具后，模型**自己判断**什么时候该查、什么时候该记 ——
#     这也是 function calling 的意义：把「用不用」的决定权交给模型。
# ---------------------------------------------------------------------------


async def remember_fact(params: FunctionCallParams) -> None:
    """记住一条关于用户的事实（跨会话保留）。"""
    args = params.arguments or {}
    key = str(args.get("key", "")).strip()
    value = str(args.get("value", "")).strip()
    if not key or not value:
        await params.result_callback(
            {"ok": False, "error": "key 和 value 都不能为空"},
            properties=_RESULT_PROPS,
        )
        return

    memory.put_fact(key, value)
    logger.info(f"[TOOL] remember_fact({key}) -> {value}")
    await params.result_callback(
        {"ok": True, "key": key, "value": value, "spoken": f"记住了：{key}是{value}"},
        properties=_RESULT_PROPS,
    )


REMEMBER_SCHEMA = FunctionSchema(
    name="remember_fact",
    description=(
        "记住一条关于用户的信息，长期保留，之后的对话都记得。"
        "当用户说「记住…」「我叫…」「我喜欢…」「以后都…」"
        "或主动提供了自己的偏好、身份、习惯时调用。"
        "key 用简短的中文标签（如「姓名」「喜欢的语言」「项目名」），value 是具体内容。"
    ),
    properties={
        "key": {"type": "string", "description": "信息的名称，简短中文标签"},
        "value": {"type": "string", "description": "信息的具体内容"},
    },
    required=["key", "value"],
    handler=remember_fact,
)


async def recall_fact(params: FunctionCallParams) -> None:
    """从长期记忆里查；找不到就明确说没有，别让模型瞎编。"""
    query = str((params.arguments or {}).get("query", "")).strip()
    hits = memory.search_facts(query) if query else memory.list_facts(limit=10)

    logger.info(f"[TOOL] recall_fact({query}) -> {len(hits)} 条")
    await params.result_callback(
        {
            "found": bool(hits),
            "items": hits,
            "spoken": (
                "；".join(f"{h['key']}是{h['value']}" for h in hits)
                if hits
                else "没有相关的记录"
            ),
        },
        properties=_RESULT_PROPS,
    )


RECALL_SCHEMA = FunctionSchema(
    name="recall_fact",
    description=(
        "回想以前记住的关于用户的信息。"
        "当用户问「我说过什么」「你还记得吗」「我叫什么」"
        "或话题涉及过去约定过的偏好时调用。"
        "也可以用于查询用户之前交代过的项目背景、习惯等信息。"
    ),
    properties={
        "query": {
            "type": "string",
            "description": "要回想的主题关键词；留空则列出全部记忆",
        }
    },
    required=[],
    handler=recall_fact,
)


# ---------------------------------------------------------------------------
# 业务数据工具
#
# 这是「接自己的业务」的模板：工具本身只管查库，不管数据从哪来。
# 数据的来源（爬监控页面 / 调内部接口 / 定时任务）是**采集侧**的事，
# 换数据源时只改采集脚本，工具与提示词都不用动。
# ---------------------------------------------------------------------------


async def query_metrics(params: FunctionCallParams) -> None:
    """查业务指标（当前为示例数据，等接入真实采集后自动变成真实值）。"""
    args = params.arguments or {}
    name = args.get("name") or None
    limit = int(args.get("limit") or 5)

    rows = memory.query_metrics(name=name, limit=limit)
    logger.info(f"[TOOL] query_metrics(name={name}) -> {len(rows)} 条")

    if not rows:
        await params.result_callback(
            {"found": False, "spoken": "没有查到相关指标"},
            properties=_RESULT_PROPS,
        )
        return

    parts = []
    for r in rows:
        unit = r.get("unit") or ""
        parts.append(f"{r['name']} {r['value']}{unit}")
    await params.result_callback(
        {
            "found": True,
            "items": rows,
            # 同时给 spoken 字段：让模型直接照读，避免把数字念成一串
            "spoken": "，".join(parts),
        },
        properties=_RESULT_PROPS,
    )


METRICS_SCHEMA = FunctionSchema(
    name="query_metrics",
    description=(
        "查询系统监控指标，包括服务可用性、响应延迟、错误率、活跃用户数等。"
        "当用户问「现在系统怎么样」「可用性多少」「延迟高不高」"
        "「有多少人在线」「有没有报错」时调用。"
    ),
    properties={
        "name": {
            "type": "string",
            "description": "指标名称关键词，如「可用性」「延迟」「错误率」；留空查全部",
        },
        "limit": {"type": "integer", "description": "返回条数，默认 5"},
    },
    required=[],
    handler=query_metrics,
)


def build_tools() -> ToolsSchema:
    """返回本次会话开放给 LLM 的工具集合。"""
    return ToolsSchema(
        standard_tools=[
            CURRENT_TIME_SCHEMA,
            REMEMBER_SCHEMA,
            RECALL_SCHEMA,
            METRICS_SCHEMA,
        ]
    )
