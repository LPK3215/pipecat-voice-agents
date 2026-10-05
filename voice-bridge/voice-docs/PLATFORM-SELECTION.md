# 宿主平台选型调研（三个候选）

> **本文管什么**：本项目要找一个"**现成的智能体系统**"当宿主（大系统 A），把语音模块作为内部零件插进去。
> 本文记录三个候选的**官网链接、基本详情、源码分析结论**，供以后查阅。
>
> **本文不管什么**：语音模块自身怎么实现（见 [`ARCHITECTURE.md`](ARCHITECTURE.md)）。
>
> **调研时间**：2026-10-05
> **分析方式**：三个仓库源码浅克隆到 `candidates/` 后**只读**分析（未改动任何源码）。
> **结论**：选定 **Open WebUI**（Python 后端，与本项目同栈）。

---

## 0. 为什么做这次调研

本项目的定位是"**语音只是大系统的一个零件**"——大系统 A 需要出声/听声时才调用语音组件 B。

因此需要先选定那个"**已经存在、能直接用、可二次开发**"的大系统 A。选型标准（按重要性）：

1. **现成可用**：配好模型就能跑起来，不是空白引擎（排除 n8n 这类需要自己搭编排的）。
2. **可二次开发**：能改源码、能自己维护升级（有"编辑权 / 升级权 / 维护权"）。
3. **有真实业务流程**：能跑通一条真实链路（如"告警 → 语音播报"）才算行得通。
4. **插入点明确**：有现成的"语音可指向外部服务 / 可加功能模块"的位置。

> 先排除的两类：
> - **空白编排引擎**（n8n / Flowise / Langflow）：本身不含智能体，要自己搭 → 不符合"现成"。
> - **配置式低代码平台**（Dify）：操作不便，已实操否定。

---

## 1. 三个候选一览

### 1.1 Open WebUI ✅（选定）

| 项 | 内容 |
|---|---|
| 官网 | <https://openwebui.com/> |
| GitHub | <https://github.com/open-webui/open-webui> |
| 文档 | <https://docs.openwebui.com/> |
| 版本（分析时） | `0.11.4` |
| 定位 | 自托管 AI 平台（"a home for AI"）：聊天 + RAG + 工具 + 语音/视频，支持多用户、可完全离线 |
| 前端 | SvelteKit + Svelte 5 + Vite + Tailwind |
| **后端** | **Python / FastAPI** + uvicorn + python-socketio + SQLAlchemy(async) + Alembic |
| 数据库 | SQLite（默认，可加密）或 PostgreSQL；另支持 9 种向量库 |
| **许可证** | **Open WebUI License**（BSD-3 风格 + 品牌保留条款）⚠️ 见第 4 节 |

**扩展机制（都不需要改核心源码）**：

| 机制 | 是什么 | 代码位置 |
|---|---|---|
| **Functions** | 单文件 Python 插件，按顶层类自动识别为 4 类：`Pipe`（伪装成模型）/ `Filter`（请求进/出中间件）/ `Action`（按钮动作）/ `Event`（事件钩子） | `backend/open_webui/utils/plugin.py`（`load_function_module_by_id`）、`backend/open_webui/functions.py`、`routers/functions.py` |
| **Tools** | 单文件 Python 插件，`Tools` 类的类方法被转成 OpenAI function-calling schema | `utils/plugin.py`（`load_tool_module_by_id`）、`utils/tools.py`（`get_tool_specs`）、`routers/tools.py` |
| **Pipelines** | **独立外部服务**（另一仓库 `open-webui/pipelines`），平台以"OpenAI 兼容连接"方式访问；用 Filter 钩子做进/出中间件 | `backend/open_webui/routers/pipelines.py`（仅反向代理） |
| MCP / OpenAPI Tool Server | 外部工具服务器 | `backend/open_webui/tools/builtin.py` |

**语音能力现状**：

