# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.0.1] - 2026-10-05

首个版本：把语音能力做成一个**可被接入的模块**（耳朵和嘴巴），思考交给外部的智能体系统。

### 新增

- **语音管线**：`VAD → STT → 轮流判定 → [适配器] → TTS`，基于 Pipecat。
- **两个传输入口**：
  - `server/app.py` —— SmallWebRTC（本地直连，浏览器）。
  - `server/ws_app.py` —— **WebSocket**（媒体走 TCP，能过任何 HTTP 端口转发）。
- **平台适配器**（`server/brain.py`）：说完才问；边收边按句喂 TTS（首句即开口）；平台慢时填场；插话则停。
- **平台客户端**（`server/agent_client.py`）：HTTP + SSE 流式 + 叫停（**换平台只改这一个文件**）。
- **页面上的过程事件通道**：思考 / 工具调用 / 工具结果 / 流程节点 / 每轮三段耗时，实时推送。
- **网页客户端**（`agent/deploy/voice-client.html`）：麦克风 → 16k PCM → WebSocket，回放 PCM。
- **应用定义即文件**（`agent/app.dsl.yml`）+ 导入脚本（`agent/import_app.py`）。
- **可调项**（每一项都带实测依据，见 `.env.example`）：`STT_PROMPT`、`VAD_STOP_SECS`、
  `SPEAK_MIN_CHARS`、`FILLER_TEXT` / `FILLER_DELAY_SECS`。

### 验证

- **4 个可复跑探针**：装配 / 脑袋接口 / 说话听话 / 端到端音频。
- **33 个秒级单元测试**（不联网、不加载模型）。
- 实测：平台首字 **1334 ms**；平台首字 → 首句 → 交给 TTS **+12 ms**；平台自身首字 **740–963 ms**；
  端到端音频往返 **318,720 字节 / 10.0 秒**。
- 真人对着浏览器说话这一整条路已由使用者实测确认。

### 文档

- `README.md` —— 项目定位与怎么跑。
- `CASE-dify.md` —— 接 Dify 的完整流程、必须人工的三步、实测踩坑与数字。
- `docs/CONCEPTS.md` —— 心智模型与答疑（每条结论带验证方式）。
- `docs/PORTING.md` —— 换成你自己的平台：契约 + 清单。
- `docs/HANDBOOK-03.md` —— 阶段手册（从零搭的过程、已验证/未验证分开写）。
