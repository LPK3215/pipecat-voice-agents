# 📄 Pipecat 调研文档（实操版）

> **这是什么**：对 Pipecat 这个**框架**的调研 —— 它能干什么、怎么组装、适合什么场景。
> 上手前了解全貌时读它；判断「该不该用这个框架」也读它。
>
> **不是什么**：不是本项目的使用说明。要看具体实现，
> 去 [`pipecat-quickstart/`](pipecat-quickstart/)；三个东西的区别见 [`README.md`](README.md)。

> 调研对象：https://github.com/pipecat-ai/pipecat
> 官网：https://www.pipecat.ai/ ｜ 文档：https://docs.pipecat.ai/
> 组织/维护方：pipecat-ai，由 **Daily** 与社区共同维护
> 许可证：**BSD-2-Clause**（可免费商用）｜ 语言：Python（≥ 3.11）

---

## 0. 一句话定位

Pipecat 是一个**开源的 Python 编排框架**，用于构建**实时语音与多模态对话式 AI 智能体**。

它本身**不是语音模型，也不是开箱即用的机器人**——它负责把第三方的 STT（语音识别）/ LLM（大模型）/ TTS（语音合成）、网络传输层（浏览器、电话）和音频处理模块，用「**流水线 + 帧**」的方式组装成一条超低延迟的实时对话管线（典型往返 **500–800ms**）。

> 用大白话说：它是 AI Agent 的“**耳朵和嘴巴**”，不生产智能，负责把各家智能能力串成能实时对话的系统。

**关键前提**：需要自备各服务商 API Key，只为所选第三方服务付费；默认走云端服务，**不适合纯文本聊天、也不适合要求全离线零 API 依赖的场景**（本地模型需额外配置）。

---

## 1. 使用场景（What you can build）

| 场景 | 说明 | 典型实现 |
|---|---|---|
| **网页语音助手** | 浏览器里直接开口对话，麦克风进、扬声器出 | WebRTC / SmallWebRTC + STT + LLM + TTS |
| **AI 电话客服** | 通过 PSTN 电话接入，做客服、信息采集、外呼 | Twilio / Telnyx / Plivo + 电话串行器 |
| **语音助手 / 陪伴** | 自然轮次的闲聊、教练、会议助理 | voice + turn-management |
| **引导式 / 表单式对话** | 有固定流程的客服、预约、信息收集 | Pipecat Flows（状态机式对话） |
| **实时语音到语音** | 追求极低延迟的双向实时对话 | OpenAI Realtime、Gemini Live、AWS Nova Sonic、Ultravox、Grok |
| **多模态应用** | 语音 + 视频 + 图像 + 文本一起交互 | vision、video-processing、image-generation |
| **数字人 / 视频主播** | 带虚拟形象说话 | Tavus、HeyGen、Simli、LemonSlice |
| **工具调用型 Agent** | 让 AI 查天气、订票、调 API、连 MCP 工具 | function-calling、mcp |
| **知识库 / 长期记忆问答** | 接企业知识库、跨会话记忆 | rag、persistent-context、context-summarization |
| **多智能体系统** | 多个 Agent 交接、并行分发、分布式协作 | multi-worker、Handoff、Job |

**判断是否适合用 Pipecat**：
- ✅ 适合：需要**实时语音对话**、想**在多家 ASR/LLM/TTS 之间灵活切换**、需要**多模态**、需要**电话/网页多渠道接入**的 Python 项目。
- ❌ 不适合：只需要纯文本聊天（直接用 LLM SDK 即可）、要求全部本地离线零 API 依赖。

---

## 2. 功能怎么组装（核心概念 + 代码）

### 2.1 三个基础概念

| 概念 | 作用 |
|---|---|
| **Pipeline（流水线）** | 把若干处理器按顺序连接，形成数据流动路径 |
| **Frame（帧）** | 数据容器（音频/文本/视频/控制信号），带唯一 ID 便于调试追踪，如 `TranscriptionFrame#1` |
| **Frame Processor（处理器）** | 接收上游帧 → 处理 → 生成新帧 → 推给下游 |

