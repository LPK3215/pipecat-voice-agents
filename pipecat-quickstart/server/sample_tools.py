"""示例工具集：一批类型各异的低成本工具，用于压测模型的「灵活调用」能力。

为什么单独成模块：
    ``tools.py`` 是**注册中心**，负责把工具交给框架；
    具体工具的实现按主题分模块放，新增一批能力不必改动装配代码。

为什么全部是零成本实现：
    测的是「模型会不会选对工具、参数传得对不对」，
    与工具背后接的是真实 API 还是本地假数据**无关**。
    用本地数据可以把变量隔离干净：调用错了，一定是模型的问题，不是网络抖动。
    真实业务接入时，只把函数体换成真调用，schema 与描述基本不动。

工具类型刻意做得**多样**，因为不同「形状」的调用错误方式不一样：
    无参数查询（时间）／单参数查询（天气）／计算（表达式）／
    换算（两个单位）／写操作（开关设备）／副作用操作（发通知）
"""

from __future__ import annotations

import ast
import operator
import time

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import (
    FunctionCallParams,
    FunctionCallResultProperties,
)

# 与 tools.py 里同名常量含义一致：不传 run_llm=True 就不会触发工具后的那一次
# LLM 生成 —— 表现是「工具执行了，但机器人永远不回答」，且完全静默。
# 这里重复定义是为了避免与 tools.py 形成循环导入。
_RESULT_PROPS = FunctionCallResultProperties(run_llm=True)


# ---------------------------------------------------------------- 天气（假数据）


_WEATHER = {
    "北京": (24, "晴"),
    "上海": (27, "多云"),
    "杭州": (29, "小雨"),
    "深圳": (31, "阴"),
    "广州": (30, "雷阵雨"),
    "成都": (22, "阴"),
}


async def get_weather(params: FunctionCallParams) -> None:
    """查天气。数据是本地写死的假数据，仅用于验证调用链路。"""
    city = str((params.arguments or {}).get("city", "")).strip()
    key = city.rstrip("市")

    if key not in _WEATHER:
        await params.result_callback(
            {
                "found": False,
                "spoken": f"我还没法查{key or '这个城市'}的天气",
                "supported_cities": list(_WEATHER),
            },
            properties=_RESULT_PROPS,
        )
        return

    temp, cond = _WEATHER[key]
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


# ---------------------------------------------------------------- 计算（真实）


# 只放行安全的运算符，绝不用 eval —— 工具参数由模型生成，不能当可信输入
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
    """算数。真实计算，但只放行四则与乘方，不用 eval。"""
    expr = str((params.arguments or {}).get("expression", "")).strip()

    try:
        value = _safe_eval(ast.parse(expr, mode="eval").body)
    except Exception as exc:  # noqa: BLE001 - 表达式来自模型，任何异常都应回报而非抛出
        logger.warning(f"[TOOL] calculate 失败: {expr!r} -> {exc}")
        await params.result_callback(
            {"ok": False, "spoken": f"这个算式我算不了：{expr}", "error": str(exc)},
            properties=_RESULT_PROPS,
        )
        return

    # 整数不要显示成 391.0
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


# ---------------------------------------------------------------- 单位换算（真实）


# 每类单位统一换算到基准单位：长度→米，重量→千克
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
    """单位换算。换算逻辑是真的，输入输出都是真实计算。"""
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


# ---------------------------------------------------------------- 设备控制（假执行）


_DEVICES = ["客厅灯", "卧室灯", "空调", "加湿器"]
_DEVICE_STATE: dict[str, str] = {d: "关闭" for d in _DEVICES}


async def control_device(params: FunctionCallParams) -> None:
    """开关设备。**不会真的控制任何东西**，只改内存状态。

    刻意保留这种「有副作用但安全」的工具：用来观察模型
    会不会在用户只是随口一提时就抢着执行动作。
    """
    args = params.arguments or {}
    device = str(args.get("device", "")).strip()
    action = str(args.get("action", "")).strip().lower()

    if device not in _DEVICE_STATE:
        await params.result_callback(
            {"ok": False, "spoken": f"没有找到{device or '这个设备'}",
             "available_devices": _DEVICES},
            properties=_RESULT_PROPS,
        )
        return

    target = "开启" if action in ("on", "开启", "打开", "开") else "关闭"
    _DEVICE_STATE[device] = target
    await params.result_callback(
        {
            "ok": True,
            "device": device,
            "state": target,
            "spoken": f"已经把{device}{target}了",
        },
        properties=_RESULT_PROPS,
    )


DEVICE_SCHEMA = FunctionSchema(
    name="control_device",
    description=(
        "开关家里的设备（客厅灯、卧室灯、空调、加湿器）。"
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


# ---------------------------------------------------------------- 发通知（假发送）


async def send_notification(params: FunctionCallParams) -> None:
    """发一条通知。**不会真的发出去**，只写日志。"""
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


# ---------------------------------------------------------------- 日程（真实存储）


_REMINDERS: list[dict] = []


async def set_reminder(params: FunctionCallParams) -> None:
    """设置提醒。存在内存里（重启即失忆，真实场景应写入 memory.py 的表）。"""
    args = params.arguments or {}
    content = str(args.get("content", "")).strip()
    when = str(args.get("when", "")).strip()

    if not content:
        await params.result_callback(
            {"ok": False, "spoken": "提醒内容不能为空"}, properties=_RESULT_PROPS
        )
        return

    item = {"content": content, "when": when, "created": time.time()}
    _REMINDERS.append(item)
    await params.result_callback(
        {
            "ok": True,
            "spoken": f"好的，{when}提醒你{content}" if when else f"好的，记住了要提醒你{content}",
            "total_reminders": len(_REMINDERS),
        },
        properties=_RESULT_PROPS,
    )


REMINDER_SCHEMA = FunctionSchema(
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


# 注册到 tools.py 的入口：新增工具只在下面这个列表里加一行
SAMPLE_SCHEMAS: list[FunctionSchema] = [
    WEATHER_SCHEMA,
    CALC_SCHEMA,
    CONVERT_SCHEMA,
    DEVICE_SCHEMA,
    NOTIFY_SCHEMA,
    REMINDER_SCHEMA,
]
