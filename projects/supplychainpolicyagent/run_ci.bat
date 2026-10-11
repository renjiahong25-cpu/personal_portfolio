@echo off
rem ============================================================
rem 一键 CI：ruff + pytest(unit) + compileall
rem 用法: run_ci.bat   （失败即退出非 0）
rem ============================================================
setlocal
set PYTHON=D:\conda_envs\cross-border-agent\python.exe
if not exist "%PYTHON%" set PYTHON=python

echo [1/3] ruff check ...
"%PYTHON%" -m ruff check .
if errorlevel 1 (
  echo [FAIL] ruff 未通过
  exit /b 1
)

echo [2/3] pytest tests/unit ...
"%PYTHON%" -m pytest tests/unit
if errorlevel 1 (
  echo [FAIL] 单测未通过
  exit /b 1
)

echo [3/3] compileall ...
"%PYTHON%" -c "import compileall,re,sys; sys.exit(0 if compileall.compile_dir('.', quiet=1, rx=re.compile(r'(^|[\\/])(\.git|\.venv|frontend)([\\/]|$)', re.I)) else 1)"
if errorlevel 1 (
  echo [FAIL] 编译检查未通过
  exit /b 1
)

echo ============================================================
echo CI PASS
echo ============================================================
exit /b 0