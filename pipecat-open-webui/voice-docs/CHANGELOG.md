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

### 移除（相对上游）

- `CODE_OF_CONDUCT.md`、`contribution_stats.py`、`demo.png`、`banner.png`、`TROUBLESHOOTING.md`、`.github/`、`docs/SECURITY.md`
- **说明**：这些是上游的元数据 / 宣传 / CI 文件，与本分支「语音实验」无关，移除**不影响运行**

### 变更

- **项目更名**：`voice-bridge` → **`pipecat-open-webui`**（Open WebUI + Pipecat 组合命名；与 `pipecat-quickstart` 一脉相承）
- 启动方式改为**瘦身模式**：`requirements-slim.txt` + **`USE_SLIM_DOCKER=true`**（适配纯 CPU 云服务器）
- 前端构建需 `NODE_OPTIONS=--max-old-space-size=10240`（否则 OOM）
- 监听端口：默认 `8080` → 本环境改用 **`8000`**（8080/3000 被环境占用）

### 保留（法律要求，未改动）

- `LICENSE` / `LICENSE_NOTICE` / `LICENSE_HISTORY` / `CONTRIBUTOR_LICENSE_AGREEMENT`
- 界面上的 "Open WebUI" 品牌

### 已知问题 / 待办

- 语音模块尚未实现（`voice-docs/ARCHITECTURE.md` 仅为契约草稿，无代码）
- 尚未接入大模型 API（需在界面 Settings → Connections 配置）
