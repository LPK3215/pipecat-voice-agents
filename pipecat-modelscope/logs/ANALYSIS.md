# Pipecat 项目实测分析（以日志为唯一依据）

> 实测：2026-10-01 19:12–19:47
> 环境：Python 3.12.14 / pipecat-ai 1.12.0 / Linux x86_64 / 8 核
> 所有结论均可在 `logs/` 下对应日志中逐行复核。

---

## 0. 结论

**链路完全打通。换对模型后，端到端从 4153 ms 降到 934 ms（4.4 倍）。**

| 维度 | 判定 |
|---|---|
| 连通性 | ✅ 通过（除两个云 Key 外全部实测跑通） |
| 代码质量 | ✅ 修复 5 个缺陷（D1–D5） |
| 及时性 | ⚠️ 接近达标：934 ms（目标 500–800 ms） |
| 最大单项优化 | **换模型**：4153 ms → 934 ms |
| 当前瓶颈 | **STT**（0.66–0.78 s），LLM 已降到 0.52 s |

---

## 1. 日志索引

### 第一轮：无 Key 状态的连通性排查

| 日志 | 内容 | 结论 |
|---|---|---|
| `01-uv-sync.log` | `uv sync` | 成功，105+12 个包 |
| `02-wiring.log` | 离线装配自检 | **28/29 通过** |
| `03` / `04-test_*.log` | `test_llm.py` / `test_e2e.py` | exit 1：缺 Key |
| `05-bot-server.log` | `bot.py` + HTTP 探测 | 前端/WebRTC **均 200**，崩在 STT 构造 |
| `06b-*-crash.log` | 首轮本地链路（旧版 Sink） | 链路通，收尾卡死 |
| `06-local-chain-sink-all.log` | A 组：Sink 放行全部帧 | **收尾 0.00 s** |
| `07-local-chain-legacy-sink.log` | B 组：复用原 Sink | **收尾 20.02 s** |

### 第二轮：真 Key 实测

| 日志 | 内容 |
|---|---|
| `08-test_llm-no-dotenv.log` | 证明 `.env` 不会被自动加载 |
| `09-test_llm-real.log` | 默认模型真实延迟 |
| `10-model-sweep.log` | **8 个候选模型横向对比** |
| `11` / `12-test_e2e-real-*.log` | 修复前跑真实端到端（崩溃，无结果） |
| `13-test_e2e-FIXED-nex.log` | 修复后首次成功产出报告 |
| `20-probe-thinking.log` | **思考模式探测** |
| `21-nex-stability-x10.log` | 连续 10 次（**第 9 次触发 429**） |
| `22-wiring-final.log` | 最终装配自检 |
| `23-test_llm-final.log` | 最终 LLM 时延（nex，460 ms） |
| `24-e2e-final-x3.log` | **最终端到端（nex，934 ms）** |
| `25-bot-server-final.log` | 最终 bot.py 启动日志 |

> ⚠️ `14` / `18-e2e-*-x3.log` 的模型标注**有误**：那两批实际跑的都是
> `Qwen3.8-Flash-Next`，原因见第 6 节「踩坑记录」。

辅助脚本：
- `scripts/check_wiring.py` —— 离线依赖与服务装配自检
- `scripts/test_local_chain.py` —— 无 Key 本地链路验证 + A/B 对照
- `scripts/probe_thinking.py` —— 思考模式对延迟的影响

---

## 2. 真实延迟数据

### 2.1 直连 API 首 token（`test_llm.py --runs 5`）

| 模型 | 平均 | 最快 | 最慢 | 结论 |
|---|---|---|---|---|
| **`nex-agi/Nex-N2.5-mini`** | **460 ms** | 366 ms | 523 ms | ✅ **最快最稳，已写死为默认** |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 1879 ms | 1625 ms | 2048 ms | 稳但慢 3.3 倍 |
| `Qwen/Qwen3.8-Flash-Next` | 1876–3011 ms | 1178 ms | 5972 ms | 抖动 15 倍 |
| `stepfun-ai/Step-3.5-Flash` | 2474 ms | 2320 ms | 2620 ms | 输出 0 字 |
| `stepfun-ai/Step-3.7-Flash` | 2198 ms | 2043 ms | 2360 ms | 输出 0 字 |
| `ZhipuAI/GLM-4.7-Flash` | 12759 ms | 6033 ms | 20045 ms | ❌ 不可用 |
| `meituan-longcat/LongCat-Flash-Lite` | — | — | — | ❌ 400 Unsupported model |
| `Shanghai_AI_Laboratory/Intern-S1-mini` | — | — | — | ❌ 401 |
| `PaddlePaddle/ERNIE-4.5-0.3B-PT` | — | — | — | ❌ 401 invalid_model |

