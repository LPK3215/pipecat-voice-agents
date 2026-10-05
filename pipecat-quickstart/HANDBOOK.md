# 语音对话 Agent 搭建手册

**第一阶段：把链路跑通、把每个插槽搞清楚**

> 定位：这是一份**从零复现文档**。目标是不看仓库代码，照着本文也能把
> 「语音进 / 语音出」的底层搭起来，并知道每个环节能换什么、怎么换、代价是什么。
>
> 配套：`README.md` 是「这个项目怎么跑」，本文是「这类东西怎么搭」。
>
> **➡️ 下一阶段见 [`HANDBOOK-02.md`](HANDBOOK-02.md)**：
> 在框架上写自己的业务 —— 工具开发规范、数据接入、编排、测试纪律、完整示例。

> **语言约定（本手册与本仓库全部文档适用）**：文档正文用中文；**代码、命令与终端输出
> 一律按原文展示（英文纯 ASCII）**，不做中文意译。例外：提示词、工具 `description` 与
> `spoken` 朗读文案属于功能内容，在代码里本来就是中文。

---

## 0. 先建立正确的心智模型

**对大模型而言，始终是文本对话。** 语音只是在两端各挂了一个转换器：

```
麦克风音频 ──[VAD 判断说完了没]──→ [ASR 语音→文字] ──→ 文本
                                                        │
                                              ┌─────────▼─────────┐
                                              │  LLM（文本进文本出）│  ← 你花钱的那个
                                              └─────────┬─────────┘
                                                        │
扬声器音频 ←──[TTS 文字→语音]───────────────────────── 文本
```

**推论**：加语音功能**不改变 LLM 的用法**。变的只是它前后多了两个编解码器，
以及中间那层流式编排（边生成边播、被打断怎么办、上下文怎么排）。
所有「语音」的复杂度都在**这两端和编排层**，不在模型本身。

### 四个模型插槽（不是一个）

| # | 环节 | 本项目用的 | 跑在哪 | 要 key 吗 |
|---|---|---|---|---|
| 1 | VAD 判断「说完了没」 | Silero VAD | 本地 | 不要 |
| 2 | ASR 语音→文字 | SenseVoice（原 Whisper base） | 本地 | 不要 |
| 3 | LLM 思考作答 | `nex-agi/Nex-N2.5-mini`（魔搭，可换 Qwen / DeepSeek） | 远程 | **要** |
| 4 | TTS 文字→语音 | Piper（Kokoro 可选） | 本地 | 不要 |

**钱只花在 3 号插槽上**，其余三个都是本地开源模型，免费、音频不出本机。

### 关键不对称：输入错了致命，输出错了只是难听

| 环节 | 出错后果 | 能挽回吗 |
|---|---|---|
| **ASR（输入）** | 错的文字喂给 LLM，后面全基于错误作答 | **不能，级联污染** |
| **TTS（输出）** | 发音难听、音色机械 | 语义没丢，只是体验差 |

所以优先级不是「输入更难做」，而是「**输入错了会毁掉整轮对话**」。
这决定了钱和时间该花在哪 —— 也解释了为什么第一阶段优化的是 ASR，不是 TTS。

### 四种通路同时存在，不是「选一种模式运行」

|  | 文字出 | 语音出 |
|---|---|---|
| **文字进** | 打字 → 屏幕字幕 | 打字 → 机器人出声 |
| **语音进** | 说话 → 屏幕字幕 | 说话 → 机器人出声 |

走哪条取决于用户此刻用嘴还是用键盘，四条通路在代码里**并存**。
本仓库的 `text_probe.py` 覆盖「文字进→语音出」，`audio_probe.py` 覆盖「语音进→语音出」。

---

## 1. 环境要求

- Python 3.12
- `uv`（本项目 venv 由 uv 管理，**没有 pip**，装包用 `uv pip install`）
- 首次运行会下载模型到本地缓存（Whisper / SenseVoice / Piper 各几百 MB）
- 需要外网（拉取模型 + 调 LLM）

