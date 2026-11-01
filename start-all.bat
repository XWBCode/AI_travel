@echo off
chcp 65001 >nul
REM ============================================================
REM AI 旅行助手 - Windows 一键启动脚本
REM 用法：双击运行，或命令行执行 start-all.bat
REM 前提：已安装 JDK17+ / Maven / Python3.10+ / Node18+ / MySQL / Redis
REM ============================================================

setlocal enabledelayedexpansion

echo ============================================
echo   AI Travel Assistant - Starting services
echo ============================================

REM ---------- 读取 .env（如果存在） ----------
if exist "%~dp0.env" (
  echo [env] loading .env
  for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%~dp0.env") do (
    set "%%a=%%b"
  )
)

REM ---------- 1. Python AI 服务 ----------
echo [1/3] Starting AI service (Python FastAPI :8000) ...
cd /d "%~dp0backend-ai"
if not exist ".venv" (
  python -m venv .venv
  call .venv\Scripts\activate.bat
  pip install -r requirements.txt -q
) else (
  call .venv\Scripts\activate.bat
)
start "ai-service" cmd /c "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 && pause"

REM ---------- 2. Java 服务 ----------
echo [2/3] Starting Java backend (:8080) ...
cd /d "%~dp0backend-java"
start "java-backend" cmd /c "mvn spring-boot:run -q && pause"

REM ---------- 3. 前端 ----------
echo [3/3] Starting frontend (Vite dev :5173) ...
cd /d "%~dp0frontend"
if not exist "node_modules" (
  call pnpm install
)
start "frontend" cmd /c "pnpm dev && pause"

echo.
echo ============================================
echo   All services started:
echo   - Frontend  : http://localhost:5173
echo   - Java API  : http://localhost:8080/api/health
echo   - AI service: http://localhost:8000/v1/health
echo   Close each window to stop the corresponding service.
echo ============================================
pause
