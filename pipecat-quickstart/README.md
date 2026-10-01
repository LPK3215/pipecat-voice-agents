# pipecat-quickstart（官方脚手架 + 魔搭 LLM + 全量日志）

由 **Pipecat 官方 CLI** 生成的语音 Agent，级联管线 `STT → LLM → TTS`。

**当前状态：前后端均已启动并实测通过，零报错。**

> 📘 **[HANDBOOK.md — 语音对话 Agent 搭建手册](HANDBOOK.md)**
> 第一阶段总结：心智模型、从零复现步骤、四个模型插槽如何切换、实测数据、踩坑清单。
> 想「照着文档从零搭一遍」或做汇报，看这份；本文只讲**这个项目怎么跑**。
>
> 📗 **[HANDBOOK-02.md — 第二阶段：写自己的业务](HANDBOOK-02.md)**
> 工具开发规范、schema 描述怎么写、数据接入、记忆双层设计、编排、测试纪律、完整示例。
>
> 📕 **[TOOL_TESTS.md — 工具调用压测报告](TOOL_TESTS.md)**
> 实测结论与**可信度标注**（部分结论已作废）、未完成项与阻塞。
> 引用其中的数据前请先读顶部的作废声明。

## 配置一览

| 项 | 值 | 需要 key |
|---|---|---|
| 前端 | 官方内置客户端 `/client/` | 否 |
| 传输 | SmallWebRTC（浏览器直连） | 否 |
| **STT** | **SenseVoice（本地，默认）/ Whisper（可切换）** | **否** |
| **LLM** | **魔搭 ModelScope（3 选 1）** | **是（唯一）** |
| **TTS** | **Piper（本地，默认）/ Kokoro（可切换）** | **否** |

> **整个项目只需要填一个 `MODELSCOPE_API_KEY`。**
> STT / TTS 全部在本地运行，不产生任何云服务调用与费用。

**可选开关**（都在 `server/.env`，默认值已是最优）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `ENABLE_TOOLS` | `1` | 是否把工具（function calling）开放给 LLM；`0` 关闭 |
| `STT_ENGINE` | `sensevoice` | 语音识别引擎：`sensevoice`（中文最优）或 `whisper`（通用） |
| `TTS_ENGINE` | `piper` | 语音合成引擎：`piper`（低延迟）或 `kokoro`（音色好，+630ms） |
| `STT_LANGUAGE` | `zh` | 显式指定识别语种；不指定时自动猜语种会更慢更易错 |
| `LLM_DISABLE_THINKING` | `1` | 关闭模型「思考」模式（首 token 快约 3 倍） |
| `VAD_STOP_SECS` | `0.6` | 中文整句判定阈值（官方 0.2s 会切断句子） |
| `ALLOW_MISSING_KEY` | 未设置 | 设 `1` 跳过缺 key 的 fail-fast（仅调试前端/传输层时用） |

生成命令（可复现）：

```bash
pipecat init pipecat-quickstart -b web -t smallwebrtc -m cascade \
  --stt whisper_stt --llm openai_llm --tts piper_tts \
  --client-framework none --no-deploy-to-cloud --no-context-hub
```

---

## 快速开始

```bash
cd server
uv sync
cp .env.example .env      # 只需填 MODELSCOPE_API_KEY，其余已预设好
uv run bot.py             # 需要外网可访问时加：--host 0.0.0.0 --port 7860
```

浏览器打开 <http://localhost:7860/client>，点 **Connect**，允许麦克风即可说话。

---

## 可选模型（3 个，改一个环境变量即可切换）

改 `server/.env` 里的 `MODELSCOPE_MODEL` 即可，无需改代码。

| 模型 | 直连首 token | 说明 |
|---|---|---|
| **`nex-agi/Nex-N2.5-mini`** | **710 ms** | **默认，最快** |
| `Qwen/Qwen3.8-Flash-Next` | 787 ms | 中文表达自然 |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 817 ms | 推理稳 |