## 2. 从零搭建：可复制的命令清单

```bash
# 1) 建项目、装依赖（Python 3.12）
mkdir -p my-voice-agent/server && cd my-voice-agent/server
uv init --python 3.12
uv add "pipecat-ai[openai,piper,runner,silero,webrtc,whisper]>=1.4.0"
```

```toml
# server/pyproject.toml 关键一行
dependencies = ["pipecat-ai[openai,piper,runner,silero,webrtc,whisper]>=1.4.0"]
```

```bash
# 2) 可选：升级为中文更强的 ASR（SenseVoice，即默认 STT_ENGINE）
#    本项目已把它声明为可选 extra，且 CPU 源已在 pyproject 里配好：
uv sync --extra sensevoice
#    若手工安装，务必显式指定 CPU 源，否则会拉 CUDA 版 torch（数 GB）：
#    uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
#    uv pip install funasr

# 3) 配置（只有 API KEY 必填）
cp .env.example .env
#    编辑 .env，填 MODELSCOPE_API_KEY

# 4) 起服务
uv run bot.py                      # 默认 7860；外网访问加 --host 0.0.0.0

# 5) 验证
uv run ../smoke.py                 # 自动拉起 bot.py 再测，测完自动关闭
uv run ../audio_probe.py           # 真实音频进 / 真实音频出（覆盖 VAD 与 ASR）
```

浏览器打开 `http://localhost:7860` 即可对话（需要麦克风权限）。

## 3. 目录结构

```
server/
├── bot.py              # 管线装配：把 VAD/STT/LLM/TTS 串起来（核心，最先读这个）
├── settings.py         # 默认值 + 本地服务构造（build_stt/build_tts）的唯一来源（自检与运行共用，保证「测的就是跑的」）
├── pipeline_logging.py # 日志：对话时间线 + 每轮分段延迟 + 工具调用六种结局 + 故障上报
├── pyproject.toml      # 依赖（含 sensevoice 可选 extra，CPU 源已配好）
├── .env / .env.example # 密钥模板（唯一必填：MODELSCOPE_API_KEY）
└── logs/               # 运行时日志（*.log 已被 .gitignore 忽略）

../ （仓库根，验证工具箱）
├── smoke.py            # 冒烟：起服务 + 基础连通性
├── verify_stack.py     # 全栈自检，可换模型 / 换参数对比
├── text_probe.py       # 文本通道探针（真实 bot.py + 真实 WebRTC）
├── audio_probe.py      # 音频链路探针（真实音频进 / 出，含 STT 与 VAD）
├── asr_bench.py        # 离线 ASR 基准：直接喂 WAV，秒级对比，改配置时用它
├── live_asr_bench.py   # 真实链路 ASR 基准：经 Opus 编解码，结论更可信
├── kb_eval.py          # 知识库检索质量评测：扫分块 / 重排 / 候选池（第二阶段用）
├── verify_tools.py     # 工具调用自检（只测后端）+ --repeat 成功率统计
├── verify_summarize.py # 上下文摘要自检：撑过阈值，验证框架真的触发压缩
├── prewarm.py          # 预热本地模型（首次运行前跑一次，避免首个会话卡在下载）
└── README.md / HANDBOOK.md / HANDBOOK-02.md / TOOL_TESTS.md   # 文档（本文即 HANDBOOK.md）
```

**第二阶段（写自己的业务）新增的文件** —— 第一阶段可以先不看，需要时见
[`HANDBOOK-02.md`](HANDBOOK-02.md)：

```
server/
├── tools.py            # function calling 注册中心 —— 业务能力的扩展点
├── sample_tools.py     # 示例工具集（天气 / 计算 / 换算 / 设备 / 通知 / 提醒）
├── memory.py           # SQLite：会话历史 + 长期记忆 + 业务表（含 TurnRecorder）
├── embeddings.py       # 文本嵌入（RAG 底座，本地 bge / OpenAI 兼容可切换）
├── knowledge.py        # 知识库：文档切块 + 向量检索
├── flows.py            # 显式编排：多步工具链（顺序由代码保证）
├── guards.py           # 可信性护栏：谎报执行检测 + 强制纠正
└── tests/              # pytest 单元测试（秒级、不联网）

../ ingest_docs.py      # 知识库写入侧（灌文档）
../ collect_orders.py   # 业务数据采集示例（采集与查询分离）
```

