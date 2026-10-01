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


def build_tools() -> ToolsSchema:
    """返回本次会话开放给 LLM 的工具集合。"""
    return ToolsSchema(standard_tools=[CURRENT_TIME_SCHEMA])
