> # 📗 pipecat-quickstart
> ## ✅ 当前维护的实现 —— **要开发就用这个**
>
> | | |
> |---|---|
> | **这是什么** | 能跑的实时语音对话 Agent，级联管线 `VAD → ASR → LLM → TTS` |
> | **需要几个 key** | **1 个**（只有 LLM 要钱；语音识别与合成全在本地跑） |
> | **状态** | **持续维护中** —— 第一阶段完成，第二阶段（写自己的业务）进行中 |
> | **总览** | ➡️ [`../README.md`](../README.md)（三个东西的区别） |
> | **找调研/基准脚本？** | ➡️ [`reference/pipecat-modelscope/`](reference/pipecat-modelscope/) （📕 已冻结，别在上面开发） |
>
> **本项目的配套文档**：
> - 📘 [`HANDBOOK.md`](docs/HANDBOOK.md) —— 第一阶段：怎么从零搭起来（插槽 / 配置 / 踩坑）
> - 📗 [`HANDBOOK-02.md`](docs/HANDBOOK-02.md) —— 第二阶段：怎么写自己的业务（工具 / 数据 / 编排 / 测试）
> - 📕 [`TOOL_TESTS.md`](docs/TOOL_TESTS.md) —— 工具调用压测报告（**引用数据前先读顶部的作废声明**）

---

# pipecat-quickstart（官方脚手架 + 魔搭 LLM + 全量日志）

由 **Pipecat 官方 CLI** 生成的语音 Agent，级联管线 `STT → LLM → TTS`。

**当前状态：前后端均已启动并实测通过，零报错。**

> 📘 **[HANDBOOK.md — 语音对话 Agent 搭建手册](docs/HANDBOOK.md)**
> 第一阶段总结：心智模型、从零复现步骤、四个模型插槽如何切换、实测数据、踩坑清单。
> 想「照着文档从零搭一遍」或做汇报，看这份；本文只讲**这个项目怎么跑**。
>
> 📗 **[HANDBOOK-02.md — 第二阶段：写自己的业务](docs/HANDBOOK-02.md)**
> 工具开发规范、schema 描述怎么写、数据接入、记忆双层设计、编排、测试纪律、完整示例。
>
> 📕 **[TOOL_TESTS.md — 工具调用压测报告](docs/TOOL_TESTS.md)**
> 实测结论与**可信度标注**（部分结论已作废）、未完成项与阻塞。
> 引用其中的数据前请先读顶部的作废声明。

> **语言约定（本项目全部文档适用）**：文档正文用中文；**代码、命令与终端输出一律按原文展示
> （英文纯 ASCII）**，不做中文意译 —— 便于直接复制运行，也避免非 UTF-8 控制台的编码问题。
> 例外：提示词、工具 `description` 与 `spoken` 朗读文案属于**功能内容**，在代码里本来就是中文。

## 配置一览

| 项 | 值 | 需要 key |
|---|---|---|
| 前端 | 官方内置客户端 `/client/` | 否 |
| 传输 | SmallWebRTC（浏览器直连） | 否 |
| **STT** | **SenseVoice（本地，默认）/ Whisper（可切换）** | **否** |
| **LLM** | **魔搭 ModelScope（默认）/ 共绩算力（可切换）** | **是（唯一）** |
| **TTS** | **Piper（本地，默认）/ Kokoro（可切换）** | **否** |

> **整个项目只需要填一个 `MODELSCOPE_API_KEY`。**
> STT / TTS 全部在本地运行，不产生任何云服务调用与费用。
>
> ⚠️ 默认 STT 引擎 **SenseVoice 需要额外依赖**（CPU 版 torch + funasr），用
> `uv sync --extra sensevoice` 一键安装（已配好 CPU 源，不会拖 CUDA 版）。
> 未安装时会**自动降级为 Whisper**（实测字错率 23.8% → SenseVoice **10.2%**）。降级会写告警，
> 且横幅 `[BOOT] STT (configured)` 之后紧跟一行 `[BOOT] STT in effect` —— 两者不一致即为降级。

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
# 想让默认的中文 STT（SenseVoice）真正生效，带 extra 安装（CPU 版 torch + funasr）：
uv sync --extra sensevoice

cp .env.example .env      # 按 LLM_PROVIDER 填对应密钥，其余已预设好

# 首次建议先预热本地模型（Whisper/SenseVoice + Piper 音色）——
# 否则会在「第一个会话建立时」才下载，期间用户第一句话无响应
uv run ../scripts/prewarm.py

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

## 切换 LLM 服务商（魔搭 / 商汤 / 共绩）

三个服务商都是 OpenAI 兼容接口，代码用的是同一个 `OpenAILLMService`，
切换**只改环境变量**，业务代码一行不动。默认仍是 **ModelScope**（保持原行为）。

在 `server/.env` 里设 `LLM_PROVIDER`：

| 值 | 服务商 | base_url | 密钥变量 | 模型变量 |
|---|---|---|---|---|
| `modelscope`（默认） | 魔搭 | `https://api-inference.modelscope.cn/v1` | `MODELSCOPE_API_KEY` | `MODELSCOPE_MODEL` |
| `sensenova` | 商汤日日新 | `https://token.sensenova.cn/v1` | `SENSENOVA_API_KEY` | `SENSENOVA_MODEL` |
| `suanli` | 共绩算力 MaaS | `https://api.suanli.cn/v1` | `SUANLI_API_KEY` | `SUANLI_MODEL` |