**设计约定**：`settings.py` 是唯一默认值来源，且 `build_stt/build_tts` 也放在这里，
自检脚本直接复用同一批默认值与同一套构造逻辑。
若各写一份，任何一侧改动都会让自检结果失真 —— 而这种漂移**不报错**，
只会让人对着错误的延迟数字做决策。

## 4. 四个插槽详解

### 4.1 VAD（Voice Activity Detection）

**作用**：判断「用户说完了没」。它决定了何时把音频送去识别。
**不是音量阈值，是个小神经网络**（Silero）。

**关键参数**：`VAD_STOP_SECS`（静音多久判定为说完）

| 值 | 效果 |
|---|---|
| 0.2（官方默认） | **中文会被逗号处的自然停顿切成两段**，LLM 只收到半句 |
| **0.6（本项目）** | 整句完整，代价是多等约 0.58s |
| 0.8 | 说话慢的人适用，更慢 |

**这是纯等待时间，不消耗算力** —— 想压延迟第一刀应该砍这里（0.6→0.45 约省 150ms，
代价是长句更容易被切断）。

### 4.2 ASR（语音→文字）—— 第一阶段主要优化对象

两种都**本地免费、无需 key**，但中文表现差距很大：

| 引擎 | 字错率 | 单句耗时 | 特点 |
|---|---|---|---|
| Whisper `base` | 23.8% | 607ms | 通用但中文弱，**倾向输出繁体**：**必须**配 `initial_prompt`（实测 41.3% → 23.8%） |
| Whisper `small` | 13.6% | 874ms | 更准但更慢，典型的准确/延迟取舍 |
| **SenseVoice（默认）** | **10.2%** | **158ms** | 非自回归，专为中文等多语种设计 |

**SenseVoice 又快又准，不是取舍。** 这是本次升级的核心结论 ——
原本以为本地升级会更慢，实测相反（非自回归架构，RTF≈0.05）。

切换：`STT_ENGINE=sensevoice | whisper`

> **是否要上讯飞/阿里云/Deepgram？**
> 中文准确率上，开源（SenseVoice 是阿里开源的，不是玩具）和付费的差距**比想象小**。
> 付费服务的真正优势是**流式**：边说边出字，你说完最后一个字时文字基本就出来了。
> 而本地方案是「等 VAD 判完 → 整句一次性识别」，天然带一段等待。
> 所以花钱买的主要是「省掉等待 + 不吃本地 CPU + 免运维」，其次才是准确率。

### 4.3 LLM（思考作答）—— 唯一花钱的插槽

**兼容 OpenAI 协议即可**，这也是本项目用 `OpenAILLMService` 却能调魔搭的原因：
换厂商时改 `base_url` 和 `api_key` 两个字符串，代码一行不动。

实测「关闭思考模式后」首 token 延迟（三个都已实测 < 1s）：

| 模型 | 首 token |
|---|---|
| `nex-agi/Nex-N2.5-mini`（默认） | 710 ms |
| `Qwen/Qwen3.8-Flash-Next` | 787 ms |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 817 ms |

**关闭「思考」模式是本项目最关键的一步调参**：Qwen/DeepSeek 默认先推理再回答，
首 token 从约 2.5s 降到约 0.8s（快 3 倍）。语音场景不需要长篇推理。

实现细节：非标准参数必须用 OpenAI SDK 的 `extra_body` 包一层，
否则会被当成未知关键字参数报错（见 `settings.py::build_llm_extra`）。

### 4.4 TTS（文字→语音）

| 引擎 | 首个音频块 | 特点 |
|---|---|---|
| **Piper（默认）** | 76ms | 音色偏机械，几乎不增加延迟 |
| Kokoro | 709ms | 音色明显自然（有中文音色，如 `zf_xiaoxiao`），代价 +630ms |