| 项 | 内容 |
|---|---|
| STT 引擎 | 本地 `faster-whisper`（默认）、OpenAI 兼容、Deepgram、Azure、Mistral |
| TTS 引擎 | OpenAI 兼容、ElevenLabs、Azure、Transformers（本地）、Mistral；前端另有浏览器 `speechSynthesis` / Kokoro |
| 配置方式 | 环境变量（`AUDIO_STT_ENGINE` / `AUDIO_TTS_ENGINE` 等，见 `backend/open_webui/config.py`）+ 运行时管理员 UI（`POST /api/v1/audio/config/update`） |
| **能否指向外部自定义服务** | ✅ **可以** —— 只要对外暴露 **OpenAI 兼容**端点：STT 走 `{base}/audio/transcriptions`、TTS 走 `{base}/audio/speech` |
| **接入点文件** | `backend/open_webui/routers/audio.py`（STT：`_transcribe_openai`；TTS：`_tts_openai`） |
| **是否实时双向** | ❌ **不是**。前端 VAD 仅为客户端静音检测（静音约 2s 停录），整段音频**批量**上传识别；后端只有请求/响应式 `/transcriptions`、`/speech`，**没有音频 WebSocket / 服务端轮次判定** |
| 相关代码 | `routers/audio.py`、`config.py`、前端 `src/lib/components/chat/MessageInput/CallOverlay.svelte`、`src/lib/apis/audio/index.ts` |

**能否把外部实时语音模块插进来**：
- **能接**：把 STT/TTS 的 OpenAI 兼容 `api_base_url` 指向自建语音服务（不改核心）；或用 Function/Pipeline 做文本级钩子。
- **接不了的地方**：平台**没有实时双工音频钩子**——要做真·实时双向（服务端 VAD / 打断 / 轮次），必须**改核心**：前端 `CallOverlay.svelte` + 后端 `routers/audio.py`（并新增音频 WS 端点）。

---

### 1.2 LibreChat（备选：许可证最自由）

| 项 | 内容 |
|---|---|
| 官网 | <https://www.librechat.ai/> |
| GitHub | <https://github.com/danny-avila/LibreChat> |
| 文档 | <https://www.librechat.ai/docs> |
| 版本（分析时） | `v0.8.8` |
| 定位 | 自托管 AI 对话平台，把多家 provider 统一到一个界面；含 Agents、MCP、Artifacts、Code Interpreter、多用户鉴权 |
| 前端 | React + Vite + Tailwind（Recoil→Jotai 迁移中） |
| 后端 | Node.js / Express + **TypeScript**（`api/` 是 CJS 接线层，业务逻辑在 `packages/api/`） |
| 数据库 | MongoDB（Mongoose）；搜索 Meilisearch；RAG 向量库 pgvector |
| **许可证** | **MIT** ✅ 最自由（可任意使用/修改/分发/再授权/出售，仅需保留版权声明） |

**扩展机制**：

| 机制 | 说明 | 位置 |
|---|---|---|
| **MCP server**（一等公民，零改核心） | 在 `librechat.yaml` 的 `mcpServers:` 配置（stdio / sse / streamable-http） | `librechat.example.yaml`、`packages/api/src/mcp/` |
| 自定义 OpenAI 兼容 endpoint（零改核心） | `endpoints.custom` | `packages/data-provider/src/config.ts` |
| **Skills**（零改核心） | 顶层 `skill/` 目录，每个技能一个文件夹 + `SKILL.md`，启动时加载、只读 | `packages/api/src/skills/deployment.ts` |
| Agents | UI 无代码创建，可挂 MCP / 工具 / 代码执行 / Subagents | `librechat.example.yaml` 的 `endpoints.agents` |
| 深度扩展（需写 TS） | 新后端行为写进 `packages/api`，工具注册表 `api/app/clients/tools/manifest.js` | 见 `AGENTS.md` |

**语音能力现状**：
- STT：`openai`、`azureOpenAI`，另有浏览器 Web Speech API。
- TTS：`openai`、`azureOpenAI`、`elevenlabs`、`localai`，另有浏览器 `speechSynthesis`。
- 配置：`librechat.yaml` 的 `speech.tts` / `speech.stt`（`url`、`apiKey`、`model`、`voices`）。
- ❌ **没有实时双向语音 / barge-in 打断**；只有"录音 → 上传 → 转文字" + "文字 → 朗读"。
- 唯一类 VAD：前端静音检测（约 3s 自动停录），不是打断模型输出。
- 相关代码：后端 `api/server/services/Files/Audio/`、`api/server/routes/files/speech/`；前端 `client/src/hooks/Input/`、`client/src/hooks/Audio/`。