```bash
# 切到商汤
LLM_PROVIDER=sensenova
SENSENOVA_API_KEY=sk-...
SENSENOVA_MODEL=sensenova-6.8-flash-lite
```

要点：

- **「关思考」参数各家写法不同**，已按服务商内置
  （见 `settings.LLM_PROVIDERS[..]["thinking_body"]`）：魔搭 `enable_thinking=false`、
  商汤 `thinking={"type":"disabled"}`、共绩暂不注入。**商汤实测 `enable_thinking` 无效**，
  必须用 `thinking.type=disabled`，否则首 token 全耗在推理上（语音场景很致命）。
- **共绩的 `model` ID 形如「厂商/模型」**（如 `qwen/qwen3.8-27b`），
  以控制台/模型广场为准；名字写错会报错。
- 注意区分：共绩的**大模型云服务**是 `api.suanli.cn`，
  而它的**算力 Open API** 是 `openapi.suanli.cn`（另一套鉴权，与本项目无关）。
- 启动横幅 `[BOOT]` 会打印实际生效的「LLM 服务商 / 模型」，可据此确认切换是否生效。
- `scripts/verify_stack.py` / `scripts/verify_tools.py` 跟随同一套解析，可用 `--model` 临时覆盖模型名。

---

## 知识库 / RAG（非结构化文档检索）

给 Agent 接上**文档语义检索**能力（手册、合同、知识文章……）。它与 `query_data`
（结构化表精确查询）互补：一个查"文档里怎么写的"，一个查"数据库里是多少"。

**写入与查询分离**（同 HANDBOOK-02 的原则）：解析/切块/算向量在离线脚本里做，
运行时工具只查库 —— 保证检索是毫秒级，不会让用户多等几秒。

```bash
cd server
# 1) 灌文档（.md/.txt，可传文件或目录）
uv run ../scripts/ingest_docs.py ../README.md
uv run ../scripts/ingest_docs.py ../sample-data/product-faq.md   # 仓库自带的假手册（用于验证检索）
uv run ../scripts/ingest_docs.py --dir ./docs
uv run ../scripts/ingest_docs.py --list            # 看已入库（不加载模型）
uv run ../scripts/ingest_docs.py --delete <source> # 删除某篇（按 source）
uv run ../scripts/ingest_docs.py --prune           # 清掉「源文件已不存在」的文档

# 2) 之后正常对话即可 —— LLM 会自动调用 search_knowledge
uv run bot.py
```

> 文档标识（`source`）统一是**相对仓库根**的路径，所以从哪个目录调用、写相对还是绝对路径，
> 都会更新**同一篇**文档。早期版本按「调用时的原样路径」存：`../README.md` 与 `README.md`
> 会被当成两篇，检索时返回重复正文块，`--delete` 也必须一模一样地拼写才行。

| 环节 | 选型 | 可替换点 |
|---|---|---|
| 嵌入 | 本地 `BAAI/bge-small-zh-v1.5`（transformers+torch，**无需 key**，512 维） | `EMBEDDING_PROVIDER=api` + `EMBEDDING_BASE_URL/API_KEY` |
| 存储 | SQLite BLOB（float32 向量） | `KNOWLEDGE_DB` |
| 检索 | NumPy 余弦暴力扫描（几百~几千块毫秒级）+ **词面重排**（`RERANK_ALPHA=0.3`） | 只改 `server/knowledge.py::search()` |

分块与重排的默认值不是拍脑袋定的：用 `scripts/kb_eval.py`（语料 = 仓库自己的文档 + 15 个手写用例）扫过
分块 300–800 × 重叠 × 重排权重 × 候选池；出厂配置（500/50 + 词面重排 α=0.3）对比旧默认
（300/0 纯余弦）是 **hit@1 33%→53%、hit@3 40%→80%、MRR 0.41→0.69**。注意语料就是**本仓库自己的文档**，
所以文档一增补，绝对数字就会漂移（语料 201→247 块之后两档都下降过）—— 只有**同一次运行内**的相对比较有意义。
随时可复跑：

```bash
cd server && uv run ../scripts/kb_eval.py                      # 默认配置对比
cd server && uv run ../scripts/kb_eval.py --sizes 300,500 --overlaps 0,50 --alpha 0.3
```

> 为什么嵌入默认本地：**当前 LLM 服务商没有 embeddings 接口**（实测商汤
> `/v1/embeddings` 返回 404），且本地免费、不受单一服务商绑定 —— 与 STT/TTS 同思路。

工具注册点：`tools.py::build_tools()` 里的 `KNOWLEDGE_SCHEMA`。

---

## 可信性护栏：谎报执行检测

**问题**：模型在没调工具时会编造成功结果（「好的，已为你设置提醒」而其实什么都没做）。
语音场景用户看不到界面，只能相信它说的话 —— 这是最危险的失效模式。

**做法**：`guards.py` 里的观察者，收集「本轮成功调用的工具」与「本轮回答文本」，
回答结束时按规则判定，命中就写 `[GUARD]` 错误日志（可外挂回调上报前端）：

```
[GUARD] suspected false claim: reply claims 'set_reminder' but successful tool calls this turn were none
```

- 纯函数 `detect_false_claim(text, executed)` 可单测，规则见 `guards.py::ACTION_RULES`。
- **已接入强制拦截**：命中后向模型注入「如实说明未完成」的纠正并让它重答
  （同一动作只纠正一次，避免 `纠正→再谎报→再纠正` 的死循环）。