**这是真实的取舍**（不像 ASR 那样能又快又好）。语音助手对「多久开口」很敏感，
因此默认 Piper。想要更好音色：`TTS_ENGINE=kokoro`。

## 5. 环境变量速查

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `modelscope` | LLM 服务商：`modelscope`（魔搭）/ `sensenova`（商汤日日新，`https://token.sensenova.cn/v1`）/ `suanli`（共绩算力，`https://api.suanli.cn/v1`） |
| `MODELSCOPE_API_KEY` | **必填** | `LLM_PROVIDER=modelscope` 时的密钥 |
| `SENSENOVA_API_KEY` | — | `LLM_PROVIDER=sensenova` 时的密钥（形如 `sk-...`） |
| `SENSENOVA_MODEL` | `sensenova-6.8-flash-lite` | 商汤模型 ID（可选 `deepseek-v4-flash` / `glm-5.2` / `kimi-k3` 等） |
| `SUANLI_API_KEY` | — | `LLM_PROVIDER=suanli` 时的密钥 |
| `SUANLI_MODEL` | `qwen/qwen3.8-27b` | 共绩模型 ID（形如「厂商/模型」，以控制台为准） |
| `MODELSCOPE_BASE_URL` | `https://api-inference.modelscope.cn/v1` | OpenAI 兼容端点 |
| `MODELSCOPE_MODEL` | `nex-agi/Nex-N2.5-mini` | 上表三选一 |
| `LLM_DISABLE_THINKING` | `1` | 关思考，首 token 2.5s→0.8s |
| `ENABLE_TOOLS` | `1` | 是否开放 function calling |
| `STT_ENGINE` | `sensevoice` | `sensevoice` / `whisper` |
| `STT_LANGUAGE` | `zh` | 显式指定，避免自动猜语种 |
| `WHISPER_MODEL` | `base` | 仅 whisper 引擎生效；**不能用官方默认的 `.en` 模型**（纯英文） |
| `TTS_ENGINE` | `piper` | `piper` / `kokoro` |
| `PIPER_VOICE_ID` | `zh_CN-huayan-medium` | 中文音色 |
| `VAD_STOP_SECS` | `0.6` | 见 4.1 |
| `KOKORO_VOICE_ID` | `zf_xiaoxiao` | 仅 `TTS_ENGINE=kokoro` 时生效 |
| `ALLOW_MISSING_KEY` | 未设置 | 设 `1` 跳过缺 key 的 fail-fast（仅调试前端/传输层） |
| `SYSTEM_INSTRUCTION` | 见 settings.py | 强调「会被朗读，别用 markdown/emoji」 |
| `TOOLS_EXCLUDE` | — | 逗号分隔，临时摘掉若干工具（按需分组加载，第二阶段） |
| `MEMORY_DB` | `server/data/memory.db` | 会话历史 / 长期记忆 / 业务表 |
| `EMBEDDING_PROVIDER` | `local` | `local`（本地 bge，无需 key）/ `api`（OpenAI 兼容 `/v1/embeddings`） |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | 仅 `local` 生效 |
| `KNOWLEDGE_DB` | `server/data/knowledge.db` | 知识库（与业务库分开管理） |

引擎名写错会**静默退回默认值**（不是报错）—— 少一个可选依赖或拼错都不该让服务起不来。

## 6. 验证工具箱：什么时候用哪个

| 场景 | 工具 |
|---|---|
| 改了配置/逻辑，先跑快速回归 | `uv run pytest`（单元测试，秒级、不联网） |
| 改了配置，想快速确认没跑偏 | `smoke.py`（自动拉起 + 关闭） |
| 想量化改某个旋钮的效果 | `verify_stack.py --model X` / `--stop-secs 0.2` |
| 改 ASR 配置，秒级看效果 | `asr_bench.py`（离线，直接喂 WAV） |
| 要下最终结论 | `live_asr_bench.py`（真实链路） |
| 端到端是否真的通 | `audio_probe.py`（**唯一覆盖 VAD 与 STT 的探针**） |
| 工具调用是否可用 | `verify_tools.py`（含 `--repeat N` 成功率统计） |
| 上下文摘要是否真的触发 | `verify_summarize.py`（把上下文撑过阈值，等框架的压缩回调） |
| 调知识库检索质量 | `kb_eval.py`（真实文档语料 + 手写用例，扫分块/重排参数） |

