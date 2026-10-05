# 贡献指南

感谢你考虑为本项目做贡献。

> **文档纪律**：同一件事**只在一处写详细版**，其余地方只留一句结论 + 链接 —— 避免多处说法分叉。
> **语言约定**：代码与终端输出保持 ASCII；但**朗读文案、提示词、中文正则**属于功能内容，故意用中文。

## 开发环境

前置要求：Python **>= 3.12**，以及 [uv](https://docs.astral.sh/uv/)。

```bash
cd voice-module-dify
uv sync
cp .env.example .env      # 只需填 BRAIN_BASE_URL / BRAIN_API_KEY；本地自测可设 BRAIN_STUB=1

# 离线自测（不连真平台，用一个本地替身当脑子）
uv run python probe/verify_brain.py
uv run python probe/verify_speech_legs.py
```

> 第一次运行会下载语音模型（Whisper + Piper），所以第一次慢、之后是秒级。

## 提交前检查

```bash
uv run pytest              # 秒级单测（不联网、不加载模型）
uv run ruff check .        # lint（规则见 pyproject.toml）
```

改动涉及能力或接口时，请**同时跑对应探针**（见 `README.md`「验证」一节），并在 PR 中附上实测输出 —— 本项目的原则是「结论不靠看着像对」。

## 提交信息规范

提交信息使用中文，采用 `类型: 说明` 格式：

| 类型 | 含义 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修复缺陷 |
| `docs` | 文档 |
| `refactor` | 重构（不改变行为） |
| `test` | 测试 |
| `style` | 格式（不影响逻辑） |
| `chore` | 杂项 |

## 代码约定

- **与 `pipecat-quickstart` 保持解耦**：可以读它的文档，但**不要 import 它的模块或复制实现**；两者只共用框架本身（`pipecat-ai`）。
- **业务逻辑不在这里**：工具 / 知识库 / 记忆 / 编排属于平台那一侧，本模块只负责听与说。
- **换平台只改一个文件**：`server/agent_client.py`。
- **新增传输入口**时，保持 `server/raw_pcm_serializer.py` 的裸 PCM 契约不变。

## 许可证

向本项目提交贡献，即表示你同意你的贡献以本项目的 [MIT 许可证](LICENSE) 授权。