### 关键：关闭「思考」模式（`LLM_DISABLE_THINKING=1`）

Qwen 与 DeepSeek 默认会**先推理再回答**，实测这一步会吃掉 1.6–2.4 秒：

| 模型 | 默认（带思考） | 关闭思考后 | 提升 |
|---|---|---|---|
| `nex-agi/Nex-N2.5-mini` | 830 ms | **710 ms** | — |
| `Qwen/Qwen3.8-Flash-Next` | 2869 ms | **787 ms** | **3.6 倍** |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 2447 ms | **817 ms** | **3.0 倍** |

实现方式（`bot.py`）：pipecat 的 `OpenAILLMSettings.extra` 里的键会被**直接当 kwargs**
传给 `client.chat.completions.create()`，所以非标准参数必须用 OpenAI SDK 的 `extra_body` 包一层：

```python
settings=OpenAILLMService.Settings(
    model=MODELSCOPE_MODEL,
    extra={"extra_body": {"enable_thinking": False}},
)
```

> 语音场景不需要长篇推理，关掉后三个模型都进入 1 秒内，效果显著。

---

## 日志系统（核心）

**设计目标：跑一次就能从日志看清前端、后端、每次请求的全过程。**

每次运行会生成两个文件：

```
server/logs/bot-<时间戳>.log   本次运行完整历史（DEBUG 级，含 pipecat 内部细节）
server/logs/bot-latest.log     固定名，永远指向最近一次运行
```

日志按前缀分四类，直接 grep 即可定位：

| 前缀 | 内容 |
|---|---|
| `[BOOT]` | **本次实际生效的配置**：会话 ID、模型、思考开关、key 状态、STT/TTS、VAD 阈值、提示词、日志路径 |
| `[CLIENT]` | 前端事件：浏览器连接/断开、RTVI 消息、客户端信息（含用户标识） |
| `[TURN]` | 一轮对话的时间线：识别文本 → 发往 LLM 的上下文 → LLM 回答 → 开始出声 → 分段延迟 |
| `[FRAME]` | 关键帧流水（DEBUG 级）：VAD、机器人说话起止、管线错误 |

实际输出示例：

```
[BOOT]   会话 ID           = c601ecc1-8843-4aba-80ed-0fbb62d65c48
[BOOT]   LLM 模型          = nex-agi/Nex-N2.5-mini
[BOOT]   关闭思考模式          = True
[BOOT]   LLM Key         = 已设置 ✅ (ms-594…4dea)
[BOOT]   STT             = Whisper(base) 本地·无需 key
[BOOT]   TTS             = Piper(zh_CN-huayan-medium) 本地·无需 key
[BOOT]   VAD 说完阈值        = 0.6s（官方推荐 0.2s）

[CLIENT] 浏览器已连接 | id=xxx
[TURN] ─────── 第 1 轮对话开始 ───────
[TURN] 用户停止说话（VAD 判定说完）
[TURN] 识别文本: '你好请用一句话接收一下。你自己。'
[TURN] → 发往 LLM 的上下文: [{'role': 'system', ...}, {'role': 'user', ...}]
[TURN] ← LLM 回答完毕: '你好，我是你的语音助手，请告诉我你需要我做什么。'
[TURN] TTS 开始出声
[TURN] 分段延迟（基准=说完）: 识别文本 558ms | 发往LLM 560ms | 首个答案token 1091ms | 首次出声 1251ms
```

实现要点（`pipeline_logging.py`）：

- `ConversationLogger` 是 pipecat 的 `BaseObserver`，把帧翻译成人话。
  同一帧每跳都会被观察到，因此用 `frame.id` 去重。
- **坑**：pipecat 的 runner 启动时会 `logger.remove()` 清空所有日志出口
  （`pipecat/runner/run.py`），因此**会话建立后必须重新调用 `setup_logging()`**，
  否则日志只进控制台、不落盘。

---

## 端到端自检（不需要浏览器/麦克风）

验证脚本读取 `server/.env` 的**同一套配置**，把一段中文音频喂进管线：