> **重要教训**：`asr_bench.py`（离线）比 `live_asr_bench.py`（真实链路）**偏乐观**。
> 真实路径要过 Opus 编解码和多次重采样，比直接喂 WAV 更难认。
> 曾出现「离线满分、真实链路仍错」的情况，**最终结论必须以真实链路为准**。

## 7. 可观测性：日志规范（**框架自带的，不要自己写**）

> 这一节是踩过坑之后补的。核心教训一句话：
> **动手写日志组件之前，先把 `pipecat/observers/` 目录翻一遍。**

### 7.1 框架自带的可观测组件

| 组件 | 作用 |
|---|---|
| **`FunctionCallObserver`** | **工具调用的完整生命周期** —— 六个时刻，见 7.2 |
| `ErrorObserver` | 错误分类（错误类别 / 该处理器是否还可用），可据此推送前端 |
| `LLMLogObserver` | LLM 进出的帧流水（DEBUG 级） |
| `MetricsLogObserver` | 各服务的 TTFB / TTFA 等指标 |
| `TranscriptionLogObserver` | 转写文本 |
| `DebugLogObserver` | 通用帧流水（DEBUG） |
| `SpeakingObserver` / `TurnTrackingObserver` / `UserBotLatencyObserver` / `StartupTimingObserver` / `ServiceMetricsObserver` | 说话状态、轮次跟踪、端到端延迟、启动耗时、服务指标 |

**规范 1：先查 `observers/`，再动手写。**
本项目早期在 `ConversationLogger` 里手写了一套「监听四个工具帧」的逻辑，
而框架早就提供了 `FunctionCallObserver` —— 白写一遍，还写漏了。

> 本仓库保留的 `ConversationLogger` 并非重复造轮子：它负责的是
> **对话时间线 + 每轮分段延迟**（INFO 级、面向「一次运行就能看懂发生了什么」），
> 框架没有等价实现。工具调用那部分已交回框架。

### 7.2 工具调用：一律用 `FunctionCallObserver`

```python
from pipecat.observers.function_call_observer import (
    FunctionCallEventKind,
    FunctionCallObserver,
)

# ⚠️ include_results 默认是 False（只记参数、不记结果），排查必须显式打开
observer = FunctionCallObserver(include_results=True)

@observer.event_handler("on_function_call_event")
async def on_function_call_event(observer, event):
    logger.info(f"{event.kind} {event.function_name} args={event.arguments}")

# 然后加进 PipelineWorker(observers=[...])
```

它把一次调用分成**六个时刻**，比「成功/失败」两分法完整得多：

| 时刻 | 含义 |
|---|---|
| `STARTED` | 模型要求调用 |
| `IN_PROGRESS` | 真正开始执行（**与上一个时刻可能相隔很久**：并发受限时要排队） |
| `COMPLETED` | 正常返回 |
| `FAILED` | 处理函数抛异常 |
| `TIMED_OUT` | 超过截止时间 |
| `CANCELLED` | 被取消（用户打断，或模型自己撤回） |

**为什么必须区分这四种结局**：`TIMED_OUT` 和 `CANCELLED` 正是
「工具明明调了、却永远没有结果」的元凶 —— 只看成功/失败会完全漏掉它们。

另外，事件里直接带了 `started_at` / `in_progress_at`，**一次记录就能读出
「排队等了多久 + 执行了多久」两段时间**，不必自己记时钟。

### 7.3 三个必须知道的坑

**坑 1：`include_results` 默认 `False`。**
框架的选择有道理（结果可能是 provider 决定的任意内容），但对排查来说
「只有参数没有结果」等于少了一半信息。**必须显式传 `True`。**