- **这是启发式**（动作词 + 应有的工具），会漏也会误报；最强的约束仍是
  「写操作走显式编排 / 确认流程」。

```bash
cd server && uv run pytest tests/test_guards.py -v   # 单独跑护栏用例
```

---

## 多步编排 / 上下文摘要 / 成功率度量

**多步编排（`flows.py`）** —— 模型**不会**自己串步骤（实测：问「我这边天气怎么样」，
它跳过「回想城市」这一步，自行编了个「北京」）。把固定的调用链写成**复合工具**，
顺序由代码保证：

```
my_local_weather  =  recall_fact(城市)  →  get_weather(city)
```

模型只需调 `my_local_weather`，中间顺序不依赖它。实测：「我这边天气怎么样」→
正确回想出「杭州」并报天气。跨阶段的业务对话再上框架自带的 `FlowManager`。

**上下文摘要** —— 用**框架自带**的 `LLMContextSummarizer`：它在 assistant 聚合器内部
创建并接好事件，我们只需在 `assistant_params` 里打开开关
（阈值见 `settings.build_summarization_config()`，默认 20 条未摘要消息）。超过阈值时把
**较早**的消息压成一条摘要，最近几条原文保留。实测：`51 -> 6` 条，可用
`scripts/verify_summarize.py` 复跑。

> 这一版之前是手写观察者（`summarize.py`），已删除 —— 框架已经提供了同样的能力，
> 而且额外带 token 阈值触发、手动触发与结果校验。这正是 `HANDBOOK.md` 第 7.1 节的教训：
> **动手写之前先把框架目录翻一遍**。

**成功率度量（`scripts/verify_tools.py --repeat N`）** —— 模型是否调工具是**非确定性**的，
样本量为 1 等于噪声。内建重复统计 + 四种**互斥**结论：

```bash
cd server && uv run ../scripts/verify_tools.py --question "现在几点了？" --repeat 20
```
实测（商汤，阳性对照）：
```
  [OK]   tool called                     5/5   100.0%
  [ERR]  call failed (API error)         0/5     0.0%
  [ERR]  no answer (request failed / pipeline stuck)  0/5   0.0%
  [WARN] self-answered (no tool call)    0/5     0.0%
```

`scripts/audio_probe.py` 同样把结论分成四类（打通 / 调用失败 / 无识别 / 不出声），便于定位。

---

## 业务数据：采集与查询分离（数据库侧）

**数据在数据层，不写在代码里。** 这是硬约束：连演示数据也是**文件**，不是代码里的字面量。

| 数据 | 放在哪 | 换掉它要改代码吗 |
|---|---|---|
| 业务行（指标 / 主机 / 告警 / 订单） | `sample-data/demo-business.json`（`DEMO_DATA_FILE` 可覆盖） | **不用** —— 换文件即可 |
| 订单导入 | `sample-data/orders.csv` → `scripts/collect_orders.py --csv` | **不用** |
| 知识库文档 | 任意 .md/.txt → `scripts/ingest_docs.py` → `server/data/knowledge.db` | **不用** |
| 示例工具数据（城市天气 / 设备清单） | `sample-data/sample-tools.json`（`SAMPLE_TOOLS_DATA` 可覆盖） | **不用** |
| 库文件本身 | `server/data/memory.db`、`server/data/knowledge.db`（`MEMORY_DB` / `KNOWLEDGE_DB`） | **不用** |
| **可查的表 / 列（你自己的库结构）** | `sample-data/business-schema.json`（`BUSINESS_SCHEMA_FILE` 可覆盖） | **不用** —— 在文件里声明你的表即可 |

**真实网络源（实测，不是假数据）**：两个入口都能直接抓公网上的合规来源，抓回来的东西进数据层，
和本地文件走同一条路：

```bash
cd server
# 非结构化：抓一份真实公开文档（示例用 Pipecat 官方仓库的 README）灌进知识库
uv run ../scripts/ingest_docs.py --url https://raw.githubusercontent.com/pipecat-ai/pipecat/main/README.md
# 结构化：抓一个公开 JSON API（示例用 GitHub 发布记录，无需 key）映射进 orders 表
uv run ../scripts/collect_orders.py --url "https://api.github.com/repos/pipecat-ai/pipecat/releases?per_page=3"
# 知识库完整性自检（损坏时直接给出重建步骤）
uv run ../scripts/ingest_docs.py --check
```

实测：抓回 41,447 字符 → **35 块入库**；入库后问「pipecat 支持哪几种传输方式？」，`search_knowledge`
命中并答出 **Daily(WebRTC) / FastAPI Websocket / LiveKit / SmallWebRTCTransport / Vonage /
WebSocket Server / WhatsApp**；问「怎么安装 pipecat？」答出 `uv add "pipecat-ai[option,...]"`
/ `pip install pipecat-ai`。这些内容**只在那份抓来的文档里** —— 所以这条链是
**真实网络 → 数据层 → 工具 → 模型**，不是代码里写死的。

> 换成你自己的源：文档直接 `ingest_docs.py --url <你的地址>`；结构化数据改
> `collect_orders.py::map_api_item` 那几行字段映射即可，其余管线不动。
> 抓取是**一次性请求**（不是爬虫）：请遵守目标站点的条款与 robots.txt。

