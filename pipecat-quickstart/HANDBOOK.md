# 语音对话 Agent 搭建手册

**第一阶段：把链路跑通、把每个插槽搞清楚**

> 定位：这是一份**从零复现文档**。目标是不看仓库代码，照着本文也能把
> 「语音进 / 语音出」的底层搭起来，并知道每个环节能换什么、怎么换、代价是什么。
>
> 配套：`README.md` 是「这个项目怎么跑」，本文是「这类东西怎么搭」。

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
| 3 | LLM 思考作答 | Qwen 系（魔搭） | 远程 | **要** |
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
# 2) 可选：升级为中文更强的 ASR
#    ⚠️ 必须指定 CPU 源。用默认源会拉 CUDA 版 torch（数 GB）；CPU 版约 0.7GB
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
uv pip install funasr

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
├── settings.py         # 所有默认值的唯一来源（自检与运行共用，保证「测的就是跑的」）
├── tools.py            # function calling 工具 —— 业务能力的扩展点（第二阶段的入口）
├── pipeline_logging.py # 分段耗时日志
└── .env                # 唯一必填：MODELSCOPE_API_KEY

../ （仓库根，验证工具箱）
├── smoke.py            # 冒烟：起服务 + 基础连通性
├── verify_stack.py     # 全栈自检，可换模型 / 换参数对比
├── text_probe.py       # 文本通道探针（真实 bot.py + 真实 WebRTC）
├── audio_probe.py      # 音频链路探针（真实音频进 / 出，含 STT 与 VAD）
├── asr_bench.py        # 离线 ASR 基准：直接喂 WAV，秒级对比，改配置时用它
├── live_asr_bench.py   # 真实链路 ASR 基准：经 Opus 编解码，结论更可信
└── verify_tools.py     # 工具调用自检（只测后端）
```

**设计约定**：`settings.py` 是唯一默认值来源，自检脚本直接复用同一批默认值。
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
| Whisper `base` | 23.8% | 607ms | 通用但中文弱，**倾向输出繁体** |
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
| `MODELSCOPE_API_KEY` | **必填** | 唯一必须自己填的 |
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
| `SYSTEM_INSTRUCTION` | 见 settings.py | 强调「会被朗读，别用 markdown/emoji」 |

引擎名写错会**静默退回默认值**（不是报错）—— 少一个可选依赖或拼错都不该让服务起不来。

## 6. 验证工具箱：什么时候用哪个

| 场景 | 工具 |
|---|---|
| 改了配置，想快速确认没跑偏 | `smoke.py`（自动拉起 + 关闭） |
| 想量化改某个旋钮的效果 | `verify_stack.py --model X` / `--stop-secs 0.2` |
| 改 ASR 配置，秒级看效果 | `asr_bench.py`（离线，直接喂 WAV） |
| 要下最终结论 | `live_asr_bench.py`（真实链路） |
| 端到端是否真的通 | `audio_probe.py`（**唯一覆盖 VAD 与 STT 的探针**） |
| 工具调用是否可用 | `verify_tools.py` |

> **重要教训**：`asr_bench.py`（离线）比 `live_asr_bench.py`（真实链路）**偏乐观**。
> 真实路径要过 Opus 编解码和多次重采样，比直接喂 WAV 更难认。
> 曾出现「离线满分、真实链路仍错」的情况，**最终结论必须以真实链路为准**。

## 7. 实测数据（可直接用于汇报）

**端到端延迟**（客户端侧计时，基准 = **用户说完的那一刻**）：

| 节点 | Whisper base | SenseVoice |
|---|---|---|
| 收到识别文本 | 1218 ms | — |
| LLM 开始生成 | 1233 ms | — |
| 收到首个答案 token | 1731 ms | — |
| TTS 开始合成 | 1842 ms | — |
| **机器人开始出声** | **2038 ms** | **1832 ms** |

**时间花在哪**（约 2s 的构成）：

- VAD 判定说完 **0.58s** —— 纯等待，可调
- ASR 识别 **0.47s**（原 0.63s）—— 本地 CPU
- LLM 首 token **0.43–0.5s** —— 只有这 0.5s 走网络
- TTS 合成 **0.25–0.3s** —— 本地

**关键判断**：LLM 只占约 1/4，其余都是本地推理。
接工具后会多一轮 LLM 推理 + 工具本身耗时，**基础设施这 2s 不会膨胀**，大致到 3s。
（人类对话节奏是 0.5–1s，所以 2s 属「能用但不算跟手」。）

## 8. 踩过的坑（按价值排序）

1. **开场白用了 `developer` 角色 → 每轮 400 静默失败。**
   魔搭接口不认该角色，而浏览器界面**没有任何提示**，看着像「连上了但没反应」。
   也就是说修复前这套流程**一次都没真正成功过**。改为 `user` 后通过。
   —— 这是本阶段最有价值的发现：**静默失败比报错危险得多**。

2. **官方默认 STT 模型 `Systran/faster-distil-whisper-medium.en` 是纯英文的**，
   中文对话会输出英文译文。中文场景必须换成多语种模型（`base`/`small`/...）。

3. **Whisper 未指定 `language` 时会自动猜语种**，既更慢也更易错（实测 607ms→370ms）。
   另外 `base` 倾向输出繁体，需给一句普通话 `initial_prompt` 拉回简体。

4. **function calling 的 `run_llm=True` 必须显式传。**
   `FunctionCallResultProperties.run_llm` 默认是 `None`（假值），不传就**不会触发
   工具结果之后的那次 LLM 生成** —— 表现是工具正常执行、日志有结果，
   但机器人永远不回答，直到超时。**静默失败，无报错无异常。**

5. **VAD `stop_secs=0.2` 会把一句中文按逗号切成两段**，LLM 只收到半句。

6. **funasr 依赖 torch**，直接装会拖进 CUDA 版（几 GB）。
   必须用 CPU 源：`uv pip install torch --index-url https://download.pytorch.org/whl/cpu`。
   装前先 `uv pip install --dry-run` 预演，确认不会覆盖已有 torch。