**坑 2：广播帧会到两次，必须去重。**
`FunctionCallsStartedFrame` / `FunctionCallInProgressFrame` /
`FunctionCallResultFrame` / `FunctionCallCancelFrame` 都由 `broadcast_frame` 发出，
而它会为**上行、下行各创建一个 Frame 实例**，两个实例 `frame.id` 不同、
却都会被观察者判定为「首次推送」（去重是按 `frame.id` 做的）。

框架的标准处理方式（见 `function_call_observer.py` 注释原文）：

```python
# These frames are broadcast, arriving as two frames, each pushed for the
# first time once. Read the downstream one.
if frame.broadcast_sibling_id is not None and data.direction != FrameDirection.DOWNSTREAM:
    return
```

**只读 DOWNSTREAM 那一个**即可，无需自己维护去重集合。
（`LLMLogObserver` 里是反过来只读 UPSTREAM 的，两种都对，选一边就行。）

**坑 3：不要替框架字段下结论。**
`FunctionCallResultFrame.run_llm` 看起来像「是否触发下一轮生成」，
但**实测与实际行为不一致**（值为 `False` 时下一轮照样发生）。
把它翻译成结论会写出**误导性日志 —— 比不写更糟**。
只报字段原值，让读日志的人自己判断。

### 7.4 日志内容规范（与用哪个组件无关，都要守）

1. **禁止截断。** 截断过的日志在排查时等于没有。
   典型反面案例：只保留上下文最后 600 字符 —— 恰好切掉开头的**系统提示词
   与注入的长期记忆**，而那正是查「模型为什么这么答」最需要的。
2. **上下文要完整落盘**，并附上**本轮可用工具清单**。
   模型选错工具时，第一件要确认的就是「它当时到底看得到哪些工具」。
3. **工具的 `tool_calls` 字段要展开**，否则「模型调了什么」在上下文里是空的。
4. **失败路径用 `warning` 级**，这样查「为什么没回答」时可以直接过滤出来。
5. **一个事件只记一次。** 权威记录交给组件，工具内部不要重复回显
   （本项目曾让同一件事在 INFO 里出现两遍，噪音很大）。
6. **日志文案一律英文纯 ASCII。** 中文与特殊字符（框线、emoji、箭头）在非 UTF-8
   控制台（如中文 Windows 的 GBK）下会抛编码异常或显示乱码。且日志文案是**跨文件契约**：
   `text_probe.py` / `audio_probe.py` / `live_asr_bench.py` / `smoke.py` 都用正则解析它，
   改文案必须同步改解析方。提示词、工具的 `description` 与 `spoken` 朗读文案属于**功能内容**，
   保持中文不变。

### 7.5 自查清单（搭新项目时照做）

- [ ] 翻一遍 `pipecat/observers/`，确认没有现成的可用
- [ ] `FunctionCallObserver(include_results=True)` 已加入 `observers=[...]`
- [ ] 工具调用有六种结局的记录，`TIMED_OUT` / `CANCELLED` 能看到
- [ ] 上下文完整落盘（不截断），含可用工具清单
- [ ] 广播帧没有重复记录
- [ ] 工具实现里没有重复的回显日志
- [ ] 日志文案全为英文纯 ASCII，且解析它的探针脚本已同步更新

---

## 8. 实测数据（可直接用于汇报）

**端到端延迟**（客户端侧计时，基准 = **用户说完的那一刻**）：

| 节点（探针输出标签） | Whisper base | SenseVoice（当前默认） |
|---|---|---|
| `user transcript`（收到识别文本） | 1218 ms | **934 ms** |
| `LLM started`（LLM 开始生成） | 1233 ms | **935 ms** |
| `first answer token`（收到首个答案 token） | 1731 ms | **1424 ms** |
| `TTS started`（TTS 开始合成） | 1842 ms | **1527 ms** |
| **`bot speaking`（机器人开始出声）** | **2038 ms** | **1721 ms** |