> **SQLite 加固**：这两个库会被多个脚本分别打开（灌库 / 机器人 / 探针），因此连接统一使用
> **WAL + 5 秒忙等**。起因是实测中 `knowledge.db` 出现过一次 btree 损坏（`database disk image
> is malformed`，`integrity_check` 报 btreeInitPage 错误）；修复后补了 `--check` 与重建指引。
> 每个源都可重新获取，所以重建只是一条命令。

**把数据删掉会怎样**（不会假报成功）：删掉 `demo-business.json` → 启动时不灌演示数据（只写一行日志）；
查询答「没有查到符合条件的记录」，`count` 答「结果是 0」（这是真话），`sum` / `avg` 也答「没有查到」
—— 不会冒出 `None` 这类怪话；请求一个没在自己库里声明的表，明确答「查询没成功：未知的表 XXX」。

**换成你自己的库会怎样**（不用改代码）：实测 —— 用外部 sqlite3 建出 `tickets` 表，只在
`business-schema.json` 里声明它，`query_data` 立刻能列表 / 条件过滤 / 文本搜索 / `count` / `avg`；
没声明的表则明确报「未知的表」。

代码只做三件事：加载、校验（表名与列名必须在白名单内）、查询。所以换成你真实的数据来源时，
`server/*.py` 一行都不用动；接了真实采集后删掉 `demo-business.json`，加载器自动变成空操作。

> 有一条单测守着这件事：`test_demo_rows_come_from_the_data_file_not_the_code` 会把数据文件里的
> 订单号、主机名拿去 grep 源码 —— 谁把业务行写回代码里，测试就红。

### 分层与跨层联通性：怎么判断是"真联通"而不是"写死在代码里"

四层的边界，就是代码里的实际调用链：

| 层 | 在仓库里是什么 | 它怎么到达下一层 |
|---|---|---|
| 接口层 | `FunctionSchema`（工具名 / 参数 / 描述）、RTVI·WebRTC 传输、探针命令行 | 模型按 schema 发起调用 |
| 功能层 | `tools.py` / `sample_tools.py` 的 handler、`flows.py`、`guards.py` | 调用数据访问层的函数 |
| 数据访问层 | `memory.py` / `knowledge.py` / `embeddings.py` 里的查询函数 | 拼参数化 SQL / 读文件 |
| 数据层 | `server/data/*.db`、`sample-data/*`（路径可由环境变量指向别处） | —— |

**判据（关键）**："先写数据、再问工具、答得出来"**不构成证据** —— 写死在代码里的常量同样答得出来。
**证伪才有说服力**：**把数据层清空**，工具必须答"查不到"。常量给不出这个结果。

一条命令看全过程（用临时库与临时数据文件，不碰你的真实数据）：

```bash
cd server && uv run ../scripts/verify_layers.py
```

```
[1] 数据层 -> 数据访问层
    [OK] 空数据层 -> 工具答"查不到"（答案不是代码里的常量）
    [OK] 外部 sqlite3 客户端写入的行，立刻能被工具查到
    [OK] 数据文件 -> 加载器 -> 数据层 -> 工具读到文件里的值
[2] 代码声明的可查列 vs 数据库真实 schema：4 张表全部对得上（漂移否则要到运行时才炸）
[3] 替换数据访问层的函数 -> 工具结果随之改变（说明真的走了层接口，不是内联逻辑）
[4] 功能层 -> 接口层：12 个工具都有 handler；同一次接口调用，外部再写一行答案就变
[5] 模型 -> 工具的传输层：由 scripts/verify_tools.py 覆盖（一次真实 LLM 调用）
verdict: [OK] every layer boundary is connected for real
```

**两种方式的本质差异**：

| | 数据写死在代码里 | 分层后通过接口联通 |
|---|---|---|
| 换数据 | 得改 `.py`、重新发布 | 换文件 / 换 DB / 改环境变量，`.py` 不动 |
| 把数据层清空 | **仍然答得出来**（答案来自常量） | 答"查不到" |
| 外部客户端写入一行 | 工具看不到 | 立刻可见 |
| 单测能证明什么 | 只能证明常量**内部自洽** | 能证明跨层调用**真的到达了数据层** |
| 防回退 | 无 | 架构守卫单测：业务行出现在源码里就红 |

**谁在守这些边界**（`cd server && uv run pytest`）：

| 边界 | 守它的测试 |
|---|---|
| 数据层 ⇄ 数据访问层 | `test_answers_come_from_the_data_layer_not_from_constants` |
| 代码声明 ⇄ 真实 schema | `test_declared_columns_exist_in_the_real_schema` |
| 数据文件 ⇄ 数据层 | `test_swapping_the_data_file_changes_answers_without_code_changes` |
| 业务行不许回到代码里 | `test_demo_rows_come_from_the_data_file_not_the_code` |
| 有状态工具按会话隔离 | `test_reminder_state_is_per_session` / `test_device_state_is_per_session` |

工具的查询**不碰外部系统**，数据由独立的**采集脚本**写入本地库 —— 否则工具里现调接口
会让用户多等几秒，外部源挂了问答也跟着崩。

```bash
cd server
uv run ../scripts/collect_orders.py            # 采集内置示例数据入 orders 表
uv run ../scripts/collect_orders.py --csv ../sample-data/orders.csv   # 用仓库自带的假数据（18 笔，跑两次不翻倍）
uv run ../scripts/collect_orders.py --list     # 查看

# 之后模型可直接用 query_data 查：table=orders
```

