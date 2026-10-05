"""Tools the LLM can call (function calling).

Why a separate module:
    Tools are the main extension point for backend capability -- they survive changing
    the model, the transport, or even the frontend. Moving them out of the pipeline
    assembly in ``bot.py`` keeps that assembly readable and makes new capabilities a
    single-file change.

No frontend changes required:
    pipecat pushes tool calls to the official prebuilt frontend as
    ``llm-function-call`` / ``llm-function-call-started`` / ``llm-function-call-result``
    messages, and the frontend renders the process and results itself.

Adding a new tool:
    1. Write an ``async def xxx(params: FunctionCallParams) -> None`` handler that ends
       with ``await params.result_callback(result, properties=...)``.
    2. Define the matching ``FunctionSchema`` and point its handler at it.
    3. Add it to the list in ``build_tools()``.
    4. Stateful tools only: bind the state to the session. ``build_tools(session_id)``
       takes the id for exactly this reason -- state created once at import time is
       shared by every conversation (see ``sample_tools.reminder_schema``).
    When a ``FunctionSchema`` carries a handler, the LLM service registers it
    automatically -- no manual ``llm.register_function``.

NOTE: ``run_llm=True`` must be passed explicitly (see ``_RESULT_PROPS``):
    ``FunctionCallResultProperties.run_llm`` defaults to ``None``, and None is falsy, so
    omitting it means **no LLM generation after the tool result** -- the tool runs, the
    log shows a result, but the bot never answers until it times out. The failure is
    **silent**: no error, no exception, only "no answer ever arrives".

NOTE: handlers are wrapped by ``safe_handler`` in ``build_tools()``:
    A handler that raises is the other half of the same trap -- the tool "ran", no result
    ever reaches the model, and the bot never answers (measured: ``query_data`` with
    ``limit="十条"``). The wrapper logs the traceback and reports ``ok=False`` back so the
    bot can answer honestly. Do not rely on it for **expected** failures (bad arguments,
    nothing found): report those yourself with ``ok=False`` and a ``spoken`` line, and
    keep the wrapper for the unexpected.

NOTE: the ``description=`` fields and ``spoken`` values below are intentionally Chinese:
they are prompts for a Chinese-speaking agent and are read aloud to the user. Do not
translate them.
"""

from __future__ import annotations

import functools
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import (
    FunctionCallParams,
    FunctionCallResultProperties,
)

import memory
import sample_tools

# Every tool's result_callback needs this, otherwise the tool completing does not trigger
# the next LLM generation (see the module docstring).
_RESULT_PROPS = FunctionCallResultProperties(run_llm=True)

# The voice assistant reports time in China's timezone by default; containers without
# tzdata fall back to the fixed offset below.
DEFAULT_TZ = "Asia/Shanghai"
FALLBACK_OFFSET = timedelta(hours=8)

# weekday() returns 0=Monday; ordered here the Chinese way.
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _resolve_tz(name: str) -> tuple[datetime, str]:
    """Resolve a timezone and get the current time; fall back to UTC+8 if unavailable."""
    try:
        return datetime.now(ZoneInfo(name)), name
    except Exception:  # noqa: BLE001 - missing tzdata or a bad name should degrade, not raise
        logger.warning(f"[TOOL] timezone {name!r} unavailable; falling back to UTC+8")
        return datetime.now(timezone(FALLBACK_OFFSET)), "UTC+8"


async def get_current_time(params: FunctionCallParams) -> None:
    """Answer "what time is it / what is today's date / what weekday is it".

    The result includes both ``spoken`` (ready-to-read Chinese) and ``iso`` (a structured
    value): letting the model read ``spoken`` directly avoids it reading an ISO string as
    a stream of digits.
    """
    requested = (params.arguments or {}).get("timezone") or DEFAULT_TZ
    now, tz_name = _resolve_tz(requested)

    spoken = (
        f"现在是 {now.year}年{now.month}月{now.day}日 "
        f"{WEEKDAYS[now.weekday()]} {now.hour}点{now.minute:02d}分"
    )

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
# Memory tools: long-term memory is not just stored, the model can read and write it.
#
# Why expose it as a tool instead of always injecting it into the context:
#     Memory grows without bound, and injecting all of it would blow the context window
#     (and cost tokens every turn). As a tool, the model **decides** when to look
#     something up and when to store something -- which is the point of function calling:
#     leaving the "use it or not" decision to the model.
# ---------------------------------------------------------------------------


