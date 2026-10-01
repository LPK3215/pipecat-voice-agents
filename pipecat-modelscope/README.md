# Pipecat 最小语音 Agent（LLM 固定为 ModelScope `nex-agi/Nex-N2.5-mini`）

一个最小可跑的实时语音对话项目，用于验证 **连通性** 与 **及时性（端到端延迟）**。

## 架构

级联式（Cascade）管线：

```
transport.input() → STT → user_aggregator → LLM → TTS → transport.output() → assistant_aggregator
```

| 环节 | 选型 | 说明 |
|---|---|---|
| **LLM** | **魔搭 ModelScope `nex-agi/Nex-N2.5-mini`** | **已写死**，见下节 |
| STT | Deepgram | 需要独立 key |
| TTS | Cartesia | 需要独立 key |
| 传输 | SmallWebRTC | 浏览器直连，无需第三方账号 |

> 魔搭这个 endpoint **只有 LLM，没有 STT/TTS 接口**（`/v1/audio/*` 均返回 404），所以语音环节必须另配服务。

---

## 模型已固定（写死在代码里）

**`nex-agi/Nex-N2.5-mini`** —— 不再使用其他模型。

写死位置（三处，均为模块级常量，**不读环境变量**）：

| 文件 | 常量 |
|---|---|
| `bot.py` | `MODELSCOPE_MODEL` / `MODELSCOPE_BASE_URL` |
| `test_e2e.py` | `MODELSCOPE_MODEL` / `MODELSCOPE_BASE_URL` |
| `test_llm.py` | `DEFAULT_MODEL` / `DEFAULT_BASE_URL` |

`.env` 里**不需要**再配置模型名，配了也不生效；只有 `MODELSCOPE_API_KEY` 需要填。

### 选型依据（2026-10-01 实测 ModelScope 全部可用模型）

直连 API 首 token 延迟：

| 模型 | 平均 | 最快 | 最慢 | 结论 |
|---|---|---|---|---|
| **`nex-agi/Nex-N2.5-mini`** | **460 ms** | 366 ms | 523 ms | ✅ **最快最稳，已选用** |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 1879 ms | 1625 ms | 2048 ms | 稳但慢 3.3 倍 |
| `Qwen/Qwen3.8-Flash-Next` | 1876–3011 ms | 1178 ms | 5972 ms | 抖动最大 15 倍 |
| `stepfun-ai/Step-3.5-Flash` | 2474 ms | 2320 ms | 2620 ms | 输出 0 字 |
| `stepfun-ai/Step-3.7-Flash` | 2198 ms | 2043 ms | 2360 ms | 输出 0 字 |
| `ZhipuAI/GLM-4.7-Flash` | 12759 ms | 6033 ms | 20045 ms | ❌ 不可用 |
| `meituan-longcat/LongCat-Flash-Lite` | — | — | — | ❌ 400 Unsupported model |
| `Shanghai_AI_Laboratory/Intern-S1-mini` | — | — | — | ❌ 401 |
| `PaddlePaddle/ERNIE-4.5-0.3B-PT` | — | — | — | ❌ 401 invalid_model |

该模型经专门探测**基本不进入「思考」模式**（实测 thinking 仅 0.005 s），
因此「首 token」≈「首个答案 token」，没有额外思考等待。
（探测器：`scripts/probe_thinking.py`，日志：`logs/20-probe-thinking.log`）

---

## 文件

```
bot.py                语音 Agent 主程序（含延迟观测）
test_llm.py           LLM 连通性 + 时延自测（不依赖 STT/TTS key）
test_e2e.py           端到端延迟实测（本地 Whisper + ModelScope + 本地 Piper，无需云 STT/TTS key）
scripts/
  check_wiring.py     离线自检：依赖导入 + 服务装配 + Key 盘点（不需要任何 key）
  test_local_chain.py 无 Key 本地链路验证 + Sink A/B 对照
  probe_thinking.py   探测模型「思考模式」对延迟的影响
logs/                 全部实测日志 + ANALYSIS.md（本次分析报告）
.env.example          环境变量模板
pyproject.toml        依赖声明
```

---

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置 key（模型名不用配，已写死）
cp .env.example .env
#    只需填 MODELSCOPE_API_KEY / DEEPGRAM_API_KEY / CARTESIA_API_KEY

# 3. 自检（不需要任何 key）
uv run scripts/check_wiring.py

# 4. 测 LLM 连通性与时延
uv run test_llm.py

# 5. 测端到端延迟（只需 MODELSCOPE_API_KEY，STT/TTS 走本地）
uv run test_e2e.py

