# Contributing to voice-bridge

> `voice-bridge` 是 **[Open WebUI](https://github.com/open-webui/open-webui)** 的二次开发分支（fork），
> 用于实验「把实时语音能力作为一个零件，插入现成的 agent 系统」。**测试性质，非生产项目。**

## 先读这个：本仓库与上游的关系

| | 说明 |
|---|---|
| 上游 | [open-webui/open-webui](https://github.com/open-webui/open-webui)（基线 `v0.11.4`） |
| 本分支 | 只做**语音相关**的增量改动，其余尽量与上游保持一致 |
| 许可证 | **沿用上游 Open WebUI License**；本分支**不改变授权**、**不移除 "Open WebUI" 品牌**（见 [`LICENSE`](./LICENSE)） |

**核心原则：能不动上游代码就不动。** 语音能力优先通过官方扩展点接入
（`Function` / `Tool` / `Pipeline`，见 [`voice-docs/PLATFORM-SELECTION.md`](./voice-docs/PLATFORM-SELECTION.md)），
而不是直接改核心。

## 本分支新增的东西

| 路径 | 内容 |
|---|---|
| `voice-docs/` | 本分支的定位、架构契约、平台选型、变更记录 |
| `docs/` | 架构图等可视化产物（由 `scripts/visualization/` 生成，不手改产物） |

## 本地开发

```bash
# 1. 后端依赖（瘦身模式，适合纯 CPU 机器）
cd backend
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements-slim.txt

# 2. 前端依赖与构建
cd ..
npm ci
NODE_OPTIONS="--max-old-space-size=10240" npm run build

# 3. 启动（注意 USE_SLIM_DOCKER 变量名）
cd backend
USE_SLIM_DOCKER=true FRONTEND_BUILD_DIR="$PWD/../build" PORT=8000 \
  PATH="$PWD/.venv/bin:$PATH" ./start.sh
```

> 更多坑与解决见 [`FAQ.md`](./FAQ.md)。

## 提交前检查清单

- [ ] **不改** `LICENSE` / `LICENSE_NOTICE` / `LICENSE_HISTORY` / `CONTRIBUTOR_LICENSE_AGREEMENT`
- [ ] **不动**界面上的 "Open WebUI" 品牌
- [ ] **不删**根目录 `CHANGELOG.md`（运行时会被读取，见 FAQ Q2）
- [ ] 语音改动尽量收敛在独立目录，便于与上游同步（`git diff upstream/main --stat`）
- [ ] 改了 `scripts/visualization/` 下的脚本后，重新运行它更新 `docs/` 产物

## 与上游同步

```bash
git remote add upstream https://github.com/open-webui/open-webui.git
git fetch upstream
git diff upstream/main --stat       # 查看本分支相对上游的改动
```

## 可视化脚本规范

- 生成脚本统一放在 `scripts/visualization/`，命名 `generate_<asset>.<ext>`
- 产物 SVG 归 `docs/`，**脚本内注释写明用途、依赖、运行方式、输出路径**
- 产物中的动态数值必须**运行时从真源读取**（如 `package.json` 的版本），不写死
