"""Sample tool set: a batch of assorted low-cost tools for stress-testing the model's
"flexible calling" behavior.

Why a separate module:
    ``tools.py`` is the **registry** that hands tools to the framework; concrete tool
    implementations live in per-topic modules, so adding a batch of capabilities does not
    require touching the assembly code.

Why everything is zero-cost to implement:
    What is being tested is "does the model pick the right tool and pass good arguments" --
    **independent of whether the tool talks to a real API or local fake data**. Local data
    isolates the variables: a wrong call is then definitely the model's fault, not network
    flakiness. When wiring in a real business, only the function bodies change; the schemas
    and descriptions barely move.

The tool types are deliberately **diverse**, because different "shapes" fail differently:
    no-arg query (time) / single-arg query (weather) / computation (expression) /
    conversion (two units) / write (toggle a device) / side-effecting action (send a notice)

NOTE: the ``description`` and ``spoken`` strings below are intentionally Chinese -- they
are prompts and read-aloud text for a Chinese-speaking agent. Data keys (cities, devices,
units) are functional too. Do not translate them.
"""

from __future__ import annotations

import ast
import json
import operator
import os
import time
from pathlib import Path

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import (
    FunctionCallParams,
    FunctionCallResultProperties,
)

# Same meaning as the constant in tools.py: without run_llm=True there is no LLM
# generation after the tool result -- the tool runs but the bot never answers, silently.
# Duplicated here to avoid a circular import with tools.py.
_RESULT_PROPS = FunctionCallResultProperties(run_llm=True)


# ---------------------------------------------------------------- demo data (from a file)
#
# The demo content -- which cities exist, what their weather is, which devices exist -- lives in
# ``sample-data/sample-tools.json``, not in this module. Data belongs to the data layer, so
# replacing it is a file change rather than a code change; a real deployment swaps these tools
# for ones that query an actual service. ``SAMPLE_TOOLS_DATA`` overrides the path.
DEMO_DATA_FILE = Path(
    os.getenv(
        "SAMPLE_TOOLS_DATA",
        str(Path(__file__).resolve().parent.parent / "sample-data" / "sample-tools.json"),
    )
)

_DATA: dict | None = None


