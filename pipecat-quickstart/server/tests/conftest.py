"""pytest 公共夹具。

这些是**单元测试**：不联网、不调 LLM、不加载语音模型，只验证纯逻辑
（配置解析、工具 handler、SQL 白名单与持久化）。端到端验证仍由仓库根目录的
verify_stack.py / smoke.py / audio_probe.py 等脚本承担。

为什么需要：此前仓库没有任何单测，回归只能靠跑完整链路（分钟级）。
这里把「改一行就可能静默坏掉」的逻辑固定下来——尤其是
search_facts 的单字查询、SQL 白名单、以及各服务商的「关思考」参数。
"""

import pathlib
import sys

import pytest

# 让测试能 import 到 server/ 下的模块（settings/tools/memory/...）
SERVER_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """把 memory 的数据库指向临时文件，避免污染真实 data/memory.db。"""
    import memory

    monkeypatch.setattr(memory, "DB_PATH", tmp_path / "test.db")
    memory.init_db()
    return memory
