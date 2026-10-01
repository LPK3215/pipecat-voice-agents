# pipecat-quickstart（官方脚手架 + 魔搭 LLM + 全量日志）

由 **Pipecat 官方 CLI** 生成的语音 Agent，级联管线 `STT → LLM → TTS`。

**当前状态：前后端均已启动并实测通过，零报错。**

## 配置一览

| 项 | 值 | 需要 key |
|---|---|---|
| 前端 | 官方内置客户端 `/client/` | 否 |
| 传输 | SmallWebRTC（浏览器直连） | 否 |
| **STT** | **Whisper（本地）** | **否** |
| **LLM** | **魔搭 ModelScope（3 选 1）** | **是（唯一）** |
| **TTS** | **Piper（本地）** | **否** |

> **整个项目只需要填一个 `MODELSCOPE_API_KEY`。**
> STT / TTS 全部在本地运行，不产生任何云服务调用与费用。

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
[TURN] 识别文本: '你好，请用一句话介绍一下你自己。'
[TURN] → 发往 LLM 的上下文: [{'role': 'system', ...}, {'role': 'user', ...}]
[TURN] ← LLM 回答完毕: '你好，我是你的语音助手，很高兴为你服务。'
[TURN] TTS 开始出声
[TURN] 分段延迟（基准=说完）: 识别文本 556ms | 发往LLM 558ms | 首个答案token 1107ms | 首次出声 1257ms
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

实测输出（nex-N2.5-mini）：

```
语音结束 -> VAD 判定说完      373 ms
语音结束 -> STT 最终文本     1114 ms
语音结束 -> LLM 首 token    1542 ms
语音结束 -> TTS 首帧音频     1859 ms
识别文本 : '你好请用一句话接收一下,你自己。'
模型回复 : '你好，我是你的语音助手，很高兴为你服务。'
TTS 音频 : 162820 字节
判定: ✅ 全链路通过
```

三个模型均验证通过（管线内 LLM 首 token：nex 1542ms / Qwen 1963ms / deepseek 2054ms）。

---

## 相对官方模板的改动

全部集中在 `server/`，共 6 处：

| # | 改动 | 原因 |
|---|---|---|
| 1 | LLM：OpenAI → 魔搭 ModelScope | 只有 ModelScope 的 key；它是 OpenAI 兼容接口，服务类仍是官方的 `OpenAILLMService` |
| 2 | 新增 `extra_body={"enable_thinking": False}` | 关闭思考模式，Qwen/DeepSeek 快 3 倍 |
| 3 | Whisper 默认值改为 `base` | 官方默认 `...-medium.en` 是**纯英文**模型，中文须用多语种 |
| 4 | VAD `stop_secs` 0.2 → 0.6 | **关键**：官方默认会把一句中文按逗号停顿切成两段（详见下节） |
| 5 | 提示词中文化 | 官方英文 prompt 会被中文音色读得很难听 |
| 6 | 新增 `pipeline_logging.py` 与观测器 | 全量日志，跑一次即可定位问题 |

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
│   ├── pipeline_logging.py  # 日志与对话时间线观察者
│   ├── pyproject.toml       # 依赖（官方生成）
│   ├── .env.example         # 唯一需要填 MODELSCOPE_API_KEY
│   ├── .env                 # 真实密钥（已被 .gitignore 忽略）
│   └── logs/                # 运行时日志（已被 .gitignore 忽略）
├── verify_stack.py          # 端到端自检脚本
└── README.md
```

---

## 已知限制

1. **免费额度限流**：ModelScope 免费推理连续压测约 10 次触发
   `429 We have to rate limit you`。
2. **延迟抖动大**：免费共享端点，同一模型同一问题实测 386 → 1215 ms（3 倍）。
   管线内端到端实测约 **1.9–2.4 s**（直连 API 仅 0.7–0.8 s，差距来自端点排队）。
3. **Whisper `base` 准确度有限**：合成语音里「介绍」被听成「接收」；
   真实人声会好很多，追求准确度请换 `small` 以上。
4. **一条预期内的告警**：`VAD stop_secs (0.6s) differs from the recommended default (0.2s)`
   是 pipecat 的一次性信息提示，不是错误（详见上文「为什么必须改 VAD」）。
5. **仅 Web 传输**：只启用了 SmallWebRTC。