```bash
cd server
uv run ../verify_stack.py                          # 用 .env 的配置
uv run ../verify_stack.py --model Qwen/Qwen3.8-Flash-Next   # 换模型对比
uv run ../verify_stack.py --stop-secs 0.2          # 复现"被切成两段"的问题
uv run ../verify_stack.py --whisper small          # 换更大 STT 模型
```

实测输出（nex-N2.5-mini，2026-10-01 复测，Whisper base）：

```
语音结束 -> VAD 判定说完      454 ms
语音结束 -> STT 最终文本     1012 ms
语音结束 -> LLM 首 token    1545 ms
语音结束 -> TTS 首帧音频     1705 ms
识别文本 : '你好请用一句话接收一下。你自己。'
模型回复 : '你好，我是你的语音助手，请告诉我你需要我做什么。'
TTS 音频 : 200270 字节
判定: ✅ 全链路通过
```

三个模型均验证通过（管线内 LLM 首 token：nex 1545ms / Qwen 1963ms / deepseek 2054ms）。

> **口径提醒**：本表的基准是「合成音频推流结束」，而 WAV 尾部带静音，
> VAD 在推流结束前约 0.45 s 就已判定说完（表中第一行），因此
> **真实体感端到端 ≈ 1.7 s + 0.45 s ≈ 2.1 s**。
> 数字只用于**横向对比**（换模型、改配置前后的差值），不要当成用户感知延迟的绝对值。

### 无浏览器冒烟测试

`verify_stack.py` 走完整链路（含 STT/LLM/TTS），较慢；
只想快速确认「前端能打开 + WebRTC 能握手 + 后端装配无错」时用：

```bash
cd server
uv run ../smoke.py            # 自动拉起 bot.py 再测，测完自动关闭
uv run ../smoke.py --no-spawn # 只测已经跑起来的实例
```

```
✅ GET /client/        -> HTTP 200（官方 Prebuilt 前端可访问）
✅ POST /api/offer     -> HTTP 200（WebRTC 握手成功）
✅ 未发现装配期硬错误
```

### 文本通道探针（不用麦克风驱动真实 bot.py）

`smoke.py` 只验证到握手为止。想验证**一整轮真实对话**、又不想开口说话时用：

```bash
cd server
uv run ../text_probe.py                            # 默认问「现在几点了？」
uv run ../text_probe.py --question "今天星期几"
```

它通过真实 WebRTC 数据通道发 RTVI `send-text` 消息 —— 与官方 Prebuilt 前端
点「发送」走的是**同一条路径**。实测输出（2026-10-01）：

```
前端收到的 RTVI 消息（次数）:
  bot-llm-text x31    metrics x23        bot-output x3
  user-llm-text x2    bot-transcription x2
  bot-tts-started/stopped x2             bot-started-speaking x2
  llm-function-call-started / -in-progress / -stopped  x1
工具 : [TOOL] get_current_time -> 现在是 2026年10月1日 星期四 21点50分
出声 : 58 次
```

这份输出同时回答了「前端不动的话能拿到什么」：字幕、延迟指标、说话状态、
工具调用过程，全部是现成的，后端不需要改前端一个字符。

### 工具调用自检

```bash
cd server
uv run ../verify_tools.py                          # 只测后端，不经过音频
```

直接驱动真实管线，检查「工具是否被调用 + 是否有最终回答」。

> **注意**：**模型是否调用工具是非确定性的** —— 同一个问题有时调用、
> 有时凭自身知识直接回答。因此通过条件是「拿到了最终回答」，
> 工具是否调用另行报告（见脚本输出）。

**踩过的两个坑**（都在 `verify_tools.py` 里有注释，写新工具时务必注意）：

1. **收集器必须放在 assistant 聚合器之前。**
   聚合器会把 `LLMTextFrame` 消化成 `LLMContextFrame` / `*TurnFrame`，
   **不再往下游转发文本帧**。放在它后面就永远收不到文本 ——
   表现是「工具正常执行、日志有结果，却一直等不到回答」，
   极易被误判成管线卡死。（标准 pipecat 管线里 TTS 也在聚合器之前，同理。）

