# Pipecat 语音 Agent —— 项目总览

> **先看这一页。** 这个仓库里有**三个项目** + 一份调研文档，它们回答的是**三个不同的问题**。

---

## 我该看哪个？

| 我想…… | 去哪 |
|---|---|
| 要一个**能跑的语音 Agent**（听→想→说全在自己手里） | **`pipecat-quickstart/`** |
| 把语音**插进一个现成的 agent 系统**（宿主是主人） | **`pipecat-open-webui/`** ← 最近在更新 |
| 把语音做成**可接入的模块**（模块是主人，去调外部平台） | **`voice-module-dify/`** |
| 了解 Pipecat 这框架能干什么 | `pipecat-项目调研.md` |
| 找模型选型依据、延迟基准脚本 | `pipecat-quickstart/reference/pipecat-modelscope/`（📕 **已冻结**） |

**一句话记法**：

| 项目 | 一句话 |
|---|---|
| 📗 `pipecat-quickstart` | **语音 Agent 本体** —— 语音就是产品本身 |
| 📘 `voice-module-dify` | **语音当零件** —— 语音模块**去调**外部平台（方向：我 → 平台） |
| 📙 `pipecat-open-webui` | **语音当零件** —— **被**现成系统调用（方向：宿主 → 我） |
| 📄 `pipecat-项目调研.md` | **这框架是什么**（上手前读） |

---

## 三个项目的本质区别

三个项目都在做"语音"，区别只有两个问题：**谁在思考？语音在哪？**

```
                    谁在思考？              语音模块在哪？            调用方向
────────────────────────────────────────────────────────────────────────────────
① pipecat-quickstart   自己（LLM 在管线里）    核心（管线本体）          ——
② voice-module-dify    外部平台（Dify 等）     独立服务，主动调平台      我 → 平台
③ pipecat-open-webui   宿主系统（Open WebUI）  宿主的内部零件            宿主 → 我
```

- **①** 是"**全套**"：VAD / ASR / LLM / TTS 全在一个管线里，你拥有全部。
- **②** 是"**外挂**"：语音模块是主人，主动去调外部平台要答案（平台是可替换的）。
- **③** 是"**寄生**"：宿主系统是主人，语音作为它内部的一块零件被调用。

> ②和③**方向正好相反** —— 这是它们不能互相替代的原因，也是为什么要各做一个。

---

## 📗 `pipecat-quickstart/` —— 完整语音 Agent

| 项 | 说明 |
|---|---|
| 是什么 | 能跑的实时语音对话 Agent，级联管线 `VAD → ASR → LLM → TTS` |
| 需要几个 key | **1 个**（只有 LLM 要钱；STT / TTS 全在本地跑） |
| 状态 | ✅ **第二阶段已结项（`v0.1.0`，2026-10-05）** —— 能力 / 验证 / 文档三方面收口 |
| 许可证 | MIT |
| 技术栈 | Python + Pipecat 1.12+；SenseVoice / Whisper（本地 STT）+ Piper / Kokoro（本地 TTS） |
| 访问方式 | 浏览器 `http://localhost:7860/client` |

配套文档：

| 文件 | 内容 |
|---|---|
| `README.md` | 这个项目本身怎么跑 |
| `docs/HANDBOOK.md` | 第一阶段：怎么从零搭起来（插槽、配置、踩坑） |
| `docs/HANDBOOK-02.md` | 第二阶段：怎么写自己的业务（工具、数据、编排、测试纪律） |
| `docs/TOOL_TESTS.md` | 工具调用压测报告（含**可信度标注**，引用前先读顶部作废声明） |

**快速上手**：

```bash
cd pipecat-quickstart/server
uv sync --extra sensevoice     # 默认中文 STT（SenseVoice）需要额外依赖
cp .env.example .env           # 只需填 MODELSCOPE_API_KEY
uv run bot.py                  # 浏览器打开 http://localhost:7860/client
```

---

## 📘 `voice-module-dify/` —— 语音模块（接外部平台）

| 项 | 说明 |
|---|---|
| 是什么 | **可单独部署的语音模块**：只负责"听"和"说"，思考交给外部的智能体系统 |
| 核心主张 | 语音这一层归你，思考那一层归平台，两边通过**可替换的接口**相连 |
| 需要几个 key | 一个平台的 key（换平台只改 `.env` 两行） |
| 状态 | ✅ **`v0.0.1` 完成**（2026-10-05）—— 4 个探针 + 33 个单测 |
| 许可证 | MIT |
| 技术栈 | Python + Pipecat；本地 Whisper（STT）+ Piper（TTS）；WebSocket / SmallWebRTC 双入口 |
| 案例 | 接 **Dify**（完整流程见 `CASE-dify.md`） |

**它特别处理了语音里真正难的部分**：判断说完没有、打断、边收边说（首句就开口）、平台慢时填场、
页面上实时显示思考 / 工具调用 / 工具结果 / 每轮三段耗时。

配套文档：

| 文件 | 内容 |
|---|---|
| `README.md` | 项目定位与怎么跑 |
| `CASE-dify.md` | 接 Dify 的完整流程、必须人工的三步、实测踩坑与数字 |
| `docs/CONCEPTS.md` | 心智模型与答疑（9 条，每条结论带验证方式） |
| `docs/PORTING.md` | 换成你自己的平台：契约 + 清单 |
| `docs/HANDBOOK-03.md` | 阶段手册（从零搭的过程、已验证/未验证分开写） |