async def remember_fact(params: FunctionCallParams) -> None:
    """Remember one fact about the user (persisted across sessions)."""
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
    """Look something up in long-term memory; say clearly when it is missing, do not guess."""
    query = str((params.arguments or {}).get("query", "")).strip()
    hits = memory.search_facts(query) if query else memory.list_facts(limit=10)

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
# Business data tool (structured knowledge base)
#
# This is the template for "wiring in your own business": the tool only queries the DB;
# where the data comes from is not its concern. Data sources (scraping a monitoring page,
# calling an internal API, a scheduled job) are the **ingestion side**; changing the
# source means changing only the ingestion script, not the tool or the prompts.
#
# Why **one generic query** instead of one tool per table:
#     A real business has dozens of tables; one tool per table would blow up the prompt
#     and force the model to guess which one to use. A single "table + filters + order +
#     aggregate" entry point multiplies the combinations while keeping the tool count
#     constant.
#
# Why not just let the model write SQL:
#     That hands read/write access to the whole database to a model that can be steered.
#     Only whitelisted tables and columns are allowed here; nothing outside the whitelist
#     is reachable.
# ---------------------------------------------------------------------------


async def query_data(params: FunctionCallParams) -> None:
    """Generic structured query (currently demo data; becomes real once ingestion is wired in)."""
    args = params.arguments or {}

    # Some models pass filters as a JSON string rather than an object; accept both.
    filters = args.get("filters") or {}
    if isinstance(filters, str):
        try:
            filters = json.loads(filters) if filters.strip() else {}
        except json.JSONDecodeError:
            filters = {}

    # Numbers may arrive as strings. query_table clamps limit to 1..50, but int() would
    # raise first -- and a raising handler is a silent hang for the caller (see safe_handler).
    try:
        limit = int(args.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10

    result = memory.query_table(
        table=str(args.get("table", "")).strip(),
        filters=filters if isinstance(filters, dict) else {},
        search=str(args.get("search") or ""),
        order_by=str(args.get("order_by") or ""),
        desc=bool(args.get("desc", True)),
        limit=limit,
        aggregate=str(args.get("aggregate") or ""),
    )

    if "error" in result:
        result["spoken"] = f"查询没成功：{result['error']}"
    elif "result" in result:
        result["spoken"] = f"结果是 {result['result']}"
    elif not result.get("rows"):
        result["found"] = False
        result["spoken"] = "没有查到符合条件的记录"
    else:
        result["found"] = True
        # Also provide spoken so the model reads it out instead of reading digits one by one.
        cells = []
        for r in result["rows"]:
            cells.append("，".join(f"{k}是{v}" for k, v in r.items() if k != "time"))
        result["spoken"] = "；".join(cells)

    await params.result_callback(result, properties=_RESULT_PROPS)


DATA_SCHEMA = FunctionSchema(
    name="query_data",
    description=(
        "查询业务数据库（结构化数据，精确查询）。"
        "当用户问系统状态、监控指标、服务器、主机、告警相关的问题时调用。\n"
        f"可用表与列：{memory.schema_summary()}。\n"
        "用法示例：\n"
        "  「可用性/延迟/错误率多少」→ table=metrics\n"
        "  「哪台服务器延迟最高」→ table=hosts, order_by=latency_ms, limit=1\n"
        "  「有几条未处理告警」→ table=alerts, filters={status:firing}, aggregate=count\n"
        "  「web-02 有什么告警」→ table=alerts, filters={host:web-02}\n"
        "注意：这是精确条件查询，不是文档检索。不要用它查文档内容。"
    ),
    properties={
        "table": {
            "type": "string",
            "enum": list(memory.TABLE_COLUMNS),
            "description": "要查的表",
        },
        "filters": {
            "type": "object",
            "description": "精确匹配条件，如 {status: firing}、{host: web-02}",
        },
        "search": {"type": "string", "description": "对名称/告警内容做模糊匹配"},
        "order_by": {"type": "string", "description": "排序列名"},
        "desc": {"type": "boolean", "description": "是否降序，默认 true"},
        "limit": {"type": "integer", "description": "返回条数，默认 10，最多 50"},
        "aggregate": {
            "type": "string",
            "description": "聚合：count 或 avg:列名 / max:列名 / min:列名 / sum:列名",
        },
    },
    required=["table"],
    handler=query_data,
)


# ---------------------------------------------------------------------------
# Knowledge base retrieval (RAG, semantic search over unstructured documents)
#
# Division of labor with query_data: this does "fuzzy semantic" document search, while
# query_data does "exact condition" table queries. Both descriptions state the boundary
# explicitly, because overlapping descriptions are the main cause of wrong tool choice.
# ---------------------------------------------------------------------------


async def search_knowledge(params: FunctionCallParams) -> None:
    """Semantic search over the local knowledge base, for questions that need documents."""
    args = params.arguments or {}
    query = str(args.get("query", "")).strip()
    if not query:
        await params.result_callback(
            {"found": False, "spoken": "请告诉我要查什么"}, properties=_RESULT_PROPS
        )
        return

    try:
        k = int(args.get("k") or 3)
    except (TypeError, ValueError):
        k = 3

    import knowledge  # lazy import: do not load the embedding model unless KB is queried

    hits = knowledge.search(query, k=k)
    if not hits:
        await params.result_callback(
            {"found": False, "query": query, "spoken": "知识库里没有找到相关内容"},
            properties=_RESULT_PROPS,
        )
        return

    # spoken: truncate chunks so the model reads a short excerpt, not a whole document.
    await params.result_callback(
        {
            "found": True,
            "query": query,
            "items": hits,
            "spoken": "；".join(h["text"][:80] for h in hits),
        },
        properties=_RESULT_PROPS,
    )


KNOWLEDGE_SCHEMA = FunctionSchema(
    name="search_knowledge",
    description=(
        "在本地知识库（文档资料）中做**语义检索**，回答需要依据文档的问题。"
        "当用户问及产品手册、操作步骤、政策条款、常见问题等**文档内容**时调用。\n"
        "这是模糊语义检索，不是精确条件查询；"
        "要查结构化数据（订单、指标、告警、主机）请用 query_data。"
    ),
    properties={
        "query": {"type": "string", "description": "要检索的问题或关键词"},
        "k": {"type": "integer", "description": "返回条数，默认 3，最多 10"},
    },
    required=["query"],
    handler=search_knowledge,
)


# ---------------------------------------------------------------------------
# Explicit orchestration: composite tools (multi-step tasks are ordered by code, not by
# the model improvising).
#
# Fixes the problem measured in HANDBOOK-02 sections 4/5: asked "what is the weather
# here", the model **skips** the "recall the city" step and invents "Beijing" -- the call
# is fine, the format is fine, only the content is wrong. Here it becomes a deterministic
# two-step chain and the model calls a single tool.
# ---------------------------------------------------------------------------


async def my_local_weather(params: FunctionCallParams) -> None:
    """Composite tool: recall the user's city, then look up that city's weather (ordered by code)."""
    import sample_tools
    from flows import first_value, run_steps

    def city_from(prev: list[dict]) -> dict:
        items = prev[0].get("items") or []
        city = first_value(items, "value") if items else ""
        return {"city": str(city or "")}

    results = await run_steps(
        [
            (recall_fact, {"query": "城市"}),
            (sample_tools.get_weather, city_from),
        ]
    )
    weather = results[-1]
    if not weather.get("found"):
        await params.result_callback(
            {
                "found": False,
                "spoken": "我还不知道你在哪个城市，先告诉我你在哪，我就能报天气了",
                "steps": results,
            },
            properties=_RESULT_PROPS,
        )
        return
    await params.result_callback(
        {
            "found": True,
            "city": weather.get("city"),
            "spoken": weather.get("spoken", ""),
            "steps": results,
        },
        properties=_RESULT_PROPS,
    )


LOCAL_WEATHER_SCHEMA = FunctionSchema(
    name="my_local_weather",
    description=(
        "查询**用户所在地**的天气。"
        "当用户说「我这边天气怎么样」「我这冷不冷」等**不带具体城市**的天气问题时调用。"
        "本工具会先回想用户记住的城市再查天气，顺序由系统保证；"
        "用户明确说了城市名时才用 get_weather。"
    ),
    properties={},
    required=[],
    handler=my_local_weather,
)


def safe_handler(handler):
    """Wrap a tool handler so a failure is **reported**, never silent.

    Measured defect: ``query_data`` with ``limit="十条"`` raised ``ValueError`` inside the
    handler. Nothing catches that, so the tool "ran" while no result ever reached the
    model -- the bot simply never answers. Same silent-failure class as the missing
    ``run_llm=True`` described above, which is why ``build_tools()`` wraps **every**
    handler instead of trusting each author to remember a try/except.
    """

    @functools.wraps(handler)
    async def wrapper(params: FunctionCallParams) -> None:
        try:
            await handler(params)
        except Exception as exc:  # noqa: BLE001 - a tool must never kill the turn
            logger.exception(f"[TOOLS] handler '{handler.__name__}' raised {type(exc).__name__}")
            await params.result_callback(
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "spoken": "抱歉，这个操作出错了，请换个说法再试一次",
                },
                properties=_RESULT_PROPS,
            )

    return wrapper


