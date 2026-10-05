# 本分支变更记录（pipecat-open-webui）

> **只记录本分支相对上游 Open WebUI 的改动。**
> 上游自身的变更历史见根目录 [`CHANGELOG.md`](../CHANGELOG.md)（**保持不动，不清空、不改写**）。

## [Unreleased] — 2026-10-05

### 新增

- 从 **Open WebUI `v0.11.4`** fork 出 `pipecat-open-webui` 分支
- `voice-docs/`：本分支文档
  - `PLATFORM-SELECTION.md`：宿主平台选型调研（Open WebUI / LibreChat / LobeChat 对比，结论 Open WebUI）
  - `ARCHITECTURE.md`：语音模块的接口契约（草稿）
  - `VOICE-README.md`：原独立语音模块的定位说明
- `docs/pipecat-open-webui-architecture.svg`：本分支架构图（由 `scripts/visualization/generate_voice_architecture.mjs` 生成）
- `CONTRIBUTING.md` / `FAQ.md` / `AUTHORS`
- `LICENSE-SUPPLEMENT.md`：**双协议**说明 —— 上游代码沿用 Open WebUI License，**本分支新增的独立文件采用 MIT**
- `voice-docs/INTEGRATION.md`：**语音模块接入说明**（给后来者：做了什么动作 / 改了哪些文件 / 调用了哪些接口 / 升级方向）
- `voice-docs/VOICE-MODES.md`：**语音形态选择与实现指南**（三档：A 简单 I/O / B 连续对话 / C 精细实时；怎么选、C 档的 5 个机制、从 B 升 C 要补哪四层）
- 更正：**③ 已能"连续对话"**（CallOverlay 自动循环 + 点击打断，属 B 档）；先前"③ 不能做实时对话"的表述不准确，已在 `VOICE-MODES.md` 更正
- `voice-docs/VOICE-UX.md`：**语音交互体验与复现**（自动发送 / 自动朗读 / 录音不遮挡输入框；3 处改动 + 哪些是内置的）
- 语音交互默认优化：`speechAutoSend`、`responseAutoPlayback` 改默认开启；录音时不再隐藏输入框
- **恢复被上游注释的「静音自动确认」**（`VoiceRecording.svelte`）：停顿 3s 自动结束录音并提交 —— 不恢复则必须手动点 ✓

### 修复

- **中文语音被识别成泰语**（说中文 → Whisper 猜成泰语 → 回复也是泰语）
  - 根因：`WHISPER_LANGUAGE` 默认为空 → 走「自动猜语种」（`config.py:1572`）
  - 修复 ①：`.env` 设 **`WHISPER_LANGUAGE=zh`**（纯环境变量，**优先级高于界面里的 STT Language 设置**）
  - 修复 ②：DB 的 `audio.stt.whisper_model` 由 `base` 改为 **`small`** —— ⚠️ 它是 **PersistentConfig，数据库值优先于环境变量**，光改 `.env` 不生效
  - 实测（同一段中文音频）：修复前 → 泰语 ❌；修复后 → `你好请用一句话介绍一下,你自己。` ✅
  - 详见 [`../../FAQ.md`](../FAQ.md) Q14 与 [`VOICE-UX.md`](VOICE-UX.md) 第 1.5 节
- **`README.md` 补充「本分支怎么跑」章节**：源码模式三步启动、CNB 访问地址、`.env` 配置表

### 移除（相对上游）

- `CODE_OF_CONDUCT.md`、`contribution_stats.py`、`demo.png`、`banner.png`、`TROUBLESHOOTING.md`、`.github/`、`docs/SECURITY.md`
- **说明**：这些是上游的元数据 / 宣传 / CI 文件，与本分支「语音实验」无关，移除**不影响运行**

### 接入语音（使用 Open WebUI 原生插槽，非外挂服务）

- **STT（语音输入）**：本地 **faster-whisper**（Open WebUI 原生引擎）
  - `backend/open_webui/routers/audio.py` 三处二次开发：放开 slim 模式对本地 Whisper 的禁止
    （每处均标注 `[pipecat-open-webui 本分支改动]`，便于与上游 diff）
  - 新增 `backend/requirements-voice.txt`：本分支额外依赖（与上游 `uv.lock` 版本对齐）
- **TTS（语音输出）**：**浏览器 `speechSynthesis`**（`audio.tts.engine = ''` 时前端直接合成，零后端依赖）
- **中间链路**：完全走 Open WebUI 官方 `chat completion` + 工具调用，未做任何改动
- 实测：STT 端点 `POST /api/v1/audio/transcriptions` → `HTTP 200`，正确转写

### 变更

- **项目更名**：`voice-bridge` → **`pipecat-open-webui`**（Open WebUI + Pipecat 组合命名；与 `pipecat-quickstart` 一脉相承）
- 启动方式改为**瘦身模式**：`requirements-slim.txt` + **`USE_SLIM_DOCKER=true`**（适配纯 CPU 云服务器）
- 前端构建需 `NODE_OPTIONS=--max-old-space-size=10240`（否则 OOM）
- 监听端口：默认 `8080` → 本环境改用 **`8000`**（8080/3000 被环境占用）

### 保留（法律要求，未改动）

- `LICENSE` / `LICENSE_NOTICE` / `LICENSE_HISTORY` / `CONTRIBUTOR_LICENSE_AGREEMENT`
- 界面上的 "Open WebUI" 品牌

### 当前状态

- ✅ 已接入 LLM（商汤 SenseNova，OpenAI 兼容），实测对话可用
- ✅ 已接入语音：STT = 本地 faster-whisper；TTS = 浏览器 `speechSynthesis`
- ✅ 公网可访问（CNB 端口代理，见 [`../FAQ.md`](../FAQ.md) Q12）
- ⏭️ **有意未做**：实时双向语音（打断 / 主动播报）—— 见 [`INTEGRATION.md`](INTEGRATION.md) 第 6 节

> `ARCHITECTURE.md` 是"外挂式"的早期设想，**本项目未采用**，保留作历史参考（详见 [`INTEGRATION.md`](INTEGRATION.md) 第 2 节）。
