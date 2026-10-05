# 语音模块接入说明（INTEGRATION）

> **本文给谁看**：后来拿到这个项目的人。
> 读完它，你应该能明白：**我们往 Open WebUI 里接了什么、怎么接的、改了哪里、调了哪些接口、以后怎么升级。**
>
> 相关文档：[`VOICE-MODES.md`](VOICE-MODES.md)（**语音形态怎么选**：三档 A/B/C）｜ [`VOICE-UX.md`](VOICE-UX.md)（**交互体验怎么复现**：自动发送 / 自动朗读 / 不遮挡）｜ [`VOICE-README.md`](VOICE-README.md)（项目定位）｜ [`ARCHITECTURE.md`](ARCHITECTURE.md)（早期设想，**未采用的外挂式**）｜ [`../FAQ.md`](../FAQ.md)（跑起来 / 排错）

---

## 0. 一句话

> 在一个**已经很大的系统**（Open WebUI）里，加了两片**薄薄的 I/O**：语音进、语音出。
> 中间的一切——思考、工具、记忆、知识库——**全走它自己的官方管线，我们一行没改**。

---

## 1. 背景与定位

| 项 | 说明 |
|---|---|
| 形态 | **第三种模式 · 内生式** —— 找到一个现成的大系统，把功能**长进它里面** |
| 宿主 | **Open WebUI** `v0.11.4`（源码在仓库根目录） |
| 我们只加 | **语音输入（STT）** 与 **语音输出（TTS）** |
| 我们不做 | 思考、工具、记忆、知识库、会话管理、界面 —— **全部是 Open WebUI 自带的** |

**硬约束**：语音模块**只做 I/O，不做思考**。
理由：一旦语音模块也自己写一套工具/记忆，就会出现"两套规范争权威"（同一件事两个地方负责 → 冲突）。
现在**职责不重叠**，所以简单、也安全。

---

## 2. 接入方案：为什么选"内生式"而不是"外挂式"

| | 外挂式（原 `ARCHITECTURE.md` 设想，**未采用**） | **内生式（实际采用）** ✅ |
|---|---|---|
| 语音的位置 | 独立服务，暴露 `/v1/sessions` 等接口，被大系统调用 | **系统内部的一个功能**（用它自己的插槽） |
| 耦合度 | 松（靠接口契约） | 紧（用它的配置 + 最小改动） |
| 需要维护 | 一套独立服务 + 接口 + 鉴权 | **一个 fork** |
| 是否符合"不要外挂一套工具" | ❌ | ✅ |
| 改动量 | 大（新写服务外壳） | **极小（3 处代码 + 1 个依赖文件）** |

> **结论**：`voice-docs/ARCHITECTURE.md` 里那套 `/v1/sessions` + `WS /audio` 的接口设计，是当时"外挂式"的草稿，
> **本项目未采用**，保留作历史参考。实际采用的是**内生式**。

---

## 3. 具体做了什么（完整动作清单）

### 3.1 需要改的核心代码：**只有一个文件，3 处**

`backend/open_webui/routers/audio.py`（每处均标注 `[pipecat-open-webui 本分支改动]`，便于与上游 diff）：

| # | 位置（函数） | 上游行为 | 本分支改动 | 为什么 |
|---|---|---|---|---|
| 1 | `set_faster_whisper_model()` | `if USE_SLIM: raise 503`（slim 下禁止本地 Whisper） | **注释掉该限制** | 我们在 slim 环境已装 faster-whisper，放开后本地 STT 可用 |
| 2 | `update_audio_config()` | slim 下拒绝把 STT 引擎设为空（= 本地） | **注释掉该拒绝** | 允许在 slim 下选择"本地 STT" |
| 3 | 同上函数末尾 | `if stt.ENGINE == '' and not USE_SLIM:`（slim 下不加载本地模型） | 去掉 `and not USE_SLIM` | 让 slim 下也能加载本地 Whisper 模型 |

> **TTS 一行没改**：`audio.tts.engine = ''` 时，前端直接用浏览器 `speechSynthesis` 合成，不经过后端。

### 3.2 额外依赖：**一个文件**

`backend/requirements-voice.txt`（在 `requirements-slim.txt` 之上追加）：

```
faster-whisper==1.2.1
huggingface-hub==1.20.1
tokenizers==0.22.2
onnxruntime==1.26.0
ctranslate2==4.8.0
av==14.0.1
```

安装：

```bash
cd backend
uv pip install --python .venv/bin/python -r requirements-voice.txt
```

> ⚠️ 版本必须与上游 `uv.lock` 对齐。**踩过的坑**：不约束版本会装到 `huggingface-hub 1.33.0`，
> 导致运行时报 `open() got an unexpected keyword argument 'metadata_errors'`。

