@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  一键启动：后端(:8000) + 前端(:5173)，随后用"有头浏览器"打开
rem  用法:
rem     start_dev.bat                起前后端 + 打开浏览器(有头)
rem     start_dev.bat --demo         起前后端 + 跑有头 Playwright 对话演示
rem     PW_HOLD=1800000 start_dev.bat --demo   演示结束后保持浏览器 30 分钟(便于手动查看)
rem  可选环境变量: PYTHON=<python.exe>  (默认取 PATH 上的 python)
rem ============================================================

set PY=%PYTHON%
if "%PY%"=="" set PY=python
where %PY% >nul 2>&1
if errorlevel 1 ( echo [ERR] 未找到 python, 请安装或设置 PYTHON 环境变量 & exit /b 1 )

where npm >nul 2>&1
if errorlevel 1 ( echo [ERR] 未找到 npm, 请安装 Node.js >=18 & exit /b 1 )

echo ============================================================
echo  [1/3] 启动后端  uvicorn :8000
echo ============================================================
start "Agent-Backend" cmd /k "%PY% -m uvicorn main:app --host 127.0.0.1 --port 8000"

echo ============================================================
echo  [2/3] 启动前端  vite :5173
echo ============================================================
start "Agent-Frontend" cmd /k "cd frontend ^&^& npm run dev"

echo ============================================================
echo  [3/3] 等待服务就绪 ...
echo ============================================================
set TRY=0
:wait
set /a TRY+=1
curl -s -o nul http://127.0.0.1:8000/health
if not errorlevel 1 goto ready
curl -s -o nul http://127.0.0.1:5173/
if not errorlevel 1 goto ready
if %TRY% GEQ 90 ( echo [WARN] 90s 内未就绪, 服务可能仍在启动, 请查看上面两个窗口日志 & goto ready )
timeout /t 1 >nul
goto wait

:ready
echo ============================================================
echo  服务已就绪
echo ============================================================

if "%1"=="--demo" (
    echo [demo] 运行有头 Playwright 对话演示 (chat-llm)...
    cd frontend
    call npx playwright install chromium
    set BASE_URL=http://127.0.0.1:5173
    call npx playwright test --headed --config playwright.config.js tests/ui/chat-llm.spec.js
    cd ..
    goto end
)

echo [browser] 用有头浏览器打开 http://127.0.0.1:5173 ...
start "" http://127.0.0.1:5173

:end
echo ============================================================
echo  启动完成。关闭后端/前端窗口即可停止服务。
echo ============================================================
endlocal