7. **探针端口不一致会静默拿不到结果** —— 连到没有服务的默认端口，
   现象是「跑完了但什么都没测到」。服务地址须与 bot 实际监听端口一致。

8. **venv 由 uv 管理时没有 pip**，用 `uv pip install`。

## 9. 常见改动对照表

| 我想…… | 改哪里 |
|---|---|
| 换个 LLM | `.env` 里 `MODELSCOPE_MODEL`（或换 `base_url` + key，代码不动） |
| 换个语音识别 | `STT_ENGINE` |
| 换个音色 | `TTS_ENGINE` / `PIPER_VOICE_ID` |
| 让机器人更早开口 | `VAD_STOP_SECS` 调小 |
| 让回答更短 | `SYSTEM_INSTRUCTION` |
| **加业务能力** | `server/tools.py` ← 第二阶段的入口 |

## 10. 下一步

- **第二阶段（写自己的业务）**：在 `tools.py` 里加 function calling 工具。
  加一个工具的流程：写 `async def handler(params: FunctionCallParams)` → 定义
  `FunctionSchema`（handler 指向它）→ 加进 `build_tools()` 列表。
  `FunctionSchema` 带 handler 时会自动注册，无需手工 `register_function`。
  前端无需改动 —— pipecat 会把调用过程以 RTVI 消息推给前端渲染。

- **第三阶段（融入大系统）**：pipecat 的传输层可替换（WebRTC / WebSocket / Daily 等），
  `bot.py` 里的管线装配与传输解耦，因此可整体嵌入既有 Python 服务。

---

## 附：本阶段的一句话结论

**语音对话的本质是「文本对话 + 两端编解码 + 流式编排」。**
四个模型插槽独立可换，钱只花在 LLM 上；真正的瓶颈在 ASR（错了会污染整轮），
而中文场景下**免费开源方案（SenseVoice）已经能同时做到更快更准**。
框架省掉的是把组件粘起来的那层工程（WebRTC 音频帧、流式传输、打断处理、
上下文管理、错误传播），这部分自己撸是几周量级，且坑全在细节里。