# 6. 启动语音 Agent（浏览器体验需要三个 key 齐全）
uv run bot.py
#    浏览器打开 http://localhost:7860/client 点击 Connect
```

---

## 及时性：实测数据（2026-10-01）

### 端到端（`test_e2e.py`，各 3 次）

| 模型 | 各次「机器人开口」 | 平均 |
|---|---|---|
| **`nex-agi/Nex-N2.5-mini`** | **938 / 943 / 922 ms** | **934 ms** |
| `Qwen/Qwen3.8-Flash-Next`（旧默认） | 2659 / 4287 / 5514 ms | 4153 ms |

> **仅换模型这一项，端到端就快了 4.4 倍，且抖动从 2.3–5.5 s 收窄到 0.92–0.94 s。**

### 分段延迟

| 环节 | `nex-N2.5-mini` | `Qwen3.8-Flash-Next`（旧默认） |
|---|---|---|
| Whisper STT TTFB | 0.66 – 0.78 s | 0.62 – 0.73 s |
| LLM TTFB（首个 token） | 0.51 – 0.57 s | 0.68 – 3.96 s |
| LLM **thinking** 耗时 | **0.005 s** | 0.89 – 1.84 s |
| **LLM TTFAT（首个答案 token）** | **0.52 – 0.58 s** | **1.88 – 4.85 s** |
| Piper TTS TTFB | 0.23 – 0.26 s | 0.03 – 0.17 s |

### 结论

- **连通性：通过。** OpenAI 兼容接口工作正常，链路已真实跑通（Whisper 转写 + LLM 回复 + Piper 出声）。
- **及时性：接近达标。** 端到端 **934 ms**，目标 500–800 ms，属于「基本可用」级别——
  对比切换模型前的 4153 ms，改善 4.4 倍。
- **瓶颈已从 LLM 转移到 STT**：LLM 只占 0.52 s，而 Whisper(base) 要 0.66–0.78 s。
  下一步优化重点是 STT（换 `small` 以上的云端 STT、或换更快的模型规格）。
- 脚本口径的「语音结束」基准晚于真实说话结束约 0.5 s，真实端到端约 **1.4 s**。

---

## 为什么「看着首 token 很快，实际体验还是卡」

这是最容易踩的坑，务必区分两个指标：

| 指标 | 含义 |
|---|---|
| **TTFB** | 服务端返回**第一个** token 的时刻（可能只是思考/空白） |
| **TTFAT** | 返回**第一个答案** token 的时刻 ← **用户真正等到的就是它** |

pipecat 源码注释：

```
TTFAT extends TTFB: it runs from the same request start but ends at the
first token of the answer the caller sees, so anything the model streams
first — reasoning, most often — falls between the two.
```

实测佐证：`Qwen3.8-Flash-Next` 某次 TTFB 0.680 s、TTFAT 1.883 s（1.204 s 全在思考），
报告里「用户说完 → LLM 首 token」显示 1856 ms，**与 TTFAT 吻合，而非 TTFB**。

> 所以只看「首 token」选模型会严重误判。`Qwen3.8-Flash-Next` 看着首 token 只要 0.68 s，
> 但用户要等 1.88 s 才看到答案。**必须看端到端实测。**

**除模型外的另外两个真实原因：**

1. **免费额度限流（429）**：连续压测 10 次即触发一次
   `429 - We have to rate limit you for model nex-agi/Nex-N2.5-mini`，
   多轮对话时更容易踩到。
2. **端点是共享免费推理服务，延迟抖动大**：同一模型同一问题实测 386 → 1215 ms（3 倍）；
   其他模型更夸张（Zhipu 6 → 20 s）。

---

## 延迟观测说明

`bot.py` 已开启三套观测，运行时直接在控制台输出：

- `MetricsLogObserver`：各服务 TTFB / TTFAT / TTFA
- `UserBotLatencyObserver`：**用户说完 → 机器人开口**的端到端延迟，带逐段分解
- `PipelineParams(enable_metrics=True)`：token 用量与处理耗时

---

## 常见问题

- **没有声音**：检查浏览器麦克风权限；确认 3 个 key 都已填写
- **连接失败**：确认端口 7860 未被占用；WebRTC 依赖 UDP，注意防火墙/VPN
- **429 限流**：免费额度限制，降低调用频率，或改用商业 API
- **测试跑完多等 20 秒**：已修复（原 `Sink` 会吞掉 `CancelFrame`）

---

## 备注

- 本项目基于 **Pipecat 1.12.0**，脚手架参考官方 `pipecat init quickstart` 生成。
- `bot.py` 的传输层仅注册了 `webrtc`；如需 Daily / 电话接入，需补充对应依赖与 `transport_params`。
- 本次实测共发现并修复 4 个缺陷（依赖漏声明、Whisper 未指定语言、`Sink` 吞 `CancelFrame`、
  自测脚本未加载 `.env`），详见 `logs/ANALYSIS.md`。