- 示例表：`orders`（订单号唯一，采集用 upsert 以免重复）。
- **换数据源只改 `scripts/collect_orders.py::fetch_from_source`**，工具/提示词不用动。
- 新增业务表的完整步骤见 `HANDBOOK-02.md` 第 8 节。

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
| `[TURN]` | 一轮对话的时间线：`transcript`（识别文本）→ `to_llm`（发往 LLM 的上下文）→ `first_token`（LLM 回答）→ `first_audio`（开始出声）→ `latency`（分段延迟） |
| `[FRAME]` | 关键帧流水（DEBUG 级）：VAD、机器人说话起止、管线错误 |

实际输出示例：

```
[BOOT] ============================================================
[BOOT] Effective configuration for this run
[BOOT]   Session ID      = c601ecc1-8843-4aba-80ed-0fbb62d65c48
[BOOT]   LLM model       = nex-agi/Nex-N2.5-mini
[BOOT]   Disable thinking= True
[BOOT]   LLM key         = set [OK] (ms-594...4dea)
[BOOT]   STT (configured)= SenseVoice(iic/SenseVoiceSmall) local, no key (best for Chinese)
[BOOT]   TTS (configured)= Piper(zh_CN-huayan-medium) local, no key (first chunk 76ms)
[BOOT]   VAD stop secs   = 0.6s (official 0.2s)
[BOOT] ============================================================

[CLIENT] browser connected | id=xxx
[TURN] ----- turn 1 started -----
[TURN] user stopped speaking (VAD end of turn)
[TURN] transcript: '你好请用一句话接收一下。你自己。'
[TURN] -> context sent to LLM: [{'role': 'system', ...}, {'role': 'user', ...}]
[TURN] <- LLM response complete: '你好，我是你的语音助手，请告诉我你需要我做什么。'
[TURN] TTS first audio
[TURN] latency (baseline=end_of_speech): transcript 558ms | to_llm 560ms | first_token 1091ms | first_audio 1251ms
```

> 注：日志文案（含横幅键名、`[TURN]` 各阶段名）全部为**英文纯 ASCII**，
> 只有对话内容（用户语音识别文本、模型回复）本身仍是中文 —— 这是运行时数据，无法避免。

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
uv run ../scripts/verify_stack.py                          # 用 .env 的配置
uv run ../scripts/verify_stack.py --model Qwen/Qwen3.8-Flash-Next   # 换模型对比
uv run ../scripts/verify_stack.py --stop-secs 0.2          # 复现"被切成两段"的问题
uv run ../scripts/verify_stack.py --whisper small          # 换更大 STT 模型
```

实测输出（nex-N2.5-mini，2026-10-01 复测，Whisper base）：

```
speech end -> VAD end of turn      454 ms
speech end -> STT final text      1012 ms
speech end -> LLM first token     1545 ms
speech end -> TTS first audio     1705 ms
transcript : '你好请用一句话接收一下。你自己。'
model reply : '你好，我是你的语音助手，请告诉我你需要我做什么。'
TTS audio   : 200270 bytes
verdict: [OK] full path passed
```

三个模型均验证通过（管线内 LLM 首 token：nex 1545ms / Qwen 1963ms / deepseek 2054ms）。

> **口径提醒**：本表的基准是「合成音频推流结束」，而 WAV 尾部带静音，
> VAD 在推流结束前约 0.45 s 就已判定说完（表中第一行），因此
> **真实体感端到端 ≈ 1.7 s + 0.45 s ≈ 2.1 s**。
> 数字只用于**横向对比**（换模型、改配置前后的差值），不要当成用户感知延迟的绝对值。

**2026-10-05 复测（商汤 `sensenova-6.8-flash-lite` + SenseVoice）**：

```
speech end -> VAD end of turn     453 ms
speech end -> STT final text     859 ms
speech end -> LLM first token    2038 ms
speech end -> TTS first audio    2071 ms
verdict: [OK] full path passed
```

> 这次复测顺带修掉一个**误导性测量**：本脚本原先把 `user_turn_stop_timeout` 留在框架默认的
> 5 秒，而 SmartTurn 对这条固定音频判 `INCOMPLETE`，导致**每个阶段都虚高 5 秒**
> （修前 `LLM first token 6609ms`）。现已在脚本里显式限制为 0.5s，
> 详见 `HANDBOOK.md` 第 9 节第 11 条。
> 另外注意两次复测的**服务商不同**（上表是魔搭 nex，这里是商汤），数字不能直接相减。

### 无浏览器冒烟测试

`scripts/verify_stack.py` 走完整链路（含 STT/LLM/TTS），较慢；
只想快速确认「前端能打开 + WebRTC 能握手 + 后端装配无错」时用：

```bash
cd server
uv run ../scripts/smoke.py            # 自动拉起 bot.py 再测，测完自动关闭
uv run ../scripts/smoke.py --no-spawn # 只测已经跑起来的实例
```

```
  [OK] GET /client/ -> HTTP 200, 14203 bytes
  [OK] frontend asset references complete: True
  [OK] POST /api/offer -> HTTP 200
  [OK] no hard wiring errors found