### 2.2 组装一条语音管线

顺序即职责链：**输入 → STT → 上下文 → LLM → TTS → 输出 → 上下文回填**。

```python
pipeline = Pipeline([
    transport.input(),              # 接收用户音频
    stt,                            # 语音转文字 (STT)
    context_aggregator.user(),      # 收集用户回复写入上下文
    llm,                            # 语言模型 (LLM)
    tts,                            # 文字转语音 (TTS)
    transport.output(),             # 把音频发回用户
    context_aggregator.assistant(), # 收集助手回复写入上下文
])
```

> 组装要点：**顺序至关重要**，处理器必须排在“能拿到所需帧类型”的位置，否则拿不到数据。

### 2.3 组装 AI 服务（可任意替换）

```python
stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))

tts = CartesiaTTSService(
    api_key=os.getenv("CARTESIA_API_KEY"),
    settings=CartesiaTTSService.Settings(
        voice=os.getenv("CARTESIA_VOICE_ID", "86e30c1d-714b-4074-a1f2-1cb6b552fb49"),
    ),
)

llm = OpenAIResponsesLLMService(
    api_key=os.getenv("OPENAI_API_KEY"),
    settings=OpenAIResponsesLLMService.Settings(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1"),
        system_instruction="You are a helpful assistant in a voice conversation. ...",
    ),
)
```

**换服务商只需替换这个对象，其余代码不动**，例如：

- STT：Deepgram → **Soniox**
- LLM：OpenAI → **Anthropic**
- TTS：Cartesia → **ElevenLabs**

### 2.4 组装多轮对话记忆（上下文）

```python
context = LLMContext()
user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
    context,
    user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
)
```

### 2.5 组装“能力”（可插拔的扩展）

| 想加的能力 | 组装方式 |
|---|---|
| **打断（interruption）** | 由 VAD + 轮次管理处理器处理（turn-management） |
| **函数调用 / 工具** | 在 LLM 上挂 function calling，或接 **MCP** 工具服务器 |
| **知识库 RAG** | 插入检索处理器 + mem0 / 向量库 |
| **上下文太长** | 插入 `context-summarization` 做摘要 |
| **运行时换音色 / 换模型** | 用 `update-settings` 示例的方式动态改配置 |
| **背景音 / 录音** | 用 `audio/` 示例的处理器 |
| **并行分支（如双语 TTS）** | 用 `ParallelPipeline` 多分支各自过滤处理 |

### 2.6 帧的类型决定调度优先级

| 类型 | 优先级 | 典型示例 |
|---|---|---|
| **SystemFrame** | 高优先级队列，中断不丢弃 | `InputAudioRawFrame`、`InterruptionFrame`、`ErrorFrame` |
| **DataFrame** | 非系统队列，按序 | `TextFrame`、`TranscriptionFrame`、`LLMTextFrame` |
| **ControlFrame** | 非系统队列，按序 | `EndFrame`、`TTSStartedFrame` |

> 特性：处理器**不消费帧、只传递**（同一路音频可被多个处理器复用）；框架保证各通道内严格保序。

---

## 3. 使用方法（从 0 跑通到上线）

### 3.1 环境准备

- Python **≥ 3.11**（推荐 ≥ 3.12）
- 安装 **uv**：`curl -LsSf https://astral.sh/uv/install.sh | sh`
- 准备三个 API Key（以默认示例为例）：**Deepgram**（STT）、**OpenAI**（LLM）、**Cartesia**（TTS）

### 3.2 本地跑通（约 5 分钟）

```bash
# 1. 安装 CLI 并生成脚手架
uv tool install "pipecat-ai[cli]"
pipecat init quickstart
cd pipecat-quickstart

# 2. 配置密钥
cp .env.example .env      # 填入 DEEPGRAM / OPENAI / CARTESIA 三个 Key

# 3. 安装依赖并启动
uv sync
uv run bot.py             # 首次约 20 秒（下载模型、导入依赖）
```

