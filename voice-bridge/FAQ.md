# FAQ

> 本文件记录 `voice-bridge`（Open WebUI 二次开发分支）在实际运行中**踩过的坑与解决办法**。
> 上游通用问题请优先查 [Open WebUI 官方文档](https://docs.openwebui.com/)。

## 启动 / 运行

### Q1: 启动报 `ModuleNotFoundError: No module named 'chromadb'`

**原因**：用了「瘦身依赖」（`requirements-slim.txt`），但**没开瘦身开关**。
`backend/open_webui/config.py` 里 `import chromadb` 是**有条件**的：

```python
if VECTOR_DB == 'chroma' and not USE_SLIM:
    import chromadb
```

**解决**：启动时设 **`USE_SLIM_DOCKER=true`**。
（⚠️ 变量名是 `USE_SLIM_DOCKER`，不是 `USE_SLIM`。）开启后 `VECTOR_DB` 默认变为 `pgvector`，跳过 chromadb。

### Q2: 启动报 `FileNotFoundError: .../open_webui/CHANGELOG.md`

**原因**：`backend/open_webui/env.py` 启动时**会读取根目录 `CHANGELOG.md`**（读不到才回退到包内）。
**这个文件不能删。**

**解决**：把 `CHANGELOG.md` 恢复/保留在项目根目录。

### Q3: 启动报 `[Errno 98] address already in use`

**原因**：**8080 / 3000 常被其它服务占用**（本环境的 code-server / codebuddy 就占了）。

**解决**：换端口。先测空闲：

```bash
python3 -c "import socket;s=socket.socket();s.bind(('0.0.0.0',8000));print('8000 free');s.close()"
```

再启动时指定 `PORT=8000`。

### Q4: 前端 `npm run build` 报 `JavaScript heap out of memory`

**原因**：Node 默认堆上限约 4GB，构建 Svelte 前端不够。

**解决**：

```bash
NODE_OPTIONS="--max-old-space-size=10240" npm run build
```

### Q5: 这台机器只有 CPU，能跑得起来吗？

**能。** Open WebUI 是「界面 + 编排」服务，**不跑模型**（模型走外部 API），所以**不依赖 GPU**。
实测 8 核 / 16GB 纯 CPU 正常启动。用瘦身依赖 + `USE_SLIM_DOCKER=true` 可进一步减负。

### Q6: 服务在容器/云环境里，怎么从浏览器访问？

把服务监听的端口（如 8000）**通过平台的端口暴露 / IDE 的 Ports 面板转发**，再访问 `http://<访问地址>:8000`。
首次访问会引导你注册**管理员账号**（第一个注册的用户即管理员）。

## 合法性与协议

### Q7: 我可以把 `LICENSE` 换成自己的吗？

**不可以。** 本分支是 Open WebUI 的**衍生作品**，**必须保留上游 Open WebUI License 及版权声明**。
你可以为自己的**新增代码**另行声明许可，但**不能替换或移除上游协议**。

### Q8: 我可以去掉界面上的 "Open WebUI" 字样吗？

Open WebUI License 第 4 条禁止移除/隐藏品牌，**除非**满足其一：
① 30 天内终端用户 ≤ 50 人；② 获得版权方书面许可；③ 持有企业许可。
**保险做法：保留品牌。**

### Q9: 为什么 `package.json` 里的 `name` 还是 `open-webui`？

那是**上游的构建标识**，改了会破坏与上游的对应关系，也可能触碰品牌条款。
本分支的**身份**体现在**仓库/目录名（`voice-bridge`）与 `voice-docs/`**，而不是改上游元数据。

## 配置与模型

### Q10: 在 `.env` 里配了 LLM，为什么界面里没生效 / 还是旧模型？

**原因**：Open WebUI 的 `PersistentConfig` 机制 —— **首次启动会把配置写进数据库**，之后**数据库值优先于环境变量**。
所以改了 `.env` 后，如果 DB 里已有旧值（如默认的 `api.openai.com` + 空 key），`.env` 就被盖住了。

**解决**：停服务 → 删掉 DB 里的旧键 → 重启（会从环境变量重新初始化）：

```bash
cd backend
pkill -f "uvicorn open_webui"
.venv/bin/python -c "
import sqlite3
c=sqlite3.connect('data/webui.db');cur=c.cursor()
cur.execute(\"DELETE FROM config WHERE key LIKE 'openai.api%'\")
c.commit();print('deleted',cur.rowcount)"
PORT=8000 PATH="$PWD/.venv/bin:$PATH" ./start.sh
```

（也可以直接在 UI 的 **Settings → Connections** 里改，效果相同。）

### Q11: 本分支用哪个 LLM？

复用 `pipecat-quickstart/server/.env` 里的凭证，实测结果：

| 服务商 | 状态 |
|---|---|
| **商汤 SenseNova** | ✅ 可用（9 个模型，均支持工具调用） |
| 共绩算力 | ❌ 余额不足（HTTP 402） |
| 魔搭 ModelScope | ⚠️ key 为空，必然报错 |

当前 `voice-bridge/.env` 已接入商汤（`OPENAI_API_BASE_URL` / `OPENAI_API_KEY`）。

## 访问（CNB 云环境）

### Q12: 在 CNB 云环境下，怎么从浏览器打开这个服务？

**不是 `http://localhost:8000`**，而是走 **CNB 的公网代理**（服务已在 `0.0.0.0:8000` 监听，符合要求）。

```bash
# 取代理地址模板（形如 https://xxxxxx-{{port}}.cnb.run）
echo "$CNB_VSCODE_PROXY_URI"
# 本环境实际值：https://6p1cwlsz8a-{{port}}.cnb.run
```

把 `{{port}}` 替换成实际端口 → **本环境访问地址：<https://6p1cwlsz8a-8000.cnb.run>**

要点：

- 服务**必须监听 `0.0.0.0`**（`start.sh` 默认如此）；监听 `localhost`/`127.0.0.1` 则无法通过代理访问
- 也可在 WebIDE 的 **PORTS** 面板添加 `8000` 端口映射，从面板里点开的 URL 访问
- 该域名前缀由**环境实例**决定，重建环境后可能变化 —— 以 `$CNB_VSCODE_PROXY_URI` 为准
- 官方文档：<https://docs.cnb.cool/zh/workspaces/business-preview.md>