2. **`result_callback` 必须显式传 `run_llm=True`。**
   `FunctionCallResultProperties.run_llm` 默认是 `None`（假值），
   不传就不会触发工具结果之后的那次 LLM 生成。见 `tools.py` 的 `_RESULT_PROPS`。

### 音频链路探针（真实音频进 → 真实音频出）

```bash
cd server
uv run ../audio_probe.py
```

把 WAV 通过**真实 WebRTC 音频轨**送进真正跑起来的 `bot.py`，
与浏览器麦克风走同一条路径：VAD → **SenseVoice** → LLM → Piper → 音频轨回传。
这是唯一覆盖到 STT 与 VAD 的自动化验证（文本通道会绕过它们两者）。

实测（2026-10-01，客户端侧计时，基准 = **用户说完的那一刻**）：

| 节点 | Whisper base（改前） | **SenseVoice（当前）** |
|---|---|---|
| 收到识别文本 | 1218 ms | **934 ms** |
| LLM 开始生成 | 1233 ms | **935 ms** |
| 收到首个答案 token | 1731 ms | **1424 ms** |
| TTS 开始合成 | 1842 ms | **1527 ms** |
| **机器人开始出声** | **2038 ms** | **1721 ms** |

后端日志独立测得的「首次出声」为 1160 ms，但它的基准是 **VAD 判定说完**
（比真实说完晚约 0.56 s）。1160 + 561 ≈ 1721 —— 两套口径互相印证。

> **测试音频是 Piper 合成的，不是真人语音**，且线上还会经过 Opus 压缩。
> 因此这里的识别结果是**保守下界**，真人通过麦克风输入通常会更好。
> 目前合成音频上「介绍」仍会被认成「接收」，但整句语义不受影响。

---

## ASR / TTS 引擎选型（都本地免费，可随时切换）

两个引擎**都本地运行、无需 key、零费用**，但中文表现差距很大。
用 `asr_bench.py` 量化（6 句中文，标准答案已知）：

```bash
cd server
uv run ../asr_bench.py                              # 只测 base（无下载）
uv run ../asr_bench.py --models base small sensevoice
```

| ASR 引擎 | 字错率 | 完全正确 | 单句耗时 |
|---|---|---|---|
| Whisper `base`（原方案） | 23.8% | 1/6 | 607 ms |
| Whisper `small` | 13.6% | 2/6 | 874 ms |
| **SenseVoice（当前默认）** | **10.2%** | **3/6** | **158 ms** |

SenseVoice 是**又快又准，不是取舍**：它非自回归（RTF ≈ 0.05），
比 `base` 快约 4 倍，字错率同时降到不到一半。

**TTS 则相反，是实打实的取舍**（同一句中文的首个音频块）：

| TTS 引擎 | 首块延迟 | 音色 |
|---|---|---|
| **Piper（当前默认）** | **76 ms** | 偏机械 |
| Kokoro `zf_xiaoxiao` | 709 ms | 明显自然（共 8 个中文音色） |

换 Kokoro 要多付约 630 ms。语音助手对「多久开口」敏感，故默认 Piper。

> **一句话总结：ASR 换引擎是净收益，TTS 换引擎是拿延迟换音色。**

---

## 关于「端到端语音模型」（全模态）

当前这条链路是**级联式多模态**：ASR → LLM → TTS。它已经是完整的
**语音进 / 语音出**，且上文实测端到端 2.0 s。

如果目标是**单一模型端到端**（音频直接进模型、音频直接出，省掉 ASR 与 TTS），
pipecat 1.12 提供了 `OpenAIRealtimeLLMService`（`services/openai/realtime/llm.py`）。
但有三点必须先想清楚：