### 2.2 端到端（`test_e2e.py`，各 3 次）

| 模型 | 各次「机器人开口」 | 平均 |
|---|---|---|
| **`nex-agi/Nex-N2.5-mini`** | **938 / 943 / 922 ms** | **934 ms** |
| `Qwen/Qwen3.8-Flash-Next` | 2659 / 4287 / 5514 ms | 4153 ms |

**仅换模型一项：快 4.4 倍，且抖动从 2.3–5.5 s 收窄到 0.92–0.94 s。**

### 2.3 分段延迟（pipecat 自身埋点）

| 环节 | `nex-N2.5-mini` | `Qwen3.8-Flash-Next` |
|---|---|---|
| Whisper STT TTFB | 0.66 – 0.78 s | 0.62 – 0.73 s |
| LLM TTFB（首个 token） | 0.51 – 0.57 s | 0.68 – 3.96 s |
| LLM **thinking** 耗时 | **0.005 s** | 0.89 – 1.84 s |
| **LLM TTFAT（首个答案 token）** | **0.52 – 0.58 s** | **1.88 – 4.85 s** |
| Piper TTS TTFB | 0.23 – 0.26 s | 0.03 – 0.17 s |

结论：换模型后**瓶颈从 LLM 转移到 STT**（0.66–0.78 s）。
下一步优化重点是 STT 而非 LLM。

### 2.4 关键指标：`TTFAT ≠ TTFB`

pipecat 源码（`frame_processor_metrics.py:242-270`，`llm_service.py:819`）：

```
TTFAT extends TTFB: it runs from the same request start but ends at the
first token of the answer the caller sees, so anything the model streams
first — reasoning, most often — falls between the two.
```

| 指标 | 含义 |
|---|---|
| **TTFB** | 首个 token（可能只是思考 / 空白） |
| **TTFAT** | 首个**答案** token ← **用户真正等到的时刻** |

实证（`e2e-20261001-193916.log`，当时跑的 Qwen）：

```
OpenAILLMService#0 TTFB:  0.680s
OpenAILLMService#0 TTFAT: 1.883s (1.204s thinking)
报告输出: 语音结束 -> LLM 首 token  1856 ms   ← 与 TTFAT 吻合，而非 TTFB
```

**教训：只看「首 token」选模型会严重误判。** Qwen 看似首 token 0.68 s，
用户实际要等 1.88 s；nex 的 TTFB 与 TTFAT 几乎相同（0.005 s thinking），所以才是真快。

### 2.5 该口径偏乐观

`report()` 基准是「音频推流结束」，而合成 WAV 尾部含静音，VAD 在推流结束前
**0.48–0.58 s** 就已判定说完（日志中表现为 `VAD 判定说完 -483 ms` 等负值）。
真实端到端约 **1.4 s**。

---

## 3. 修复的缺陷

### D1｜依赖漏声明（致命，已修）

`test_e2e.py` 依赖 `faster-whisper`/`piper`/`soxr`/`numpy`，`pyproject.toml` 一个都没写。
证据：`02-wiring.log` 首轮 `[FAIL] import pipecat.services.whisper.stt → No module named 'faster_whisper'`。
**已修**：`[dependency-groups] dev` 增加 `pipecat-ai[piper,whisper]>=1.12.0`。

### D2｜缺 Key 时报错太晚（未修）

`bot.py` 在客户端连上之后才构造服务，缺 Key 表现为
「`🚀 Bot ready!` + 浏览器连上成功 → 连接被静默打断」（`05-bot-server.log`），
极易误判为网络问题。

### D3｜Whisper 未指定语言，中文被识别成英文（已修）

修复前：中文音频 → `Please use a sentence to introduce yourself.`
修复后：中文音频 → `你好請用一句話介紹一下,你自己。`（`24-e2e-final-x3.log`）
**已修**：传入 `WhisperSTTService.Settings(language=Language.ZH)`。

> 遗留：Whisper base 输出**繁体**中文。可加
> `initial_prompt="以下是普通话的句子。"` 引导简体，或换 `small` 以上模型。

### D4｜`Sink` 吞掉 `CancelFrame` → 结果全部丢失（已修）