浏览器打开 **http://localhost:7860/client/**，点击 **Connect**，允许麦克风即可对话。

### 3.3 生产部署到 Pipecat Cloud（约 5 分钟）

```bash
pipecat cloud auth login                                   # 浏览器授权
# 查看 pcc-deploy.toml：agent_name / secret_set / [scaling] min_agents
pipecat cloud secrets set pipecat-quickstart-secrets --file .env   # 上传密钥
pipecat cloud deploy                                       # 自动构建镜像并部署
```

> 同一份代码本地与云端通用，**部署到 Pipecat Cloud 无需改代码**。

### 3.4 换成其他接入方式（传输层）

- **Daily**：注册 dashboard.daily.co，配置 `DAILY_ROOM_URL`、`DAILY_API_KEY`，运行加 `-t daily`
- **Twilio（电话）**：安装并运行 `ngrok http 7860`，配置 TwiML 指向 ngrok 地址，然后：

```bash
uv run getting-started/06-voice-agent.py -t twilio -x NGROK_HOST_NAME
```

- **自定义网络**：`uv run python <example> --host 0.0.0.0 --port 8080`

### 3.5 运行入口与事件（bot.py 关键片段）

```python
runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
await runner.add_workers(worker)

@worker.rtvi.event_handler("on_client_ready")
async def on_client_ready(rtvi):
    context.add_message({"role": "developer", "content": "Start by concisely introducing yourself."})
    await worker.queue_frames([LLMRunFrame()])   # 触发开场白

@transport.event_handler("on_client_disconnected")
async def on_client_disconnected(transport, client):
    await runner.cancel()                        # 断线清理资源

await runner.run()
```

### 3.6 常见问题排查

- **无音频/视频**：检查浏览器麦克风/摄像头权限
- **连接失败**：先换浏览器；再检查 VPN/防火墙（WebRTC 走 UDP）
- **报错**：检查 `.env` 中 API Key 是否正确
- **端口冲突**：用 `--port` 更换端口

---

## 4. 使用案例（官方 examples 目录）

仓库 `examples/` 提供 **30+ 生产级示例**，README 明确“展示如何用 Pipecat 构建语音与多模态智能体”。运行方式：

```bash
cp env.example .env        # 填 API Key（须在仓库根目录执行）
uv run python getting-started/01-say-one-thing.py
# 打开 http://localhost:7860/client/ 点击 Connect
```

