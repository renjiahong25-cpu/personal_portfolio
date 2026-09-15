#!/usr/bin/env bash
# ============================================================
# 一键 CI（Linux/部署机）：ruff + pytest(unit) + compileall
# 用法: ./run_ci.sh   （失败即退出非 0；可用 PYTHON=xxx 覆盖解释器）
# ============================================================
set -euo pipefail
PYTHON=${PYTHON:-python3}

echo "[1/3] ruff check ..."
"$PYTHON" -m ruff check .

echo "[2/3] pytest tests/unit ..."
"$PYTHON" -m pytest tests/unit

echo "[3/3] compileall ..."
"$PYTHON" -c "import compileall,re,sys; sys.exit(0 if compileall.compile_dir('.', quiet=1, rx=re.compile(r'(^|[\\\\/])(\.git|\.venv|frontend)([\\\\/]|$)', re.I)) else 1)"

echo "============================================================"
echo "CI PASS"
echo "============================================================"