verdict: [OK] frontend and backend handshake passed
```

### 文本通道探针（不用麦克风驱动真实 bot.py）

`scripts/smoke.py` 只验证到握手为止。想验证**一整轮真实对话**、又不想开口说话时用：

```bash
cd server
uv run ../scripts/text_probe.py                            # 默认问「现在几点了？」
uv run ../scripts/text_probe.py --question "今天星期几"
```

它通过真实 WebRTC 数据通道发 RTVI `send-text` 消息 —— 与官方 Prebuilt 前端
点「发送」走的是**同一条路径**。实测输出（2026-10-01）：

```
  RTVI messages the frontend received this turn (count):
     bot-llm-text                       x31
     metrics                            x23
     bot-output                         x3
     user-llm-text                      x2
     bot-transcription                  x2
     bot-tts-started                    x2
     bot-started-speaking               x2
     llm-function-call-started          x1
     llm-function-call-in-progress      x1
     llm-function-call-stopped          x1
  tool: [TOOL] [OK] completed get_current_time (id=xxx)
  spoke: 58 time(s)
```

这份输出同时回答了「前端不动的话能拿到什么」：字幕、延迟指标、说话状态、
工具调用过程，全部是现成的，后端不需要改前端一个字符。

### 工具调用自检

```bash
cd server
uv run ../scripts/verify_tools.py                          # 只测后端，不经过音频
```

直接驱动真实管线，检查「工具是否被调用 + 是否有最终回答」。

> **注意**：**模型是否调用工具是非确定性的** —— 同一个问题有时调用、
> 有时凭自身知识直接回答。因此通过条件是「拿到了最终回答」，
> 工具是否调用另行报告（见脚本输出）。

**踩过的两个坑**（都在 `scripts/verify_tools.py` 里有注释，写新工具时务必注意）：

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
uv run ../scripts/audio_probe.py
```

把 WAV 通过**真实 WebRTC 音频轨**送进真正跑起来的 `bot.py`，
与浏览器麦克风走同一条路径：VAD → **SenseVoice** → LLM → Piper → 音频轨回传。
这是唯一覆盖到 STT 与 VAD 的自动化验证（文本通道会绕过它们两者）。

实测（2026-10-01，客户端侧计时，基准 = **用户说完的那一刻**）：

| 节点（探针输出标签） | Whisper base（改前） | **SenseVoice（当前）** |
|---|---|---|
| `user transcript`（收到识别文本） | 1218 ms | **934 ms** |
| `LLM started`（LLM 开始生成） | 1233 ms | **935 ms** |
| `first answer token`（收到首个答案 token） | 1731 ms | **1424 ms** |
| `TTS started`（TTS 开始合成） | 1842 ms | **1527 ms** |
| **`bot speaking`（机器人开始出声）** | **2038 ms** | **1721 ms** |

后端日志独立测得的 `first_audio`（首次出声）为 1160 ms，但它的基准是 **VAD 判定说完**
（比真实说完晚约 0.56 s）。1160 + 561 ≈ 1721 —— 两套口径互相印证。

> **测试音频是 Piper 合成的，不是真人语音**，且线上还会经过 Opus 压缩。
> 因此这里的识别结果是**保守下界**，真人通过麦克风输入通常会更好。
> 目前合成音频上「介绍」仍会被认成「接收」，但整句语义不受影响。

---

## ASR / TTS 引擎选型（都本地免费，可随时切换）

两个引擎**都本地运行、无需 key、零费用**，但中文表现差距很大。
用 `scripts/asr_bench.py` 量化（6 句中文，标准答案已知）：

```bash
cd server
uv run ../scripts/asr_bench.py                              # 只测 base（无下载）
uv run ../scripts/asr_bench.py --models base small sensevoice
```

| ASR 引擎 | 字错率 | 完全正确 | 单句耗时 |
|---|---|---|---|
| Whisper `base`（原方案，已配普通话 `initial_prompt`） | 23.8% | 1/6 | 607 ms |
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

全部集中在 `server/`，共 17 处（第 13–17 条是第二阶段新增，详见上文各专节与 `HANDBOOK-02.md`）：