1. 它依赖 **OpenAI 的 Realtime 接口**，不是 chat-completions；
2. **魔搭当前的 chat-completions 接口不支持音频输入** —— 只换模型名做不到；
3. 换成 OpenAI / Gemini Live 意味着同时换服务商、密钥与服务类，
   属于**接入方式变更**，不是配置改动。

结论：在「只用魔搭一个 key」的前提下，**级联式是唯一可行、且已实测跑通的方案**。

---

## 相对官方模板的改动

全部集中在 `server/`，共 12 处：

| # | 改动 | 原因 |
|---|---|---|
| 1 | LLM：OpenAI → 魔搭 ModelScope | 只有 ModelScope 的 key；它是 OpenAI 兼容接口，服务类仍是官方的 `OpenAILLMService` |
| 2 | 新增 `extra_body={"enable_thinking": False}` | 关闭思考模式，Qwen/DeepSeek 快 3 倍 |
| 3 | Whisper 默认值改为 `base` | 官方默认 `...-medium.en` 是**纯英文**模型，中文须用多语种 |
| 4 | VAD `stop_secs` 0.2 → 0.6 | **关键**：官方默认会把一句中文按逗号停顿切成两段（详见下节） |
| 5 | 提示词中文化 | 官方英文 prompt 会被中文音色读得很难听 |
| 6 | 新增 `pipeline_logging.py` 与观测器 | 全量日志，跑一次即可定位问题 |
| 7 | 新增 `settings.py` | 默认值与 `verify_stack.py` 共用，避免「测的」和「跑的」配置漂移 |
| 8 | 缺 `MODELSCOPE_API_KEY` 时 **fail-fast** | 早期只打一行 ERROR 就照常启动：浏览器能连上、握手也成功，但一开口必然没反应，看起来像网络故障。现在直接终止并给出填 key 的步骤 |
| 9 | Whisper 加 `initial_prompt="以下是普通话的句子。"` | base 模型会把中文转成繁体（「请」→「請」），实测已修 |
| 10 | 开场白角色 `developer` → `user` | **修掉了一个静默失败**（详见下节）：魔搭接口不认 `developer`，且它留在上下文里会让**后续每一轮都失败** |
| 11 | 新增 `tools.py`（function calling） | 后端能力的扩展点；前端无需改动，pipecat 以 `llm-function-call*` 消息推送，Prebuilt 前端自动渲染 |
| 12 | 新增故障上报（`ErrorObserver` → RTVI `error`） | 服务失败时前端原本毫无提示（连得上、握得手、但没反应）。现在错误同时写 `[ERROR]` 日志并推到前端 |

### 为什么必须改 VAD（第 4 点）

官方默认 `stop_secs=0.2` 把一句中文在逗号处判定为「说完」，STT 因此分段转写，
LLM 只收到半句就作答。实测日志：

```
30.650  Transcription: [你好请用一句话接收一下 ]   ← 只收到前半句
30.653  User stopped speaking
30.903  User started speaking                     ← 同一句又"重新开始"
31.952  Transcription: [你自己 ]                  ← 后半句
模型回复: 好的，我已经收到，请说。                  ← 因为输入是残缺的
```

改为 `0.6` 后：

```
全部分段 : ['你好请用一句话接收一下,你自己。 ']    ← 整句完整
模型回复 : 你好，我是语音助手，可以随时帮你解答问题、整理信息或陪你聊天。
```

代价：只多等约 0.2 s。

### 为什么开场白角色必须是 `user`（第 10 点）

这一条是**靠新加的故障上报才发现的**：会话一建立，日志里马上出现

```
[ERROR] OpenAILLMService#0 | invalid_request | 服务已不可用 | 异常=BadRequestError
Error code: 400 - {'error': {'code': 'invalid_request',
  'message': 'Unexpected message role.', ...}}
```

两个坑叠在一起：

1. 魔搭的 OpenAI 兼容接口**不接受 `developer` 角色** → `Unexpected message role`。
2. 这条消息会**留在上下文里**，于是后续每一轮都带着它一起发出去，
   结果是**每一轮都 400**。