> 探针的输出标签是**英文纯 ASCII**（项目约定：代码内的终端输出与注释一律英文纯 ASCII，
> 避免非 UTF-8 控制台编码问题；见 7.4 第 6 条），上表括号内为中文对照。

**时间花在哪**（约 2s 的构成）：

- VAD 判定说完 **0.58s** —— 纯等待，可调
- ASR 识别 **0.47s**（原 0.63s）—— 本地 CPU
- LLM 首 token **0.43–0.5s** —— 只有这 0.5s 走网络
- TTS 合成 **0.25–0.3s** —— 本地

**关键判断**：LLM 只占约 1/4，其余都是本地推理。
接工具后会多一轮 LLM 推理 + 工具本身耗时，**基础设施这 2s 不会膨胀**，大致到 3s。
（人类对话节奏是 0.5–1s，所以 2s 属「能用但不算跟手」。）

## 9. 踩过的坑（按价值排序）

1. **开场白用了 `developer` 角色 → 每轮 400 静默失败。**
   魔搭接口不认该角色，而浏览器界面**没有任何提示**，看着像「连上了但没反应」。
   也就是说修复前这套流程**一次都没真正成功过**。改为 `user` 后通过。
   —— 这是本阶段最有价值的发现：**静默失败比报错危险得多**。

2. **官方默认 STT 模型 `Systran/faster-distil-whisper-medium.en` 是纯英文的**，
   中文对话会输出英文译文。中文场景必须换成多语种模型（`base`/`small`/...）。

3. **Whisper 未指定 `language` 时会自动猜语种**，既更慢也更易错（实测 607ms→370ms）。
   另外 `base` 倾向输出繁体，需给一句普通话 `initial_prompt` 拉回简体
   （实测 CER 41.3% → 23.8%，逐句可见 `這個月的校獸額` 这类繁体输出）。
   显式指定 `language="zh"` **不改变准确率**，但每句快约 250ms（实测 424ms vs 671ms）
   —— 这才是 bot 默认 `zh` 的理由，不要误以为它提升精度。

4. **function calling 的 `run_llm=True` 必须显式传。**
   `FunctionCallResultProperties.run_llm` 默认是 `None`（假值），不传就**不会触发
   工具结果之后的那次 LLM 生成** —— 表现是工具正常执行、日志有结果，
   但机器人永远不回答，直到超时。**静默失败，无报错无异常。**

5. **VAD `stop_secs=0.2` 会把一句中文按逗号切成两段**，LLM 只收到半句。

6. **funasr 依赖 torch**，直接装会拖进 CUDA 版（几 GB）。
   本项目已用 `uv sync --extra sensevoice` + `pyproject.toml` 里的 CPU 源解决；
   手工装时务必用 CPU 源：`uv pip install torch --index-url https://download.pytorch.org/whl/cpu`。

7. **探针端口不一致会静默拿不到结果** —— 连到没有服务的默认端口，
   现象是「跑完了但什么都没测到」。服务地址须与 bot 实际监听端口一致。

8. **venv 由 uv 管理时没有 pip**，用 `uv pip install`。

9. **样本量为 1 的 A/B 对比，得到的是噪声，不是规律。**
   实测教训：曾用「10 个工具 vs 5 个工具**各跑一次**」得出「工具越多越不调」，
   并写进了报告。用重复试验重测后被推翻 —— 减到 5 个工具并未变好，
   减到 3 个工具时反而调用了错误的工具。
   缺省采样温度下模型每次结果不同，**这类实验至少要重复 5 次看分布**。
   （见 `TOOL_TESTS.md` 第 6 节）

10. **先确认测量工具是对的，再去测被测对象。**
    同一个症状「机器人没好好回答」，背后可能是完全不同的原因：
    模型真的没调工具 / 请求失败（欠费、429）/ 参数传错 / 压根没记日志。
    **四种情况的排查方向完全相反**，而表象一模一样。
    实测教训：验证脚本曾把 `429 insufficient balance` 报成
    「模型没有调用工具（自行作答）」，于是把一次**环境故障**读成了**模型能力问题**，
    差点据此去换模型。

    > **对策 —— 每次实验前先跑一次「阳性对照」**：
    > 挑一个必然触发工具的问题（如「现在几点了」）先跑一遍。
    > 不通，就是环境问题，别急着下结论。