| # | 改动 | 原因 |
|---|---|---|
| 1 | LLM：OpenAI → 魔搭 ModelScope | 只有 ModelScope 的 key；它是 OpenAI 兼容接口，服务类仍是官方的 `OpenAILLMService` |
| 2 | 新增 `extra_body={"enable_thinking": False}` | 关闭思考模式，Qwen/DeepSeek 快 3 倍 |
| 3 | Whisper 默认值改为 `base` | 官方默认 `...-medium.en` 是**纯英文**模型，中文须用多语种 |
| 4 | VAD `stop_secs` 0.2 → 0.6 | **关键**：官方默认会把一句中文按逗号停顿切成两段（详见下节） |
| 5 | 提示词中文化 | 官方英文 prompt 会被中文音色读得很难听 |
| 6 | 新增 `pipeline_logging.py` 与观测器 | 全量日志，跑一次即可定位问题 |
| 7 | 新增 `settings.py` | 默认值与本地服务构造（`build_stt`/`build_tts`）与 `scripts/verify_stack.py` 共用，避免「测的」和「跑的」配置漂移 |
| 8 | 缺 `MODELSCOPE_API_KEY` 时 **fail-fast** | 早期只打一行 ERROR 就照常启动：浏览器能连上、握手也成功，但一开口必然没反应，看起来像网络故障。现在直接终止并给出填 key 的步骤 |
| 9 | Whisper 加 `initial_prompt="以下是普通话的句子。"` | base 模型会把中文转成繁体（「请」→「請」）。实测已修，且量级不小：**CER 41.3% → 23.8%**（`scripts/asr_bench.py`，不加提示词的那两档逐句都是繁体） |
| 10 | 开场白角色 `developer` → `user` | **修掉了一个静默失败**（详见下节）：魔搭接口不认 `developer`，且它留在上下文里会让**后续每一轮都失败** |
| 11 | 新增 `tools.py`（function calling） | 后端能力的扩展点；前端无需改动，pipecat 以 `llm-function-call*` 消息推送，Prebuilt 前端自动渲染 |
| 12 | 新增故障上报（`ErrorObserver` → RTVI `error`） | 服务失败时前端原本毫无提示（连得上、握得手、但没反应）。现在错误同时写 `[ERROR]` 日志并推到前端 |
| 13 | 新增 `memory.py`（SQLite：会话历史 / 长期记忆 / 业务表） | 框架**不提供任何持久化**；且 `TurnRecorder` 必须是**观察者**，挂在管线末端收不到文本帧（两侧文本都被各自的聚合器消费） |
| 14 | 新增 `knowledge.py` + `embeddings.py`（RAG） | 框架没有知识库；嵌入默认走本地模型，不受单一服务商绑定（多数服务商无 `/v1/embeddings`） |
| 15 | 新增 `guards.py` + 纠正回调 | 「**谎报执行**」是最危险的失效模式：没调工具却声称已完成，用户基于虚假状态做决策 |
| 16 | 新增 `flows.py`；上下文摘要改用**框架自带**的 `LLMContextSummarizer` | 模型不会自己串多步任务（会跳过步骤并自行编造参数）；摘要由框架在 assistant 聚合器内实现，`enable_auto_context_summarization=True` 即接线完成 |
| 17 | 新增 `tests/`（109 个单元测试） | 回归不必再跑分钟级全链路；覆盖配置解析、工具 handler、**SQL 注入对抗**、知识库、护栏、编排、摘要、观察者落库、工具状态隔离与异常兜底、抓取解析与字段映射 |
| 18 | 工具改为**按会话**构建（`build_tools(session_id)`） | 与「会话 ID 不再放模块级」同源：`set_reminder` 的状态原本是模块级列表，多会话时 A 的提醒会计进 B 的计数，而且无上限增长 |
| 19 | 所有工具 handler 统一兜底（`safe_handler`） | 实测缺陷：`query_data` 的 `limit="十条"` 在 handler 内抛 `ValueError`，没人接住 → 工具跑了但没有结果回到模型，**机器人永不回答**（与忘了 `run_llm=True` 同类的静默失败）。在 `build_tools()` 集中包装，新加工具不会漏 |
| 20 | 知识库写入侧：稳定 `source` + `--prune` | 原来用「调用时的原样路径」当文档标识：`../README.md` 与 `README.md` 会被当成两篇 → 检索重复命中、`--delete` 必须拼写一致。改为相对仓库根，并支持清掉源文件已不存在的记录（否则会检索到已删除的内容） |
| 21 | 数据层对抗性验证；`search` 与时间戳修复 | 文档声称 SQL 层「白名单 + 结构化参数」，用 20 个注入样本实测确认成立（表名/列名/排序/聚合全被拦，敏感表不可达，库完好）。同一探针却查出：`search` 用**全局**列名名单，`orders` 没有那些列名 → 搜索被静默忽略、返回未过滤的行（`search="绝不存在zzz"` 也能返回 10 行）；`orders.updated_at` 的原始时间戳会进 `spoken` 被念出来。改为每表一份 `TEXT_COLUMNS`、无可用列时报错而非沉默，并格式化任意 `*_at` 字段 |
| 22 | 业务数据搬出代码，进**数据文件** | 演示业务行原本是 `memory.py` 里的字面量（`bot.py` 直接调用），示例工具的城市天气 / 设备清单也一样写在 `sample_tools.py` 里 —— 这正是"假数据写死在代码里假装数据层"。改为 `sample-data/demo-business.json` / `sample-tools.json`，`DEMO_DATA_FILE` / `SAMPLE_TOOLS_DATA` 可覆盖，代码只做加载 + 白名单校验；设备状态同时改为按会话（与提醒一致）。加了一条架构守卫单测：业务行出现在源码里就红 |
| 23 | 新增逐层联通性探针 `scripts/verify_layers.py` + 两条边界单测 | "先写数据再问工具、答得出来"**不构成证据**（写死的常量也能答）。改为**证伪**：清空数据层必须答"查不到"、外部 sqlite3 客户端写入必须立刻可见、替换数据访问层函数必须改变工具结果、代码声明的列必须与真实 schema 一致、换数据文件必须改变接口答案。8 项检查全过，并把「写死在代码里 vs 分层联通」的差异写进文档 |
| 24 | 可查的表/列改为**数据文件**声明；空数据不再念出 `None` | ①白名单原本写死在 `memory.py`，换成你自己的库还得改代码 —— 改为 `sample-data/business-schema.json`（`BUSINESS_SCHEMA_FILE` 可覆盖），实测外部建的 `tickets` 表只要在文件里声明就能列表/过滤/搜索/聚合，没声明的表明确报"未知的表"；②数据为空时 `sum/avg` 会答"结果是 None"（把 Python 的 None 念出来），改为数据访问层标记"无数据"、功能层答"没有查到符合条件的记录"，`count` 的 0 仍如实回答 |