最严重的问题。原 `Sink` 只放行 `StartFrame`/`EndFrame`，
`PipelineWorker` 等不到 `CancelFrame` 抵达末端，**固定阻塞 20 s**，
并在 `finally` 中抛 `TimeoutError` 向上传播——**导致 `report(tl)` 永不执行，
测试永远拿不到任何结果**。

证据（`11`/`12-test_e2e-real-*.log`，两次都崩在同一行）：

```
File "test_e2e.py", line 265, in run_benchmark
    await asyncio.wait_for(run_task, timeout=10)
TimeoutError
```

A/B 对照（`06` vs `07`）：

| 组 | Sink | CancelFrame 告警 | 收尾耗时 |
|---|---|---|---|
| A | 放行全部帧 | 0 次 | **0.00 s** |
| B | 原 Sink | 1 次 | **20.02 s** |

**已修**：`Sink` 仅消费 `TTSAudioRawFrame`，其余帧一律放行；
`finally` 里的 `wait_for` 包进 `try/except`，保证结果不丢失。

### D5｜自测脚本不加载 `.env`（已修）

只有 `bot.py` 调用了 `load_dotenv()`，`.env` 就在同目录却读不到
（`08-test_llm-no-dotenv.log` 仍报「请先设置环境变量」），
与 README 流程直接矛盾。
**已修**：`test_llm.py` / `test_e2e.py` 都加 `load_dotenv(override=True)`。

### 观测器瑕疵（未修，非阻塞）

- **回复文本被累加多遍**：`TimelineObserver` 对每一跳帧都累加一次 `reply`。
  证据：日志中 `Generating TTS` 仅 1 次、`usage characters` 与回复长度相符 → TTS 只跑一次。
- **延迟表出现负值**：见 2.5。

---

## 4. 模型已固定

`nex-agi/Nex-N2.5-mini` 已**写死**在 `bot.py` / `test_e2e.py` / `test_llm.py` 的模块级常量中，
且**不读环境变量**。`.env` 只需配 `MODELSCOPE_API_KEY`。

依据：该模型直连首 token 460 ms（最快）、端到端 934 ms（最优）、
thinking 仅 0.005 s（无思考等待）。

---

## 5. 下一步

1. **换 STT**：瓶颈已转移到 Whisper(base) 的 0.66–0.78 s。
   可试 `--whisper small`（更准但更慢）或直接上云端 Deepgram（更快）。
2. **浏览器端跑通**：`bot.py` 仍缺 Deepgram + Cartesia 两个 Key。
   备选：把 `bot.py` 的 STT/TTS 换成本地 Whisper + Piper，即可零成本体验。
3. **修 D2**：启动时校验三个 Key，缺失直接 fail-fast。
4. **注意免费额度**：连续 10 次压测即触发 429；多轮对话需限速。

---

## 6. 踩坑记录（重要教训）

### 6.1 `load_dotenv(override=True)` 会盖掉命令行环境变量

排查过程中一度得出「nex 端到端 3595 ms」的错误结论。真因：

```python
# test_e2e.py 里的写法（当时）
base_url=os.getenv("MODELSCOPE_BASE_URL", ...),
model=os.getenv("MODELSCOPE_MODEL", "Qwen/Qwen3.8-Flash-Next"),
```

命令里传了 `MODELSCOPE_MODEL="nex-agi/Nex-N2.5-mini"`，
但 `.env` 里是 `MODELSCOPE_MODEL=Qwen/Qwen3.8-Flash-Next`，
而 `load_dotenv(override=True)` **让 `.env` 覆盖 shell 环境变量** → 实际跑的是 Qwen。

现场验证：

```
$ MODELSCOPE_MODEL=nex-agi/Nex-N2.5-mini \
  python -c "load_dotenv('demo.env', override=True); print(os.getenv('MODELSCOPE_MODEL'))"
  os.getenv 实际取到 -> Qwen/Qwen3.8-Flash-Next
```

**修复**：模型名改为模块级常量，彻底不读环境变量。

### 6.2 本次教训

1. **验证脚本的模型时，必须确认「实际生效」的配置**，不能只看命令里传了什么。
   日志里应打印最终生效的 model / base_url。
2. **做 A/B 对照前，确保两组只差一个变量**。本次因为 env 覆盖，
   两组其实跑的是同一个模型，差点得出错误结论。
3. **`override=True` 是把双刃剑**：保证 `.env` 权威，但会让临时命令行覆盖失效。