**插入外部语音模块**：可行，首选**零改核心**的 **MCP server**（把实时语音服务封装成 MCP）或**自定义 endpoint**。平台只做"文本进 / 文本出"编排，实时音频由外部模块自持。

---

### 1.3 LobeChat（排除）

| 项 | 内容 |
|---|---|
| 官网 | <https://lobehub.com/> |
| GitHub | <https://github.com/lobehub/lobe-chat> |
| 文档 | <https://lobehub.com/docs> |
| 版本（分析时） | `@lobehub/lobehub` `2.2.17` |
| 定位 | 开源 AI Agent 框架 / 工作空间（Agent 团队、Agent Builder、个人记忆） |
| 前端 | Next.js 16 + React 19 + TypeScript（内嵌 SPA，react-router） |
| 后端 | **Hono + TypeScript**（业务在 `apps/server/src`；`src/app/(backend)` 只是 Next.js route shell） |
| 数据库 | PostgreSQL + Drizzle ORM；另有 Redis、S3、better-auth |
| 结构 | monorepo：`apps/`（server / desktop / cli / share / workbench / auth）+ `packages/`（100+ 包） |
| **许可证** | **LobeHub Community License**（Apache 2.0 + 附加条件）⚠️ 见第 4 节 |

**扩展机制**：
- 顶层 `plugins/` 只是 **Vite 构建期插件**，与聊天插件无关。
- 聊天插件：独立仓库 + manifest（`@lobehub/chat-plugin-sdk`），动态加载 → 不改核心。
- **MCP**：完整支持（`src/services/mcp.ts`、`apps/server/src/routers/tools/mcp.ts`）。
- 深度 UI 集成：新增 `packages/builtin-tool-xxx` 并在 `packages/builtin-tools/src/register.ts` 注册 → **要改核心源码**。

**语音能力现状**：
- STT：`packages/model-runtime`（openai 兼容 / azure / google），后端 tRPC `apps/server/src/routers/lambda/asr.ts`。
- TTS：外置库 `@lobehub/tts`，`webapi/tts/openai/route.ts`。
- ❌ **没有实时双向语音 / 打断**；最接近的是"实时流式听写"（`src/features/ChatInput/Dictation/*`，WebSocket + 16kHz PCM），但该会话入口 `/webapi/asr/realtime/session` **不在 OSS 后端**，属**云端 / business 能力**。

**排除理由**：最重（346M、100+ 包）、TypeScript 栈（与 Python 语音模块不同源）、**"改源码后分发需商业许可"**（与"编辑权 / 升级权"诉求冲突最大）。

---

## 2. 横向对比表

| 维度 | **Open WebUI** ✅ | LibreChat | LobeChat |
|---|---|---|---|
| 定位 | 自托管 AI 平台（聊天/RAG/工具） | 自托管 AI 对话平台（多 provider/Agents） | AI Agent 框架与工作空间 |
| 前端 | SvelteKit | React + Vite | Next.js + React 19 |
| **后端** | **Python / FastAPI** ✅ | Node/Express + TS | Hono + TS |
| 数据库 | SQLite / PostgreSQL | MongoDB | PostgreSQL |
| 加模块的姿势 | **Functions / Tools / Pipelines（纯 Python）** | MCP / 自定义 endpoint / `skill/` | MCP / 插件 / `builtin-tool-*` |
| 语音能否指向外部服务 | ✅ 接入点 `routers/audio.py` | ✅ | ✅ |
| **实时双向语音（VAD/打断）** | ❌ 无 | ❌ 无 | ❌ 无（云端版才有） |
| **许可证** | Open WebUI License（品牌保留） | **MIT**（最自由） | Community（改源码分发需商业许可） |
| 仓库规模 | 中（约 67M） | 中（约 94M） | 重（约 346M） |
| 语言与语音模块同源 | ✅ **是（Python）** | ❌ 否 | ❌ 否 |

