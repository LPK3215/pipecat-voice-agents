# 贡献指南

感谢你考虑为本项目做贡献。

> **语言约定（与本仓库文档一致）**：文档正文用中文；**代码、命令与终端输出一律按原文展示
> （英文纯 ASCII）**，不做中文意译 —— 便于直接复制运行，也避免非 UTF-8 控制台的编码问题。
> 例外：提示词、工具 `description` 与 `spoken` 朗读文案属于**功能内容**，在代码里本来就是中文。

## 开发环境

前置要求：Python **>= 3.11**，以及 [uv](https://docs.astral.sh/uv/)。

```bash
cd server
uv sync --extra sensevoice      # 含默认中文 STT（SenseVoice）所需依赖
cp .env.example .env            # 按 LLM_PROVIDER 填对应密钥（唯一需要注册的地方）
uv run ../scripts/prewarm.py    # 首次运行前预热本地模型（Whisper/SenseVoice + Piper）
uv run bot.py                   # 浏览器打开 http://localhost:7860/client
```

## 提交前检查

```bash
cd server
uv run pytest                   # 单元测试（秒级，不联网、不加载模型）
uv run ruff check .             # lint（server 目录）
uvx ruff check ../scripts/      # lint（脚本目录）
```

改动涉及某项能力时，请**同时跑对应探针**（见 `README.md`「端到端自检」一节），
并在 PR 中附上实测输出 —— 本项目的原则是「结论不靠看起来对」。

## 提交信息规范

提交信息使用中文，采用 `类型: 说明` 格式，类型参考：

| 类型 | 含义 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修复缺陷 |
| `docs` | 文档 |
| `refactor` | 重构（不改变行为） |
| `test` | 测试 |
| `style` | 格式（不影响逻辑） |
| `chore` | 杂项 |

## 代码与文档约定

- **新增工具**：在 `server/tools.py` 的 `build_tools()` 中注册；`description` 要写清
  **边界**（何时不该调用），因为描述重叠正是模型选错工具的主因。
- **新增业务数据**：放进 `sample-data/` 的数据文件，**不要把业务行写进代码**
  （有架构守卫单测会在业务行出现在源码里时变红）。
- **新增能力**：先检查框架是否已提供（见 `docs/HANDBOOK.md` 第 7.1 节：动手写之前先把框架目录翻一遍）。
- **文档纪律**：同一件事只在一处写详细版，其余地方给链接，避免多处说法分叉。

## 许可证

向本项目提交贡献，即表示你同意你的贡献以本项目的 [MIT 许可证](LICENSE) 授权。