def _with_safe_handlers(schemas: list[FunctionSchema]) -> list[FunctionSchema]:
    """Rebuild each schema with its handler wrapped (FunctionSchema has no copy helper)."""
    return [
        FunctionSchema(
            name=s.name,
            description=s.description,
            properties=s.properties,
            required=s.required,
            handler=safe_handler(s.handler) if s.handler else None,
        )
        for s in schemas
    ]


def build_tools(session_id: str = "default") -> ToolsSchema:
    """Return the set of tools exposed to the LLM for this session.

    ``session_id`` is handed to the one stateful sample tool (``set_reminder``), so its
    store stays per conversation. bot.py builds the tools inside ``run_bot`` for this
    reason -- building them once at import time would make that state process-global.

    As tools multiply, the **boundaries between their descriptions** matter more than the
    tools themselves: overlapping descriptions (say, "time" and "schedule" both vague)
    make the model choose wrong. When adding a tool, first decide "when should it NOT be
    called" and write that boundary into the description.

    ``TOOLS_EXCLUDE`` (comma-separated tool names) temporarily removes tools.
    This is not a debugging toy: measured, as the tool count grows the model "forgets"
    some tools or even **claims it ran them**; loading groups on demand is a real
    degradation lever.
    """
    schemas = [
        # system capabilities
        CURRENT_TIME_SCHEMA,
        # memory
        REMEMBER_SCHEMA,
        RECALL_SCHEMA,
        # structured data
        DATA_SCHEMA,
        # unstructured documents (knowledge base / RAG)
        KNOWLEDGE_SCHEMA,
        # explicitly orchestrated composite tool (multi-step, ordered by code)
        LOCAL_WEATHER_SCHEMA,
        # a set of assorted sample tools (see sample_tools.py)
        *sample_tools.sample_schemas(session_id),
    ]

    excluded = {s.strip() for s in os.getenv("TOOLS_EXCLUDE", "").split(",") if s.strip()}
    if excluded:
        kept = [s for s in schemas if s.name not in excluded]
        logger.info(
            f"[TOOLS] TOOLS_EXCLUDE removed {len(schemas) - len(kept)} tools; "
            f"{len(kept)} remain: {[s.name for s in kept]}"
        )
        schemas = kept

    return ToolsSchema(standard_tools=_with_safe_handlers(schemas))
