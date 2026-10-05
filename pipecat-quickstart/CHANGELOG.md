# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 2026-10-05

首个版本：级联式实时语音对话 Agent（`VAD → ASR → LLM → TTS`），本仓库**只依赖一个 LLM 密钥**
（语音识别与合成都跑在本地）。

### 新增

- **语音管线**：基于 Pipecat 的级联管线，含轮次判定（VAD）与打断处理。
- **本地 STT**：SenseVoice（默认，中文最优）/ Whisper（可切换），均无需密钥；
  依赖缺失时自动降级并在日志与启动横幅中如实反映。
- **本地 TTS**：Piper（默认，低延迟）/ Kokoro（可切换，音色更自然）。
- **LLM 接入**：魔搭 ModelScope（默认），可切换商汤日日新 / 共绩算力；
  三家均为 OpenAI 兼容接口，切换只改环境变量。内建「关闭思考」以压缩首 token 延迟。
- **记忆**：短期会话记忆（`turns` 表）+ 长期记忆（`facts` 表，会话注入 + 工具双层读写）。
- **知识库 / RAG**：本地嵌入 `BAAI/bge-small-zh-v1.5` + SQLite 向量检索 + 词面重排。
- **结构化查询**：`query_data` 工具（表 / 列白名单 + 参数化 SQL，模型不写 SQL）。
- **工具系统**：12 个工具，统一异常兜底（`safe_handler`）、状态按会话隔离、
  `TOOLS_EXCLUDE` 按需加载。
- **可信性护栏**：检测并强制纠正「谎报执行」（`guards.py`）。
- **显式编排**：多步工具链（复合工具，顺序由代码保证，`flows.py`）。
- **上下文摘要**：采用框架自带的 `LLMContextSummarizer`。
- **数据采集**：CSV / URL 采集脚本（采集与查询分离），含定时采集（cron / systemd）示例。
- **观测**：全量日志（`[BOOT]` / `[CLIENT]` / `[TURN]` / `[FRAME]` / `[ERROR]` / `[TOOL]`）
  与故障上报（错误同时写日志并推送前端）。
- **验证**：`scripts/` 下 13 个自检 / 探针脚本，`server/tests/` 下 113 个单元测试。

### 文档

- `README.md` —— 项目入口与全部能力说明。
- `docs/HANDBOOK.md` —— 第一阶段：怎么从零搭起来（插槽 / 配置 / 踩坑）。
- `docs/HANDBOOK-02.md` —— 第二阶段：怎么写自己的业务（工具 / 数据 / 编排 / 测试）。
- `docs/TOOL_TESTS.md` —— 工具调用压测报告（引用数据前请先读顶部的作废声明）。
- `reference/pipecat-modelscope/` —— 已冻结的调研项目（模型选型报告与基准脚本）。

### 说明

- `server/bot.py` 基于 Pipecat 官方脚手架生成，其顶部保留了上游的
  `BSD 2-Clause` 版权声明（`Copyright (c) 2024-2025, Daily`）。
