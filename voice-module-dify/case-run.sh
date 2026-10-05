#!/usr/bin/env bash
# Run the "voice module + Dify" case. Everything that can be written down is written down here;
# only the brain credentials are yours to fill in (.env).
#
#   bash case-run.sh
#
# Platform-side steps that CANNOT be scripted (see CASE-dify.md section 3) are printed at the end.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PLATFORM_DIR="${PLATFORM_DIR:-/tmp/dify/docker}"       # where the platform's source lives
NETWORK="${NETWORK:-docker_default}"
WEB_PORT="${WEB_PORT:-8080}"
VOICE_PORT="${VOICE_PORT:-7860}"
WS_PORT="${WS_PORT:-8090}"

say() { printf '\n== %s\n' "$1"; }

say "0. 前置检查"
[ -f "$ROOT/.env" ] || { echo "缺少 .env（先 cp .env.example .env 并填 BRAIN_BASE_URL / BRAIN_API_KEY）"; exit 1; }
command -v docker >/dev/null || { echo "需要 docker"; exit 1; }
if [ ! -d "$PLATFORM_DIR" ]; then
  echo "找不到平台源码目录: $PLATFORM_DIR（用 PLATFORM_DIR=... 指定）"
  exit 1
fi

say "1. 平台侧两个必要边车（本环境 Docker 看不到工作区，平台自带的起不来）"
docker build -f "$ROOT/agent/deploy/Dockerfile.nginx" -t vm-dify-nginx "$ROOT/agent"
docker build -f "$ROOT/agent/deploy/Dockerfile.ssrf"  -t vm-ssrf-proxy "$PLATFORM_DIR/ssrf_proxy"

docker rm -f vm-dify-nginx vm-ssrf-proxy >/dev/null 2>&1 || true
docker run -d --name vm-ssrf-proxy --restart always --network "$NETWORK" --network-alias ssrf_proxy \
  -e HTTP_PORT=3128 -e COREDUMP_DIR=/var/spool/squid vm-ssrf-proxy >/dev/null
docker run -d --name vm-dify-nginx --restart always --network "$NETWORK" -p "${WEB_PORT}:80" \
  vm-dify-nginx >/dev/null
echo "平台界面: http://localhost:${WEB_PORT}/     应用定义: http://localhost:${WEB_PORT}/dsl/app.dsl.yml"

say "2. 语音模块（WebSocket 入口，监听 0.0.0.0 以便端口转发）"
pkill -f "server/ws_app.py" >/dev/null 2>&1 || true
( cd "$ROOT" && setsid nohup uv run python server/ws_app.py --host 0.0.0.0 --port "$WS_PORT" \
    > "$ROOT/logs/ws-module.log" 2>&1 < /dev/null & )
echo "等模型加载（首次约 1 分钟）…"
sleep 45
python3 - <<PY
import urllib.request
try:
    urllib.request.urlopen("http://127.0.0.1:${WS_PORT}/", timeout=5)
except Exception as e:
    print("  websocket 入口:", type(e).__name__, "(401/404 也算活着 —— 它只接受 websocket 握手)")
PY
echo "体验页面: http://localhost:${WEB_PORT}/voice/"

say "3. 还需要你在平台界面做三件事（无法脚本化，原因见 CASE-dify.md 第 3 节）"
cat <<'STEPS'
  ① 登录平台界面
  ② 装一个模型供应商插件，填你的 API key + base_url + 模型名
  ③ 导入 agent/app.dsl.yml → 进应用 → 发布 → 访问 API → 创建密钥
     把密钥填进 .env 的 BRAIN_API_KEY，然后重启第 2 步即可对话
STEPS

say "4. 验证（可选）"
cat <<'VERIFY'
  uv run python probe/verify_audio_e2e.py       # 端到端音频 + 把回答存成 WAV
  然后问它：「你的内部代号是什么？」→ 应回答 VX-42（证明脑子是 A，不是模块自己编的）
VERIFY