### 3.3 配置

`.env`（项目根，已被 `.gitignore` 忽略）：

```bash
USE_SLIM_DOCKER=true                              # 瘦身模式
OPENAI_API_BASE_URL=https://token.sensenova.cn/v1 # LLM（商汤，OpenAI 兼容）
OPENAI_API_KEY=sk-...
```

数据库里的音频配置（**保持默认即可**）：

| 键 | 值 | 含义 |
|---|---|---|
| `audio.stt.engine` | `""` | 空 = **本地 Whisper** |
| `audio.stt.whisper_model` | `"base"` | 可改 `small` 提升中文准确率 |
| `audio.tts.engine` | `""` | 空 = **浏览器语音合成** |

---

## 4. 运行时的调用链与接口

```
🎤 浏览器录音
   │  前端：src/lib/components/chat/MessageInput/VoiceRecording.svelte
   ▼
POST /api/v1/audio/transcriptions     ← STT（后端本地 Whisper）
   │  routers/audio.py → _transcribe_whisper() → faster-whisper
   ▼
（文字进入正常聊天流程）
POST /api/v1/chat/completions         ← Open WebUI 官方：LLM + 工具 + 记忆 + RAG（未改动）
   ▼
（回复文字）
 🔊 浏览器 speechSynthesis.speak()      ← TTS（前端，不走后端）
```

| 环节 | 接口 / 位置 | 归属 |
|---|---|---|
| 语音转文字 | `POST /api/v1/audio/transcriptions` | Open WebUI 原生（我们只放开了 slim 限制） |
| 语音转文字（可选） | 浏览器 `SpeechRecognition`（`stt.engine='web'` 时） | Open WebUI 原生 |
| 聊天 | `POST /api/v1/chat/completions` | Open WebUI 原生（**未改动**） |
| 文字转语音 | 浏览器 `speechSynthesis` | Open WebUI 原生 |
| 文字转语音（可选） | `POST /api/v1/audio/speech`（`tts.engine!=''` 时） | Open WebUI 原生 |
| 关键前端文件 | `MessageInput/VoiceRecording.svelte`、`MessageInput/CallOverlay.svelte`、`Settings/Audio.svelte` | Open WebUI 原生 |

> **关键点**：除了那 3 处 slim 限制，**语音相关的所有代码都是 Open WebUI 自带的**。
> 换句话说——**我们只是让它的原生能力在 slim 模式下可用，不是自己写语音功能。**

---

## 5. 从零复现（步骤）

```bash
# 1. 后端瘦身依赖 + 本分支语音依赖
cd backend
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements-slim.txt
uv pip install --python .venv/bin/python -r requirements-voice.txt

# 2. 前端构建（需要调大 Node 堆内存，否则 OOM）
cd ..
npm ci
NODE_OPTIONS="--max-old-space-size=10240" npm run build

# 3. 配置 .env（见第 3.3 节）

# 4. 启动
cd backend
PORT=8000 PATH="$PWD/.venv/bin:$PATH" ./start.sh
```

访问：浏览器打开 `$CNB_VSCODE_PROXY_URI` 把 `{{port}}` 换成 `8000`（CNB 云环境，见 [`../FAQ.md`](../FAQ.md) Q12）。

---

## 6. 以后的升级方向

| 想升级什么 | 怎么做 | 代价 |
|---|---|---|
| **中文识别更准** | `.env` 设 `WHISPER_MODEL=small`（或 `medium`） | 更慢（CPU） |
| **声音更自然** | 配 `AUDIO_TTS_ENGINE`（`openai` / `elevenlabs` / `azure`）指向云端 TTS | 要 key、要钱、要网 |
| **换更强/更快的 LLM** | 改 `.env` 的 `OPENAI_API_BASE_URL` / `OPENAI_API_KEY` | 无 |
| **实时双向语音**（边说边听、打断、主动播报） | **不是配置能解决的** —— 完整判断标准与实现要点见 [`VOICE-MODES.md`](VOICE-MODES.md) | 大（≈ 把 ② 的核心重做一遍） |
| **跟上上游** | `git remote add upstream https://github.com/open-webui/open-webui.git` → `git diff upstream/main --stat` 看本分支改动 | 注意 `audio.py` 3 处冲突 |

> ⚠️ **现状的边界**：当前是**点按式语音**（点麦克风 → 说一句 → 出结果），
> **不是**实时双向语音。要做即时的，请按上表最后一行规划。

---

## 7. 变更记录

见 [`CHANGELOG.md`](CHANGELOG.md)。
