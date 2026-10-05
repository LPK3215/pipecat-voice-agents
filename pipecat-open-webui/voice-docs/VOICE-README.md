# pipecat-open-webui

**把语音能力做成一个大系统里的一个组件** —— 它只负责"听"和"说"，**被**大系统按需调用。

> **状态**：🚧 **宿主已落地，语音模块待实现**
>
> - ✅ 已选宿主 **Open WebUI**（`v0.11.4`），并接入 LLM（商汤 SenseNova）—— 已能对话
> - 🚧 本项目自己的**语音模块**尚未实现：接口契约见 [`ARCHITECTURE.md`](ARCHITECTURE.md)（草稿）
> - 选型依据见 [`PLATFORM-SELECTION.md`](PLATFORM-SELECTION.md)
>
> **分工**：本文只写这个项目（"组件形态"）。另外两个项目在别处：
> 管线搭建与插槽 → [`../../pipecat-quickstart/docs/HANDBOOK.md`](../../pipecat-quickstart/docs/HANDBOOK.md)；
> "语音模块当主人"的那种形态 → [`../../voice-module-dify/README.md`](../../voice-module-dify/README.md)。

---

## 它和另外两个项目是什么关系

```
① pipecat-quickstart（第一 / 二阶段）
   语音管线搭通 → 往管线里灌业务（工具 / 知识库 / 记忆都在自己手里）

② voice-module-dify（第三阶段 · 已经做完并验证）
   网页 ──▶ 语音模块 ──▶ 平台（Dify）
   ↑ 语音模块是"主人"：它主动去调平台问答案

③ pipecat-open-webui（本项目 · 宿主已落地）
   Open WebUI（大系统 A）──▶ 语音组件 B（本项目要做的）
   ↑ 大系统是"主人"：它需要出声/听声时，才调用 B
```

| | ② `voice-module-dify` | ③ `pipecat-open-webui`（本项目） |
|---|---|---|
| 谁调用谁 | **语音模块 → 平台**（B 主动问 A） | **大系统 → 语音组件**（A 按需调 B） |
| 语音在系统里的地位 | 就是那个产品本身 | **十分之一的一个部件** |
| 思考在哪 | 外部平台 | **大系统自己**（B 不管） |
| 会话 | 单会话（一个人一个进程） | **多会话**（A 里很多人/很多任务同时用） |
| 对外暴露 | WebSocket（裸 PCM + 事件） | **一组明确的接口**（建房 / 说话 / 播报 / 关房 + 鉴权） |
| 适合 | 独立做一个语音助手 | **已经有一个强系统，只想给它装上耳朵和嘴** |

**一句话**：②是"做一个语音产品"，③是"**给别人的系统装一个语音零件**"。

## 当前真实结构

本仓库根目录 = **Open WebUI 源码**（上游原样保留）+ 我们的新增：

```
pipecat-open-webui/                     ← 仓库根 = Open WebUI v0.11.4
├── backend/  src/  static/  ...        ← 上游 Open WebUI（不改）
├── voice-docs/                         ← 【我们】项目文档
│   ├── VOICE-README.md                 ← 本文件：定位与关系
│   ├── ARCHITECTURE.md                 ← 语音模块接口契约（草稿）
│   ├── PLATFORM-SELECTION.md           ← 宿主选型调研
│   └── CHANGELOG.md                    ← 本分支变更记录
├── docs/                               ← 【我们】架构图（脚本生成）
├── CONTRIBUTING.md / FAQ.md / AUTHORS / LICENSE-SUPPLEMENT.md   ← 【我们】
└── .env                                ← 本地配置（已 gitignore）
```

> 下面这段是**最初**为独立项目画的"计划结构"（`server/` `probes/` `deploy/`），保留作设计参考 ——
> 实际形态已改为"**寄生在 Open WebUI 里**"：语音模块将作为它的扩展（Function / Pipeline）接入。

```
（历史计划，未采用）
pipecat-open-webui/
├── README.md
├── docs/ARCHITECTURE.md
├── server/     ← 计划：服务外壳（HTTP 接口 + 会话管理 + 鉴权）
├── probes/     ← 计划：探针（多会话隔离、并发、主动播报、延迟）
└── deploy/     ← 计划：部署件
```

## 这个组件对外提供什么（一页纸契约）

| 能力 | 形态 | 说明 |
|---|---|---|
| 开一个语音会话 | `POST /v1/sessions` | 返回 `session_id`；音频通道与它绑定 |
| 收音频 / 送音频 | `WS /v1/sessions/{id}/audio` | 二进制帧＝PCM 音频，文本帧＝JSON 事件（**沿用②已验证的契约**） |
| 让它主动说一句 | `POST /v1/sessions/{id}/say` | 大系统想播报什么（通知、结果）时用 —— 这是②没有的能力 |
| 关掉 | `DELETE /v1/sessions/{id}` | 释放资源 |
| 鉴权 | `Authorization: Bearer …` | 每个会话独立、可撤销 |

细节（字段、错误码、事件词表、超时与上限）全部在 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

## 下一步（按顺序，且都要可复跑）

1. 定 4 个决策（见 ARCHITECTURE 第 8 节）—— **已定**：宿主 = Open WebUI；音频 = 浏览器网页；鉴权 = 静态 Bearer；复用②代码。
2. 写**最小实现**：一个会话、一条音频通道、一次主动播报。
3. 配**探针**：会话隔离（两路音频不串）、并发、播报、延迟 —— 每一条都要有数字。
4. 再接到 Open WebUI（通过它的 `Function` / `Pipeline` 扩展点，而不是改核心）。

## 诚实说明

- 接口是据②的经验拟的**草稿**，不是既成事实 —— 改哪一条都行。
- **语音模块还没有一行代码** —— 别把它当成"已经能用"。
- ②里已验证过的东西（PCM+事件双通道、事件词表、打断/填场/分句旋钮、探针写法）**可以直接搬**；
  要新写的是**服务外壳**那部分（清单在 ARCHITECTURE 第 7 节）。