---

## 3. 关键发现

1. **三个系统全都没有"实时双向语音"**——都只是"录音 → 转文字 → 朗读"的**点按式**语音，没有服务端 VAD、没有真打断、没有全双工。
   → 这**正好是语音模块要填补的空白**：**把"点按式语音"升级为"实时对话式语音"**。这是"值不值得做"的正面回答。

2. **只有 Open WebUI 是 Python 后端**，与本项目（Python / Pipecat）**同栈**，已有代码可直接搬用，改造成本最低。

3. **Open WebUI 有明确的"语音可指向外部服务"的设置点**（`routers/audio.py` 的 OpenAI 兼容 `api_base_url`），是三个里最清晰的"插入位置"。

---

## 4. 许可证注意点（重要，逐条核对过）

| 项目 | 协议 | 关键限制 | 对本项目（内部使用 + 改源码） |
|---|---|---|---|
| **Open WebUI** | Open WebUI License（BSD-3 风格 + 品牌条款） | 第 4 条：不得移除/篡改 "Open WebUI" 品牌，除非：① 30 天内终端用户 ≤ 50 人；② 获书面许可；③ 持企业许可 | ✅ 自用、改源码、维护升级**合法**；仅"去品牌对外分发"受限 |
| **LibreChat** | **MIT** | 仅需保留版权与许可声明 | ✅ 完全自由 |
| **LobeChat** | LobeHub Community License（Apache 2.0 + 附加） | 不改源码可商用；**开发并分发衍生作品（改源码后分发）须获商业许可** | ⚠️ 自用可，分发受限 |

> **诚实标注**：仓库内 `package.json` 的 `license` 字段与随附 `LICENSE` 文件**不一致**——LibreChat 写 `ISC` 但 `LICENSE` 是 MIT；LobeChat 写 `MIT` 但 `LICENSE` 是 Community License；Open WebUI 的 `pyproject.toml` 标注 `Other/Proprietary License`。**一律以仓库根目录的 `LICENSE` 文件为准。**

---

## 5. 结论与理由

> **选定宿主：Open WebUI**（Python 为主）。

| 理由 | 说明 |
|---|---|
| 1. **技术栈同源（最关键）** | 唯一 Python 后端，语音模块与已有代码可直接搬用、无需跨语言重写 |
| 2. **插入点现成** | `routers/audio.py` 支持把 STT/TTS 指向外部 OpenAI 兼容服务 |
| 3. **扩展机制是 Python** | Functions / Tools / Pipelines 全用 Python |
| 4. **许可证对"自用"无碍** | 品牌条款只限制"去品牌分发"，内部使用 + 改源码完全合法 |

- **备选**：**LibreChat** —— 许可证 MIT 最自由；但后端为 TypeScript，语音模块只能走外部服务 / MCP，改造更费力。
- **排除**：**LobeChat** —— 最重、TS 栈、许可证对"改源码分发"限制最强。

---

## 6. 下一步（待办，未执行）

1. **落地清单**：Open WebUI 本地跑起来 → 确认"配好模型就能用"。
2. **实现语音模块**：按 [`ARCHITECTURE.md`](ARCHITECTURE.md) 契约写最小实现（一个会话 + 一条音频通道 + 一次主动播报）。
3. **写适配层（本项目核心工作量）**：
   - Open WebUI 的语音是"**整段音频批量上传**"（`POST /audio/transcriptions`）；
   - 语音模块是"**流式 WebSocket**"（`WS /sessions/{id}/audio`）；
   - **两者协议不匹配**，需要写一层适配（在 Open WebUI 侧写 Function/Pipeline 调用语音模块的实时接口）。
4. **跑真实业务流程**：如"告警 → 语音播报"，用探针量化。

---

## 附：候选仓库源码位置

分析基于以下浅克隆的源码快照（**只读，未改动**）：

```
candidates/
├── open-webui/    0.11.4        （Python/FastAPI + SvelteKit）
├── LibreChat/     v0.8.8        （Node/TS + React）
└── lobe-chat/     2.2.17        （Next.js/Hono）
```