11. **VAD 的 0.6s 与 STT 的 p99 会埋一个「5 秒兜底」的坑（2026-10-05 实测新增）。**
    框架的默认轮次结束策略是 `TurnAnalyzerUserTurnStopStrategy`（本地 SmartTurn 模型）。
    它会检查 `VAD stop_secs` 与 STT 的 p99 延迟：当 `stop_secs（0.6s） >= p99（SenseVoice 0.5s）`
    时，内部等待时间会**塌缩为 0**，日志直接告警
    `may cause delayed turn detection specified by the user_turn_stop_timeout`。
    而 `user_turn_stop_timeout` 的**框架默认值是 5.0 秒** —— 一旦 SmartTurn 判出 `INCOMPLETE`
    （实测这句普通话置信度只有 31%），轮次就一直等到 5 秒兜底才结束，
    用户感知就是「**说完了，它愣 5 秒才回应**」。

    实测（`verify_stack.py`，2026-10-05，商汤模型）：修前 `LLM first token 6609ms`，
    修后 **2038ms**；同一条音频走真实 bot（`audio_probe.py`）是 1862ms。

    **对策要分两侧看，别一刀切：**

    - **产品侧（`bot.py`）不要乱调兜底。** SmartTurn 说 `INCOMPLETE` 的含义是
      「用户可能还没说完」—— 把兜底调小会让长句被提前打断，正好破坏 4.1 节的 0.6s 初衷。
      真实 bot 走 WebRTC 时这条音频被判为完整，端到端正常。
    - **测量侧（`verify_stack.py`）必须显式限制。** 它喂的是一条**固定完整语句**，
      不该等轮次检测器表态，所以显式传 `user_turn_stop_timeout=0.5`
      （见脚本里的 `HARNESS_TURN_STOP_TIMEOUT`）。否则测出来的**每个阶段都虚高 5 秒**，
      而这个脚本的职责恰恰是给出可信的分段延迟。

## 10. 常见改动对照表

| 我想…… | 改哪里 |
|---|---|
| 换个 LLM | `.env` 里 `MODELSCOPE_MODEL`（或换 `base_url` + key，代码不动） |
| 换个语音识别 | `STT_ENGINE` |
| 换个音色 | `TTS_ENGINE` / `PIPER_VOICE_ID` |
| 让机器人更早开口 | `VAD_STOP_SECS` 调小 |
| 让回答更短 | `SYSTEM_INSTRUCTION` |
| **加业务能力** | `server/tools.py` ← 第二阶段的入口 |

## 11. 下一步

- **第二阶段（写自己的业务）**：**已单独成文 → [`HANDBOOK-02.md`](HANDBOOK-02.md)**
  包含：框架的六个业务扩展点（另有护栏与摘要两处横切观察者）、加工具的完整步骤与四条硬规范、
  schema 描述怎么写（决定模型会不会调）、数据接入（采集与查询必须分开）、
  记忆的双层设计、什么时候不能让模型自由决定、测试纪律、完整示例、自查清单。
  进度与待办见该文第 10 节。

- **第三阶段（融入大系统）**：pipecat 的传输层可替换（WebRTC / WebSocket / Daily 等），
  `bot.py` 里的管线装配与传输解耦，因此可整体嵌入既有 Python 服务。

---

## 附：本阶段的一句话结论

**语音对话的本质是「文本对话 + 两端编解码 + 流式编排」。**
四个模型插槽独立可换，钱只花在 LLM 上；真正的瓶颈在 ASR（错了会污染整轮），
而中文场景下**免费开源方案（SenseVoice）已经能同时做到更快更准**。
框架省掉的是把组件粘起来的那层工程（WebRTC 音频帧、流式传输、打断处理、
上下文管理、错误传播），这部分自己撸是几周量级，且坑全在细节里。