| 示例目录 | 功能 | 对应场景 |
|---|---|---|
| **getting-started/** | 渐进式入门：从最小 TTS 到带 function calling 的完整语音 Agent | 第一次上手 |
| **voice/** | 完整 STT+LLM+TTS 语音 Agent，演示多家语音服务商 | 生产级语音机器人 |
| **transcription/** | 各家 STT 供应商的语音转文字 | 转写需求 |
| **realtime/** | OpenAI Realtime / Gemini Live / Nova Sonic / Ultravox / Grok | 低延迟实时对话 |
| **turn-management/** | 轮次检测、打断处理、用户输入管理 | 自然对话节奏 |
| **flows/** | Pipecat Flows 结构化对话（预定义 + 动态路径 + 状态管理） | 客服、表单、引导式对话 |
| **function-calling/** | 多家 LLM 的函数调用 | 让 AI 调用工具/API |
| **mcp/** | MCP（Model Context Protocol）工具服务器集成 | 接外部工具生态 |
| **vision/** | 图像描述 + 多模态 LLM | 看图说话 |
| **image-generation/** | 文生图（fal / Google / OpenAI） | 生成图片 |
| **thinking/** | LLM 思考/推理模式 | 复杂推理 |
| **persistent-context/** | 跨会话保持上下文 | 长期记忆 |
| **context-summarization/** | 对话摘要以控制 token | 长对话 |
| **rag/** | 检索增强 + grounding + 长期记忆（Mem0、Gemini） | 知识库问答、客服检索 |
| **update-settings/stt·tts·llm** | 运行时修改 STT/TTS/LLM 配置 | 动态换模型/音色 |
| **transports/** | WebRTC、Daily、LiveKit 传输层 | 多渠道接入 |
| **multi-worker/** | 多 worker | 多智能体 |
| **video-avatar/** | 数字人（Tavus、HeyGen、Simli、LemonSlice） | 虚拟主播 |
| **video-processing/** | 视频处理、镜像、GStreamer、自定义视频轨道 | 视频场景 |
| **audio/** | 音频录制、背景音、音效 | 音频处理 |
| **observability/** | observers、心跳、Sentry 指标 | 线上监控 |
| **features/** | 唤醒词、实时翻译、服务切换、音色切换、DTMF 键盘菜单 | 杂项功能 |
| **assets/、context-summarization** 等 | 资源与其余能力示例 | 参考 |

> 更多示例见独立仓库 **pipecat-examples**。

### 4.1 典型案例：15 行代码搭一个 AI 电话客服

核心思路 = 语音识别 + 大模型对话 + 语音合成 + 电话接入 + 对话记忆；社区实践显示接入 Twilio 的骨架代码可压缩到十几行：

```python
from pipecat.transports.services.daily import DailyTransport
from pipecat.serializers.twilio import TwilioFrameSerializer

transport = DailyTransport(room_url="...", token="...", bot_name="Bot")
# pipeline 内串上 stt → llm → tts，即可让电话那头与 AI 实时对话
```

### 4.2 典型案例：网页语音助手（10 分钟可跑）

`pipecat init quickstart` 生成的 `bot.py` 即是完整案例：浏览器 WebRTC 采集麦克风 → Silero VAD 检测说话起止 → Deepgram 转写 → GPT 生成回复 → Cartesia 合成语音 → 流式回传浏览器，一次往返 **<1 秒**。

---

## 5. 架构与生态补充

### 5.1 多智能体层

| 组件 | 职责 |
|---|---|
| **WorkerRunner** | 拥有消息总线，管理各 agent 生命周期与发现；单 bot 与多 agent 共用 |
| **BaseWorker** | Agent 基础单元，纯总线协调，自身无 pipeline（适合做协调者） |
| **PipelineWorker** | Worker + 一条 Pipecat pipeline |
| **LLMWorker** | Worker + pipeline + LLM 能力 |

协作方式：**Handoff（交接）** / **Job（并行分发汇总）** / **分布式（跨进程跨机器共享总线）**。
部署切换只需换总线类型（in-process / Redis / Postgres），**Agent 代码无需修改**。

### 5.2 支持的 AI 服务（节选）

| 类别 | 代表服务 |
|---|---|
| **STT** | Deepgram、AssemblyAI、OpenAI Whisper、Groq、Google、Azure、AWS、ElevenLabs、Soniox、Speechmatics、NVIDIA、Mistral、FunASR、Moonshine、Sarvam、xAI |
| **LLM** | OpenAI、Anthropic、Gemini、DeepSeek、Mistral、Groq、Ollama、Fireworks、Together、Cerebras、OpenRouter、Perplexity、Qwen、AWS、Azure、NVIDIA NIM |
| **TTS** | ElevenLabs、Cartesia、OpenAI、Google、Azure、AWS、Deepgram、Hume、Inworld、MiniMax、Kokoro、Piper、Rime、Resemble、Speechify、Fish、xAI、XTTS |
| **语音到语音** | OpenAI Realtime、Gemini Multimodal Live、AWS Nova Sonic、Grok Voice Agent、Ultravox |
| **视频 / 数字人** | HeyGen、Tavus、Simli、LemonSlice |
| **视觉/图像** | fal、Google Imagen、Moondream |
| **音频处理** | Silero VAD、Krisp Viva、Koala、ai-coustics、RNNoise |
| **记忆 / 观测** | mem0 ｜ OpenTelemetry、Sentry |

**传输层**：Daily、LiveKit、SmallWebRTC、Vonage、FastAPI Websocket、WebSocket Server、WhatsApp；电话侧 Twilio、Telnyx、Plivo、Exotel、Genesys、Vonage。
**客户端 SDK**：JavaScript、React、React Native、Swift、Kotlin、C++、ESP32。

### 5.3 配套工具链

- **Pipecat CLI**：`pipecat init` 脚手架 + 监控 + 部署
- **Pipecat Flows**：结构化/状态机对话
- **Pipecat UI**：基于 shadcn 的语音 AI 组件库
- **Whisker**：实时调试器 ｜ **Tail**：终端仪表盘
- **Pipecat Cloud**：官方托管（亦可自托管）

---

## 6. 总结

- **是什么**：开源 Python 实时语音/多模态 AI Agent **编排框架**（BSD-2-Clause）。
- **核心模型**：Pipeline + Processor + Frame + Transport + 可插拔 Service。
- **能干什么**：网页语音助手、AI 电话客服、引导式对话、实时语音到语音、多模态、数字人、工具调用 Agent、知识库问答、多智能体。
- **怎么组装**：把 STT/LLM/TTS/上下文/工具按顺序串进 `Pipeline`，服务商可逐个替换而不动其余代码。
- **怎么用**：`pipecat init quickstart` → 填 Key → `uv run bot.py` → 浏览器对话；生产用 `pipecat cloud deploy`。
- **怎么参考**：`examples/` 下 30+ 官方示例覆盖上述几乎全部场景。

**它改变的不是语音能力本身，而是“如何把各种语音/AI 能力组装成一个低延迟、可扩展的实时对话系统”。**

---

## 附：使用成本参考

> 结论：**开发/接入成本很低，但运行成本不算低**——运行开销主要来自第三方 STT/LLM/TTS 服务商，与 Pipecat 本身无关。
> 下列价格为调研时（2026-09）参考值，实际以各服务商官网为准。

### A. 免费 / 极低成本的部分

- **框架本身**：BSD-2-Clause 开源，自托管零授权费。
- **开发效率**：`pipecat init quickstart` 约 5 分钟跑通；15 行代码即可搭起语音客服骨架。
- **Pipecat Cloud 免费项**：1:1 WebRTC 语音会话免费；并发不限额；Krisp VIVA 每月 1 万活跃分钟以内免费。

### B. 需要付费的部分（账单主体）

| 项目 | 参考单价 |
|---|---|
| Pipecat Cloud 托管 agent-1x（活跃 / 预留） | $0.01 / $0.0005 每分钟 |
| Pipecat Cloud 托管 agent-2x（语音+视频，活跃） | $0.02 / 分钟 |
| Pipecat Cloud 托管 agent-3x（活跃） | $0.03 / 分钟 |
| Daily WebRTC 语音+视频 传输 | $0.004 / 参会者分钟 |
| PSTN 电话呼入/呼出 | $0.018 / 分钟 |
| Daily SIP 呼入/呼出 | $0.003–0.02 / 分钟 |
| SIP Refer（转接第三方号码） | $0.20 / 次事件 |
| 录制（音频 / 音视频 / 存储） | $0.005 / $0.01349 / $0.003 每分钟 |
| **STT（Deepgram 等）** | 约 $0.004–0.01 / 分钟 |
| **TTS（Cartesia / Deepgram Aura / ElevenLabs）** | 约 $0.015–0.10 / 分钟 |
| **LLM（token 计费）** | 随对话长度变动 |
| **语音到语音实时模型**（OpenAI Realtime 等） | 实测约 $0.05–0.15 / 分钟 |

### C. 综合估算

- 自建「STT + LLM + TTS」管线：约 **$0.03–0.10 / 分钟**
- 直接用实时语音模型：约 **$0.06–0.15 / 分钟**
- 叠加电话接入（PSTN $0.018/分钟）后更高

即：**一通 10 分钟语音对话，成本大约几毛到一块多美元**（取决于选型与时长）。

### D. 降本要点

1. **选经济型服务**：TTS 用 Deepgram Aura（比 ElevenLabs 便宜约 40%）、STT 用 Deepgram Flux 等对话优化模型。
2. **控制上下文长度**：用 `context-summarization` 做摘要，避免长对话 token 膨胀。
3. **合理使用预留实例**：高频稳定业务用 reserved（$0.0005/分钟）替代 active（$0.01/分钟）。
4. **传输选型**：1:1 WebRTC 语音免费，优先于付费传输；仅在必须时用 PSTN。
5. **先小规模验证**：用每天少量分钟数跑通业务，再决定是否扩量。

---

## 附：自研 vs 用 Pipecat（什么时候值得用）

### 先分清两件事

- **"实现 TTS 本身"**：训练/自研语音模型、语音克隆——**Pipecat 不做，也不需要你做**，那是模型服务商（ElevenLabs、Cartesia 等）的活儿。
- **"把 TTS 实时编排起来"**：低延迟流式、可打断、多服务切换——**这才是 Pipecat 的核心价值**，也是自研成本最高的地方。

### 自研实时编排的真实工作量

| 能力 | 自研难度 | Pipecat |
|---|---|---|
| 打断处理（用户插话立刻停播 + 清空待播队列） | 极高，极易出 bug | 内置，经生产验证 |
| 双向流式（边说边合成、边合成边播放） | 高 | 现成管线 |
| VAD + 轮次检测（判断用户是否说完） | 中高 | 内置 Silero VAD + turn-management |
| 帧级低延迟调度（优先级队列、保序） | 高 | 框架保证 |
| 多服务商切换（Deepgram→Soniox、OpenAI→Anthropic） | 各自适配、重复劳动 | 换一个对象即可 |
| 上下文/token 管理、多代理、多渠道传输 | 各自造轮子 | 现成方案 |

> 自研这一整套通常需要**数周到数月**，且回声、自打断、延迟抖动等坑几乎必踩。

### Pipecat 不做的事

- 不实现 TTS 模型本身（不训练、不做语音克隆）
- 不提供电话线路/号码（需接 Twilio 等）
- 不保证端到端延迟（取决于所选服务商）

### 结论

> 在「把 TTS 等能力**实时、低延迟、可打断地编排成对话系统**」这件事上，Pipecat 的价值**远超自研**；
> 但「实现 TTS 本身」不是它的职责，也不需要你承担。
> 它省掉的是**编排与集成的工程**，而不是**语音模型本身**。

**判断口诀**：需要实时语音对话 + 想快速上线 + 不愿踩流式/打断的坑 → 用 Pipecat；只需纯文本、或要全离线自研模型 → 不需要它。

---

## 附：模型要求与两种架构（级联式 vs 端到端）

### 前置澄清

**Pipecat 本身是编排框架，不是模型**，所以它"需要什么模型"取决于你选的架构。**两种架构都支持，不是只支持一种**，且可混用。

### 架构 A：级联式（STT → LLM → TTS）

- **对模型要求低**：普通文本 LLM 即可（GPT-4.1、Claude、DeepSeek…），语音由独立的 STT / TTS 处理。
- 实时性由 **STT + TTS + Pipecat 框架**共同承担，LLM 只做纯文本推理，无需原生支持实时音频。
- 优点：可插拔、可换供应商、成本低、完全可控。
- 缺点：延迟是三段累加（通常 500–800ms），语调/情感表现较弱。

### 架构 B：端到端实时语音（语音到语音 / 全模态）

- **必须使用原生实时语音模型**，Pipecat 已内置以下支持（见 `examples/realtime/`）：

| 服务商 / 模型 | 说明 |
|---|---|
| **OpenAI Realtime** | 示例最多，唯一支持“客户端委派 + Responses API”，支持视频与纯文本 |
| **Google Gemini Live** | 示例最全（含 Google 搜索接地、Vertex、Files API、视频） |
| **xAI Grok** | 实时语音 + 异步工具调用 |
| **Ultravox** | 实时语音 + 纯文本模式 |
| **AWS Nova Sonic** | 实时语音 + 异步工具调用 |
| **Azure OpenAI Realtime** | 实时语音 + 异步工具调用 |
| **Inworld** | 基础实时语音 + 本地驱动轮次 |

- 优点：延迟更低（可 <500ms）、原生打断、语调更自然。
- 缺点：绑定单一供应商、成本高、可控性低（黑盒）。
- 注意：**视频输入仅 OpenAI Realtime 与 Gemini Live 支持**；异步工具调用则上述多数服务商都提供示例。

### 对照表

| 维度 | 级联式 | 端到端实时 |
|---|---|---|
| 需要的模型 | 普通文本 LLM + 独立 STT/TTS | 原生实时语音模型 |
| 延迟 | 500–800ms | 可 <500ms |
| 自然度 | 较好 | 最佳 |
| 可插拔性 | 极高 | 低 |
| 成本 | 低 | 高 |
| 工具调用 | 成熟 | 部分支持 |
| 视频输入 | 自行组合 | 仅 OpenAI / Gemini Live |
| 可控性 | 高 | 低 |

### 术语澄清

- **多模态（multimodal）**：模型能处理多种输入（文本 + 图像等）——Pipecat 支持大量此类 LLM（vision 示例）。
- **全模态（omni-modal）**：原生端到端处理语音/文本/图像/视频，语音进语音出——对应 `realtime/` 的 7 家。
- **Pipecat 定位**：本身是「**多模态编排框架**」，两类模型都能接，但它自己不生产智能。

### 选型结论

> **走级联式时，对模型的要求其实很低**——普通 LLM 即可，因为实时性由 STT/TTS + 框架承担。
> - 要**低延迟 + 自然打断 + 情感语调** → 端到端全模态（OpenAI Realtime / Gemini Live）
> - 要**低成本 + 可换供应商 + 完全可控** → 级联式（普通 LLM + 独立 STT/TTS）
> - 也可**混用**：Realtime 模型做对话 + 挂工具调用 + 挂视觉。

---

## 附：接入自研 / 自定义模型

### 关键前提

**Pipecat 不加载模型权重，它调用的是「服务（API）」**。所以能否"直接使用"，取决于模型以什么形式暴露。

| 模型形态 | 能否直接用 | 需要做什么 |
|---|---|---|
| **已有 OpenAI 兼容 API**（vLLM、Ollama、多数自建推理服务） | ✅ 几乎直接用 | 换 `base_url` + `api_key` + `model` |
| **自定义 HTTP/WebSocket API**（自有协议） | ⚠️ 需适配 | 写自定义 Service 包一层 |
| **只有本地模型权重** | ❌ 不能直接用 | 先用 vLLM/Ollama 起服务，再按第 1 条接入 |

### 最省事路径：OpenAI 兼容接口

只要模型服务兼容 OpenAI 的 chat completions 接口，改三个参数即可：

```python
llm = OpenAILLMService(
    api_key="<你的 key>",
    base_url="https://<你的域名>/v1",            # 指向自己的模型服务
    settings=OpenAILLMService.Settings(model="<你的模型 ID>"),
)
```

> 官方文档明确：`base_url` 用于代理或自托管部署的覆盖，许多第三方 LLM 提供 OpenAI 兼容 API，可直接用 `OpenAILLMService` 连接。
> 接入第三方时，未支持的参数建议保持 `NOT_GIVEN`（会从请求中省略），比显式传 `None` 更安全。

### 无兼容接口时：写自定义 Service

继承 Pipecat 的基类（`LLMService` / `STTService` / `TTSService`），实现 `process_frame` 与调用逻辑，把自有协议封装成 Pipecat 能识别的处理器。可参考 `examples/` 与官方「社区集成」指南。

### 还需要补齐哪些环节

| 你提供的模型 | 还需要什么 |
|---|---|
| 只有 LLM | STT + TTS + Transport（+ 可选 VAD） |
| 只有 STT | LLM + TTS + Transport |
| 只有 TTS | STT + LLM + Transport |
| 全模态实时语音模型 | 理论上只需 Transport（工具/视觉可选） |

### 运行时可动态调整

模型参数（temperature、max_tokens 等）可在会话中通过 `LLMUpdateSettingsFrame` 动态修改；也可用 **ServiceSwitcher** 在会话中途更换服务商。
