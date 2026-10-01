"""本地持久化：会话历史 + 长期记忆 + 业务数据表。

为什么自己写这一层：
    pipecat **不提供任何持久化**。它自带的只有「短期记忆」——即 ``LLMContext``
    里的消息列表，进程一退出就没了；长期记忆只给了一个 mem0 适配器
    （``services/mem0/memory.py``），而 mem0 云端要 key、可能收费。
    知识库（向量检索）则完全没有。

    结论：这三类都只能自己接。SQLite 是零依赖、零成本、零运维的起点，
    之后想换 mem0 或向量库，**替换位置就在本模块内** —— 对外暴露的始终是
    「读一段文本塞进上下文」或「注册一个工具」，上层不用改。

三张表各自的定位：
    turns    会话历史（临时记忆的持久化）：重启后能接着上一轮聊
    facts    长期记忆：跨会话记住的用户偏好、约定、结论
    metrics  业务数据：外部系统（如监控页面）抓来的指标，供工具查询

关于「AI 系统 = 临时记忆 + 长期记忆 + 技能 + 知识库 + 数据库」：
    本模块承担了其中的「长期记忆 + 数据库」两块；
    「临时记忆」由框架的 LLMContext 负责（本模块只做它的持久化）；
    「技能」是 tools.py；「知识库」暂未实现，可在此基础上加向量检索。
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameProcessor

# 数据库文件路径。默认放在 server/data/ 下，与代码分离
DB_PATH = Path(
    os.getenv("MEMORY_DB", str(Path(__file__).resolve().parent / "data" / "memory.db"))
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, id);

CREATE TABLE IF NOT EXISTS facts (
    key        TEXT    PRIMARY KEY,
    value      TEXT    NOT NULL,
    source     TEXT    NOT NULL DEFAULT 'user',
    created_at REAL    NOT NULL,
    updated_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    value      REAL    NOT NULL,
    unit       TEXT    NOT NULL DEFAULT '',
    note       TEXT    NOT NULL DEFAULT '',
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics(name, id);
"""


def _conn() -> sqlite3.Connection:
    """取一个连接。

    SQLite 的调用都在毫秒级，这里直接用同步接口；若将来量大再换 aiosqlite，
    替换点只在函数内部。
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表。重复调用无副作用（都用 IF NOT EXISTS）。"""
    with _conn() as conn:
        conn.executescript(_SCHEMA)


def new_session_id() -> str:
    """每次连接一个会话 ID。短一点，日志里好认。"""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- 会话历史


def save_turn(session_id: str, role: str, content: str) -> None:
    """存一轮对话。空内容不存 —— 打断、静音会产生空文本。"""
    text = (content or "").strip()
    if not text:
        return
    with _conn() as conn:
        conn.execute(
            "INSERT INTO turns (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (session_id, role, text, time.time()),
        )


def recent_turns(session_id: str | None = None, limit: int = 20) -> list[dict]:
    """取最近若干轮。

    ``session_id`` 为空时跨会话取（用于「上次我们聊到哪」这种恢复场景）。
    """
    with _conn() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT role, content FROM turns WHERE session_id=? "
                "ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content FROM turns ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


# ---------------------------------------------------------------- 长期记忆


def put_fact(key: str, value: str, source: str = "user") -> None:
    """记一条长期事实。同 key 覆盖（保留 created_at）。"""
    now = time.time()
    with _conn() as conn:
        conn.execute(
            """INSERT INTO facts (key, value, source, created_at, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value, source=excluded.source,
                   updated_at=excluded.updated_at""",
            (key, value, source, now, now),
        )


def get_fact(key: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT key, value, updated_at FROM facts WHERE key=?", (key,)
        ).fetchone()
    return dict(row) if row else None