def load_demo_data(force: bool = False) -> dict:
    """Return the demo tool data (cached). A missing file yields empty data, never a crash."""
    global _DATA
    if _DATA is None or force:
        try:
            _DATA = json.loads(DEMO_DATA_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.warning(f"[SAMPLE] demo data file not found: {DEMO_DATA_FILE}")
            _DATA = {}
    return _DATA


def _weather_table() -> dict:
    return load_demo_data().get("weather", {})


# ---------------------------------------------------------------- weather (data from the file)


async def get_weather(params: FunctionCallParams) -> None:
    """Look up weather. Data is hard-coded locally; it only validates the call path."""
    city = str((params.arguments or {}).get("city", "")).strip()
    key = city.rstrip("市")
    weather = _weather_table()

    if key not in weather:
        await params.result_callback(
            {
                "found": False,
                "spoken": f"我还没法查{key or '这个城市'}的天气",
                "supported_cities": list(weather),
            },
            properties=_RESULT_PROPS,
        )
        return

    entry = weather[key]
    temp, cond = entry["temperature_c"], entry["condition"]
    await params.result_callback(
        {
            "found": True,
            "city": key,
            "temperature_c": temp,
            "condition": cond,
            "spoken": f"{key}现在{cond}，气温{temp}摄氏度",
        },
        properties=_RESULT_PROPS,
    )


WEATHER_SCHEMA = FunctionSchema(
    name="get_weather",
    description=(
        "查询某个城市的当前天气。"
        "当用户问「天气怎么样」「冷不冷」「要不要带伞」「多少度」时调用。"
        "city 只填城市名，不要带「市」以外的多余修饰。"
    ),
    properties={
        "city": {"type": "string", "description": "城市名，如「杭州」「北京」"}
    },
    required=["city"],
    handler=get_weather,
)


# ---------------------------------------------------------------- calculation (real)


# Only safe operators are allowed; never use eval -- tool arguments come from the model
# and are not trusted input.
_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError("表达式中含有不允许的运算")


async def calculate(params: FunctionCallParams) -> None:
    """Arithmetic. Real computation, but only four operations and power; no eval."""
    expr = str((params.arguments or {}).get("expression", "")).strip()

    try:
        value = _safe_eval(ast.parse(expr, mode="eval").body)
    except Exception as exc:  # noqa: BLE001 - expression comes from the model; report, do not raise
        logger.warning(f"[TOOL] calculate failed: {expr!r} -> {exc}")
        await params.result_callback(
            {"ok": False, "spoken": f"这个算式我算不了：{expr}", "error": str(exc)},
            properties=_RESULT_PROPS,
        )
        return

    # Do not show integers as 391.0
    shown = int(value) if isinstance(value, float) and value.is_integer() else value
    await params.result_callback(
        {"ok": True, "expression": expr, "result": shown, "spoken": f"等于 {shown}"},
        properties=_RESULT_PROPS,
    )


CALC_SCHEMA = FunctionSchema(
    name="calculate",
    description=(
        "进行数学计算。当用户问了任何需要算数的题目时调用，"
        "例如「23 乘以 17 等于几」「100 除以 4」「2 的 10 次方」。"
        "支持 + - * / // % ** 与小括号，请把用户的话转成算式再传入。"
    ),
    properties={
        "expression": {
            "type": "string",
            "description": "数学表达式，如 (23*17+5)/2，使用半角符号",
        }
    },
    required=["expression"],
    handler=calculate,
)


# ---------------------------------------------------------------- unit conversion (real)


# Each unit class is normalized to a base unit: length -> meter, weight -> kilogram.
_LENGTH = {"米": 1.0, "m": 1.0, "千米": 1000.0, "公里": 1000.0, "km": 1000.0,
           "厘米": 0.01, "cm": 0.01, "毫米": 0.001, "mm": 0.001,
           "英里": 1609.344, "mile": 1609.344, "英尺": 0.3048, "ft": 0.3048}
_WEIGHT = {"千克": 1.0, "kg": 1.0, "公斤": 1.0, "克": 0.001, "g": 0.001,
           "吨": 1000.0, "斤": 0.5, "磅": 0.4536, "lb": 0.4536}


def _convert_temperature(value: float, src: str, dst: str) -> float | None:
    table = {"摄氏度": "c", "℃": "c", "c": "c", "华氏度": "f", "℉": "f", "f": "f",
             "开尔文": "k", "k": "k"}
    a, b = table.get(src), table.get(dst)
    if not a or not b:
        return None
    c = value if a == "c" else (value - 32) * 5 / 9 if a == "f" else value - 273.15
    if b == "c":
        return c
    if b == "f":
        return c * 9 / 5 + 32
    return c + 273.15


async def convert_unit(params: FunctionCallParams) -> None:
    """Unit conversion. The conversion logic is real; input and output are computed."""
    args = params.arguments or {}
    value = float(args.get("value") or 0)
    src = str(args.get("from_unit", "")).strip()
    dst = str(args.get("to_unit", "")).strip()

    result = _convert_temperature(value, src, dst)
    if result is None:
        for table in (_LENGTH, _WEIGHT):
            if src in table and dst in table:
                result = value * table[src] / table[dst]
                break

    if result is None:
        await params.result_callback(
            {"ok": False, "spoken": f"我不支持从「{src}」换算到「{dst}」"},
            properties=_RESULT_PROPS,
        )
        return

    shown = round(result, 4)
    await params.result_callback(
        {
            "ok": True,
            "result": shown,
            "spoken": f"{value}{src}等于{shown}{dst}",
        },
        properties=_RESULT_PROPS,
    )


CONVERT_SCHEMA = FunctionSchema(
    name="convert_unit",
    description=(
        "单位换算，支持长度（米/千米/厘米/英里/英尺）、"
        "重量（千克/克/斤/磅/吨）、温度（摄氏度/华氏度/开尔文）。"
        "当用户问「30 摄氏度是多少华氏度」「5 公里等于多少英里」时调用。"
    ),
    properties={
        "value": {"type": "number", "description": "要换算的数值"},
        "from_unit": {"type": "string", "description": "原单位，如「摄氏度」"},
        "to_unit": {"type": "string", "description": "目标单位，如「华氏度」"},
    },
    required=["value", "from_unit", "to_unit"],
    handler=convert_unit,
)


# ---------------------------------------------------------------- device control (data-driven)
#
# State is kept **per session**, for the same reason reminders are: a module-level dict would be
# shared by every conversation (HANDBOOK-02 section 7, item 10). The device list itself comes
# from the data file, so adding a device is a data edit, not a code edit.

_DEVICE_STATE: dict[str, dict[str, str]] = {}


def _device_state(session_id: str) -> dict[str, str]:
    """On/off state for one session, initialised from the data file's device list."""
    return _DEVICE_STATE.setdefault(
        session_id, {name: "关闭" for name in load_demo_data().get("devices", [])}
    )


def device_schema(session_id: str) -> FunctionSchema:
    """Build the ``control_device`` schema bound to a single session."""

    async def control_device(params: FunctionCallParams) -> None:
        """Toggle a device. **Controls nothing real**, only changes in-memory state.

        Deliberately kept as a "has side effects but is safe" tool: useful for observing
        whether the model rushes to act when the user merely mentions something in passing.
        """
        state = _device_state(session_id)
        args = params.arguments or {}
        device = str(args.get("device", "")).strip()
        action = str(args.get("action", "")).strip().lower()

        if device not in state:
            await params.result_callback(
                {"ok": False, "spoken": f"没有找到{device or '这个设备'}",
                 "available_devices": list(state)},
                properties=_RESULT_PROPS,
            )
            return

        target = "开启" if action in ("on", "开启", "打开", "开") else "关闭"
        state[device] = target
        await params.result_callback(
            {
                "ok": True,
                "device": device,
                "state": target,
                "spoken": f"已经把{device}{target}了",
            },
            properties=_RESULT_PROPS,
        )

    # The prompt's device list is read from the data file, so it cannot drift from the data.
    devices = "、".join(_device_state(session_id)) or "（数据文件未配置设备）"
    return FunctionSchema(
        name="control_device",
        description=(
            f"开关家里的设备（{devices}）。"
            "**只有用户明确要求执行动作时才调用**，例如「把客厅灯关掉」「打开空调」。"
            "如果用户只是在描述情况或询问，不要调用。"
        ),
        properties={
            "device": {"type": "string", "description": "设备名，如「客厅灯」"},
            "action": {"type": "string", "enum": ["on", "off"], "description": "开或关"},
        },
        required=["device", "action"],
        handler=control_device,
    )


# ---------------------------------------------------------------- notification (fake send)


async def send_notification(params: FunctionCallParams) -> None:
    """Send a notification. **Nothing is actually sent**, only logged."""
    args = params.arguments or {}
    to = str(args.get("to", "")).strip() or "自己"
    message = str(args.get("message", "")).strip()

    if not message:
        await params.result_callback(
            {"ok": False, "spoken": "通知内容不能为空"}, properties=_RESULT_PROPS
        )
        return

    await params.result_callback(
        {"ok": True, "to": to, "message": message,
         "spoken": f"已经给{to}发了通知：{message}"},
        properties=_RESULT_PROPS,
    )


NOTIFY_SCHEMA = FunctionSchema(
    name="send_notification",
    description=(
        "给某人发送一条文字通知。"
        "当用户说「帮我提醒某人」「通知一下」「给他说一声」「发个消息说…」时调用。"
        "to 留空表示发给用户自己。"
    ),
    properties={
        "to": {"type": "string", "description": "接收人，留空则发给自己"},
        "message": {"type": "string", "description": "通知内容"},
    },
    required=["message"],
    handler=send_notification,
)


# ---------------------------------------------------------------- reminder (per-session storage)
#
# set_reminder is the only sample tool that carries state, so its schema is built **per
# session** instead of being one module-level list shared by every conversation. A shared
# store would let one conversation's reminders show up in another conversation's count --
# the same root cause as the module-level SESSION_ID that bot.py used to have.
#
# The store is still in memory (lost on restart); real usage should write to a memory.py
# table with a session column.

MAX_REMINDERS_PER_SESSION = 50

_REMINDERS: dict[str, list[dict]] = {}


def count_reminders(session_id: str) -> int:
    """Reminders recorded for one session (used in the tool result and by tests)."""
    return len(_REMINDERS.get(session_id, []))


def reminder_schema(session_id: str) -> FunctionSchema:
    """Build the ``set_reminder`` schema bound to a single session."""

    async def set_reminder(params: FunctionCallParams) -> None:
        """Set a reminder for this session. In memory only, so it is lost on restart."""
        args = params.arguments or {}
        content = str(args.get("content", "")).strip()
        when = str(args.get("when", "")).strip()

        if not content:
            await params.result_callback(
                {"ok": False, "spoken": "提醒内容不能为空"}, properties=_RESULT_PROPS
            )
            return

        items = _REMINDERS.setdefault(session_id, [])
        items.append({"content": content, "when": when, "created": time.time()})
        # Bound the demo store: keep the newest entries, drop the oldest.
        if len(items) > MAX_REMINDERS_PER_SESSION:
            del items[:-MAX_REMINDERS_PER_SESSION]

        await params.result_callback(
            {
                "ok": True,
                "spoken": f"好的，{when}提醒你{content}" if when else f"好的，记住了要提醒你{content}",
                "total_reminders": len(items),
            },
            properties=_RESULT_PROPS,
        )

    return FunctionSchema(
        name="set_reminder",
        description=(
            "设置一条提醒事项。"
            "当用户说「提醒我…」「别忘了…」「X 点叫我…」时调用。"
            "when 填用户说的时间描述原话（如「明天早上八点」），不要自己换算成时间戳。"
        ),
        properties={
            "content": {"type": "string", "description": "要提醒的事情"},
            "when": {"type": "string", "description": "时间，用户怎么说就怎么填"},
        },
        required=["content"],
        handler=set_reminder,
    )


# Entry point registered by tools.py: adding a stateless tool means adding one line here.
SAMPLE_SCHEMAS: list[FunctionSchema] = [
    WEATHER_SCHEMA,
    CALC_SCHEMA,
    CONVERT_SCHEMA,
    NOTIFY_SCHEMA,
]


def sample_schemas(session_id: str) -> list[FunctionSchema]:
    """All sample tools for one session: the shared stateless set plus this session's stateful
    ones (``set_reminder`` and ``control_device`` -- see their schema factories)."""
    return [*SAMPLE_SCHEMAS, reminder_schema(session_id), device_schema(session_id)]