3. 即便改成 `system` 也发不出去：该接口要求消息里**至少有一条 `user`**，
   否则 `No user query found in messages.`（开场时确实还没有用户说过话）。

所以开场白只能以 `user` 角色发出。

**为什么以前没发现**：这个失败是**静默**的 —— 浏览器连得上、WebRTC 握手也成功，
只是机器人从不开口。没有任何前端提示，也没有可定位的日志，
极易被误判成网络故障。它一直存在于已提交的版本里。

---

## 远程访问（云端 IDE / 容器环境）

若在云端容器里跑、用浏览器从外部访问，需要两步：

```bash
uv run bot.py --host 0.0.0.0 --port 7860
```

但**仅有端口转发还是听不到声音**，因为：
- SmallWebRTC 默认 `ice_servers = []`（无 STUN/TURN）
- 服务器只通告内网候选地址，外部浏览器到不了
- 端口转发通常只映射 TCP，转发不了 WebRTC 的 UDP

**三种解法：**

1. **在本地电脑跑**（最省事）：本机环回不涉及 NAT，一定能出声
2. **配 TURN 中继服务器**：
   ```bash
   uv run bot.py --host 0.0.0.0 \
     --ice-servers '[{"urls":"turn:你的地址:3478","username":"u","credential":"p"}]'
   ```
   注意：只加 STUN 无效，**必须是 TURN（中继）**
3. **改用 Daily 传输**：Daily 自带穿透，但需要 Daily 账号

---

## 项目结构

```
pipecat-quickstart/
├── server/
│   ├── bot.py               # 主程序（官方模板 + 上述改动）
│   ├── settings.py          # 默认配置的唯一来源（bot 与自检共用，防漂移）
│   ├── tools.py             # LLM 可调用的工具（新增能力只改这里）
│   ├── pipeline_logging.py  # 日志、对话时间线、故障上报（[ERROR] → 前端）
│   ├── pyproject.toml       # 依赖（官方生成）
│   ├── .env.example         # 唯一需要填 MODELSCOPE_API_KEY
│   ├── .env                 # 真实密钥（已被 .gitignore 忽略）
│   └── logs/                # 运行时日志（*.log 已被 .gitignore 忽略）
├── verify_stack.py          # 端到端自检（完整链路，较慢）
├── verify_tools.py          # 工具调用自检（只测后端）
├── asr_bench.py             # ASR 基准：多配置中文识别字错率 / 耗时对比
├── text_probe.py            # 文本通道探针（真实 bot.py + 真实 WebRTC）
├── audio_probe.py           # 音频链路探针（真实音频进 / 出，含 STT 与 VAD）
├── smoke.py                 # 无浏览器冒烟测试（前端 + 握手 + 装配）
└── README.md
```

> **为什么有 `settings.py`**：`bot.py` 与 `verify_stack.py` 需要同一批默认值
> （模型、VAD 阈值、提示词……）。若各写一份，改了一侧而没改另一侧，
> 自检结果就会失真——而这种漂移**不会报错**，只会让人对着错误数字做决策。

---

## 已知限制

1. **免费额度限流**：ModelScope 免费推理连续压测约 10 次触发
   `429 We have to rate limit you`。
2. **延迟抖动大**：免费共享端点，同一模型同一问题实测 386 → 1215 ms（3 倍）。
   管线内端到端实测约 **1.9–2.4 s**（直连 API 仅 0.7–0.8 s，差距来自端点排队）。
3. **Whisper `base` 准确度有限**：合成语音里「介绍」被听成「接收」；
   真实人声会好很多，追求准确度请换 `small` 以上。
   （繁体问题已通过 `initial_prompt` 修正，见「相对官方模板的改动」第 9 条。）
4. **一条预期内的告警**：`VAD stop_secs (0.6s) differs from the recommended default (0.2s)`
   是 pipecat 的一次性信息提示，不是错误（详见上文「为什么必须改 VAD」）。
5. **仅 Web 传输**：只启用了 SmallWebRTC。