def delete_fact(key: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM facts WHERE key=?", (key,))
    return cur.rowcount > 0


def list_facts(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT key, value FROM facts ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def search_facts(query: str, limit: int = 5) -> list[dict]:
    """按关键词查长期记忆。

    没有向量库之前，这是「知识库」的最小可用形态：子串匹配 + 分词兜底。
    中文没有空格，所以把 query 切成 2~4 字的片段再 OR 匹配，
    比整串 LIKE 命中率高得多。之后要换向量检索，替换点就是这个函数。
    """
    q = (query or "").strip()
    if not q:
        return []

    terms = [q]
    # 中文按 2 字滑窗切分；英文/数字按空格切
    if re.search(r"[\u4e00-\u9fff]", q):
        cleaned = re.sub(r"[^\u4e00-\u9fff0-9a-zA-Z]", "", q)
        terms += [cleaned[i : i + 2] for i in range(len(cleaned) - 1)]
    else:
        terms += q.split()
    terms = [t for t in dict.fromkeys(terms) if len(t) >= 2][:8]

    where = " OR ".join(["key LIKE ? OR value LIKE ?"] * len(terms))
    params: list = []
    for t in terms:
        params += [f"%{t}%", f"%{t}%"]

    with _conn() as conn:
        rows = conn.execute(
            f"SELECT key, value FROM facts WHERE {where} "
            f"ORDER BY updated_at DESC LIMIT ?",  # noqa: S608 - terms 由本地切分产生
            (*params, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 业务数据


def record_metric(
    name: str, value: float, unit: str = "", note: str = ""
) -> None:
    """写入一条业务指标。外部采集脚本（如爬监控页）调这个。"""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO metrics (name, value, unit, note, created_at) VALUES (?,?,?,?,?)",
            (name, value, unit, note, time.time()),
        )


def query_metrics(name: str | None = None, limit: int = 5) -> list[dict]:
    """查最新指标。``name`` 为空时返回所有指标名的最新一条。"""
    with _conn() as conn:
        if name:
            rows = conn.execute(
                "SELECT name, value, unit, note, created_at FROM metrics "
                "WHERE name LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{name}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT name, value, unit, note, created_at FROM metrics m
                   WHERE id = (SELECT MAX(id) FROM metrics WHERE name = m.name)
                   ORDER BY name LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


class TurnRecorder(FrameProcessor):
    """把每轮对话写进 ``turns`` 表。

    为什么需要它：框架的 ``LLMContext`` 只在内存里，进程重启即失忆。
    挂到管线末端即可同时看到用户侧（STT 产出的 TranscriptionFrame）
    与助手侧（LLM 产出的 LLMTextFrame）—— 控制帧与文本帧都会一路向下游传播。

    助手侧要在 ``LLMFullResponseEndFrame`` 才落库：一次回答是流式产生的，
    逐帧存会把一句话拆成几十条。
    """

    def __init__(self, session_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._session_id = session_id
        self._pending: list[str] = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            save_turn(self._session_id, "user", frame.text)
        elif isinstance(frame, LLMTextFrame):
            self._pending.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._pending:
                save_turn(self._session_id, "assistant", "".join(self._pending))
                self._pending.clear()

        await self.push_frame(frame, direction)


def load_memory_into_context(context, limit: int = 10) -> int:
    """把长期记忆与上一轮会话塞进 LLMContext。

    这是「长期记忆」真正生效的地方：框架不管你怎么存，
    但你要在**建上下文时**把记忆放进去，模型才看得到。
    返回注入的消息条数。
    """
    injected = 0
    facts = list_facts(limit=20)
    if facts:
        lines = "\n".join(f"- {f['key']}: {f['value']}" for f in facts)
        context.add_message(
            {
                "role": "user",
                "content": f"以下是你需要记住的关于用户的信息：\n{lines}",
            }
        )
        injected += 1
    return injected


def seed_demo_metrics() -> int:
    """灌入示例业务数据，便于在没有真实数据源时验证工具链路。

    返回写入条数；已有数据时不重复灌。
    """
    if query_metrics(limit=1):
        return 0
    demo = [
        ("服务可用性", 99.95, "%", "最近 24 小时"),
        ("平均响应延迟", 187.0, "ms", "最近 1 小时"),
        ("错误率", 0.12, "%", "最近 1 小时"),
        ("活跃用户数", 3421.0, "人", "当前在线"),
    ]
    for name, value, unit, note in demo:
        record_metric(name, value, unit, note)
    logger.info(f"[MEMORY] 已灌入 {len(demo)} 条示例指标（示例数据，非真实业务）")
    return len(demo)