**30 秒跑起来**（不用真平台，用本地替身顶着）：

```bash
cd voice-module-dify
uv sync
cp .env.example .env          # 把 BRAIN_STUB 设成 1（用本地替身当脑子）
uv run python probe/verify_brain.py         # 验证"接脑袋"这条接口
uv run python probe/verify_speech_legs.py   # 验证它真的能说、也能听
```

---

## 📙 `pipecat-open-webui/` —— 语音零件（被宿主调用）

| 项 | 说明 |
|---|---|
| 是什么 | **Open WebUI 的二次开发分支**：把一个现成 agent 系统的语音能力升级/补齐 |
| 核心主张 | 宿主系统是主人，语音是它内部的一块**可插拔零件**（用它的原生插槽，非外挂服务） |
| 上游基线 | Open WebUI `v0.11.4` |
| 状态 | ✅ **已跑通**（LLM 对话 + 语音输入输出）；测试性质，非生产 |
| 许可证 | **双协议** —— 上游代码沿用 **Open WebUI License**；**本分支新增的独立文件采用 MIT**（见 `LICENSE-SUPPLEMENT.md`） |
| 技术栈 | Python / FastAPI 后端 + SvelteKit 前端；**只做界面与编排，不跑模型**（纯 CPU 够用） |
| 语音实现 | **STT** = 本地 `faster-whisper`（用 Open WebUI 原生插槽）；**TTS** = 浏览器 `speechSynthesis` |

**访问地址**：

| 场景 | 地址 |
|---|---|
| 本机 | <http://localhost:8000> |
| **CNB 云环境（当前）** | **<https://6p1cwlsz8a-8000.cnb.run>** |

**启动**（源码模式，详见该项目 `README.md` 的「本分支怎么跑」一节）：

```bash
cd pipecat-open-webui/backend
USE_SLIM_DOCKER=true FRONTEND_BUILD_DIR=/workspace/pipecat-open-webui/build PORT=8000 \
  PATH="$PWD/.venv/bin:$PATH" ./start.sh
```

配套文档（本分支自有的放在 `voice-docs/`，与上游 `docs/` 分开）：

| 文件 | 内容 |
|---|---|
| `README.md` | 本分支怎么跑（源码模式 / 地址 / `.env` 配置） |
| `voice-docs/INTEGRATION.md` | **语音接入说明**（做了什么 / 改了哪些文件 / 调了哪些接口） |
| `voice-docs/VOICE-MODES.md` | **语音形态怎么选**（A 简单 I/O / B 连续对话 / C 精细实时） |
| `voice-docs/VOICE-UX.md` | **语音交互体验怎么复现**（自动发送 / 自动朗读 / 必需配置） |
| `voice-docs/CHANGELOG.md` | 本分支相对上游的**全部改动记录** |
| `FAQ.md` | 常见问题（含 **Q14：识别成外语怎么办**） |
| `voice-docs/PLATFORM-SELECTION.md` | 宿主平台选型调研（Open WebUI / LibreChat / LobeChat 对比） |

---

## 📕 `pipecat-quickstart/reference/pipecat-modelscope/` —— 已冻结的调研/验证项目

| 项 | 说明 |
|---|---|
| 是什么 | **只回答三个问题**：连通性行不行、延迟快不快、该用哪个模型 |
| 需要几个 key | **3 个**（Deepgram + Cartesia + 魔搭） |
| 状态 | **不再维护** —— 不要在它上面继续开发 |
| 位置 | 已归档进主项目：`pipecat-quickstart/reference/` |

保留价值：

- **模型选型报告** —— 为什么默认是 `nex-agi/Nex-N2.5-mini`
- **5 个基准/验证脚本**
- **`logs/ANALYSIS.md`** —— 当时发现并修复的缺陷记录
- **一个关键洞察：`TTFB` ≠ `TTFAT`** —— 只看"首 token"选模型会严重误判

---

## 三个容易踩的坑

**1. 延迟数字不能直接比。**

`pipecat-quickstart`（934ms）和 `reference/pipecat-modelscope`（1.7–1.9s）的基准**口径不同**：
前者是「用户真正说完」那一刻，后者是「音频推流结束」，**后者偏乐观约 0.5 秒**。

**2. 引用 `TOOL_TESTS.md` 的数据前，先读它顶部的作废声明。**

有一条结论已被重复试验推翻（「工具越多越不调」），只保留原文并标注作废原因，**不要拿它当结论用**。

**3. 语音识别默认不指定语言 = 自动猜语种。**

Whisper 不指定语言时会自动检测，**实测会把中文误判成泰语**（②和③都踩过）。
两个项目都已在 `.env` 里固定 `STT_LANGUAGE=zh` / `WHISPER_LANGUAGE=zh`，**别删这两行**。

---

## 语言约定（所有项目通用）

文档正文用**中文**；**代码、命令与终端输出一律按原文展示（英文纯 ASCII）**，不做中文意译 ——
便于直接复制运行，也避免非 UTF-8 控制台的编码问题。
例外：提示词、工具 `description` 与 `spoken` 朗读文案属于**功能内容**，在代码里本来就是中文。

---

## 仓库

<https://cnb.cool/lpk3215/pipecat-ai-test> ｜ 作者：cnb.lpk
