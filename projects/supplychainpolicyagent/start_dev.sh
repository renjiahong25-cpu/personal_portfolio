#!/usr/bin/env bash
# ============================================================
#  一键启动：后端(:8000) + 前端(:5173)，随后用"有头浏览器"打开
#  用法:
#     ./start_dev.sh                起前后端 + 打开浏览器(有头)
#     ./start_dev.sh --demo         起前后端 + 跑有头 Playwright 对话演示
#     PW_HOLD=1800000 ./start_dev.sh --demo   演示结束后保持 30 分钟
#  可选环境变量: PYTHON=<python>  (默认 python3)
# ============================================================
set -uo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "[ERR] 未找到 python，请安装或设置 PYTHON"; exit 1; }
command -v npm   >/dev/null 2>&1 || { echo "[ERR] 未找到 npm，请安装 Node.js >=18"; exit 1; }

echo "============================================================"
echo " [1/3] 启动后端  uvicorn :8000"
echo "============================================================"
"$PY" -m uvicorn main:app --host 127.0.0.1 --port 8000 &
BACK_PID=$!

echo "============================================================"
echo " [2/3] 启动前端  vite :5173"
echo "============================================================"
(cd frontend && npm run dev) &
FRONT_PID=$!

cleanup() { echo; echo "[stop] 关闭前后端 ..."; kill "$BACK_PID" "$FRONT_PID" 2>/dev/null; }
trap cleanup EXIT

echo "============================================================"
echo " [3/3] 等待服务就绪 ..."
echo "============================================================"
for i in $(seq 1 90); do
  if curl -s -o /dev/null http://127.0.0.1:8000/health; then break; fi
  if curl -s -o /dev/null http://127.0.0.1:5173/; then break; fi
  sleep 1
done

echo "============================================================"
echo " 服务已就绪"
echo "============================================================"

if [ "${1:-}" = "--demo" ]; then
  echo "[demo] 运行有头 Playwright 对话演示 (chat-llm)..."
  cd frontend
  npx playwright install chromium
  BASE_URL=http://127.0.0.1:5173 npx playwright test --headed --config playwright.config.js tests/ui/chat-llm.spec.js
  cd ..
  exit 0
fi

case "$(uname)" in
  Darwin) open http://127.0.0.1:5173 ;;
  Linux)  xdg-open http://127.0.0.1:5173 2>/dev/null || echo "[browser] 请手动打开 http://127.0.0.1:5173" ;;
  *)      echo "[browser] 请打开 http://127.0.0.1:5173" ;;
esac

echo "============================================================"
echo " 启动完成。Ctrl+C 停止前后端。"
echo "============================================================"
wait