### 为什么必须改 VAD（第 4 点）

官方默认 `stop_secs=0.2` 把一句中文在逗号处判定为「说完」，STT 因此分段转写，
LLM 只收到半句就作答。实测日志：

```
30.650  Transcription: [你好请用一句话接收一下 ]   ← 只收到前半句
30.653  User stopped speaking
30.903  User started speaking                     ← 同一句又"重新开始"
31.952  Transcription: [你自己 ]                  ← 后半句
model reply : 好的，我已经收到，请说。                  ← 因为输入是残缺的
```

改为 `0.6` 后：

```
all segments : ['你好请用一句话接收一下,你自己。 ']    ← 整句完整
model reply  : 你好，我是语音助手，可以随时帮你解答问题、整理信息或陪你聊天。
```

代价：只多等约 0.2 s。

### 为什么开场白角色必须是 `user`（第 10 点）

这一条是**靠新加的故障上报才发现的**：会话一建立，日志里马上出现

```
[ERROR] OpenAILLMService#0 | invalid_request | service unusable | exception=BadRequestError
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
│   ├── settings.py          # 默认值 + 本地服务构造的唯一来源（bot 与自检共用，防漂移）
│   ├── tools.py             # LLM 可调用的工具注册中心（新增能力改这里）
│   ├── sample_tools.py      # 示例工具集（天气/计算/换算/设备/通知/提醒）
│   ├── memory.py            # 本地持久化：会话历史 + 长期记忆 + 业务表 + 落库观察者
│   ├── embeddings.py        # 文本嵌入（RAG 底座，本地 bge / OpenAI 兼容可切换）
│   ├── knowledge.py         # 知识库：文档切块 + 向量检索（换向量库只改这里）
│   ├── flows.py             # 显式编排：多步工具链（复合工具，顺序由代码保证）
│   ├── guards.py            # 可信性护栏：谎报执行检测 + 强制纠正
│   ├── pipeline_logging.py  # 日志、对话时间线、故障上报（[ERROR] → 前端）
│   ├── tests/               # pytest 单元测试（配置/工具/SQL/知识库/护栏/编排/摘要）
│   ├── pyproject.toml       # 依赖（含 sensevoice 可选 extra）
│   ├── .env.example         # 密钥与服务商模板（按 LLM_PROVIDER 填）
│   ├── .env                 # 真实密钥（已被 .gitignore 忽略）
│   └── logs/                # 运行时日志（*.log 已被 .gitignore 忽略）
├── sample-data/             # 数据层样例（都是**文件**，不是代码）：orders.csv（18 笔订单）+ product-faq.md（测试 RAG 用）
│                            #   + demo-business.json（指标/主机/告警/订单，启动时加载）+ sample-tools.json（城市天气/设备）
│                            #   + business-schema.json（可查的表/列声明：换库不用改代码）
│                            #   + verify-input-zh.wav（音频探针用的 16k 中文音频；**未入库**，换机器需自备）
├── scripts/                 # 独立脚本：自检 / 探针 / 基准 / 采集（不再散在根目录）
│   ├── verify_stack.py      # 端到端自检（完整链路，较慢）
│   ├── verify_tools.py      # 工具调用自检（只测后端）+ --repeat 成功率统计
│   ├── verify_summarize.py  # 上下文摘要自检（撑过阈值，验证框架真的触发压缩）
│   ├── verify_layers.py     # 分层联通性自检（含"清空数据层必须答查不到"的证伪）
│   ├── smoke.py             # 无浏览器冒烟测试（前端 + 握手 + 装配）
│   ├── text_probe.py        # 文本通道探针（真实 bot.py + 真实 WebRTC）
│   ├── audio_probe.py       # 音频链路探针（真实音频进 / 出，含 STT 与 VAD）
│   ├── asr_bench.py         # ASR 基准：多配置中文识别字错率 / 耗时对比
│   ├── live_asr_bench.py    # 真实链路 ASR 基准（经 Opus 编解码）
│   ├── kb_eval.py           # 知识库检索质量评测（分块 / 重排 / 候选池 参数扫描）
│   ├── ingest_docs.py       # 把文档灌入知识库（RAG 写入侧）
│   ├── collect_orders.py    # 业务数据采集示例（采集与查询分离）
│   └── prewarm.py           # 预热本地模型（首次运行前跑一次）
├── docs/                    # 文档（README 留在根目录当入口）
│   ├── HANDBOOK.md          # 第一阶段：怎么搭起来（插槽 / 配置 / 踩坑）
│   ├── HANDBOOK-02.md       # 第二阶段：怎么写自己的业务（工具 / 数据 / 编排 / 测试）
│   └── TOOL_TESTS.md        # 工具调用压测报告（引用数据前先读顶部的作废声明）
├── reference/               # 参考案例（只读，不再开发）
│   └── pipecat-modelscope/  #   已冻结的调研项目：模型选型报告 + 基准脚本 + ANALYSIS.md
└── README.md                # 本文（入口）
```

> **为什么有 `settings.py`**：`bot.py` 与 `scripts/verify_stack.py` 需要同一批默认值
> （模型、VAD 阈值、提示词……）**以及同一套 STT/TTS 构造逻辑**（`build_stt`/`build_tts`）。
> 若各写一份，改了一侧而没改另一侧，
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
