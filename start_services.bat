@echo off
chcp 65001 >nul
echo ==========================================
echo   Code Review Agent - Service Launcher
echo ==========================================
echo.

set "PYTHON=C:\tools\anaconda3\envs\pytorch\python.exe"
cd /d X:\VScode\code-review-agent
set "LOGS_DIR=%CD%\logs"
if not exist "%LOGS_DIR%" mkdir "%LOGS_DIR%"

rem Proxy settings for the model API; preserve inherited values.
if not defined HTTP_PROXY set "HTTP_PROXY=http://127.0.0.1:7897"
if not defined HTTPS_PROXY set "HTTPS_PROXY=http://127.0.0.1:7897"
if not defined ALL_PROXY set "ALL_PROXY=http://127.0.0.1:7897"

if not exist "%PYTHON%" set "PYTHON=python"

echo [1/2] Starting FastAPI (port 8000)...
start "FastAPI" /D "%CD%" /B "%PYTHON%" -m uvicorn src.api.server:app --host 0.0.0.0 --port 8000 > "%LOGS_DIR%\fastapi-service.out.log" 2> "%LOGS_DIR%\fastapi-service.err.log"

echo [2/2] Starting Streamlit (port 8501)...
start "Streamlit" /D "%CD%" /B "%PYTHON%" -m streamlit run src/app/streamlit_app.py --server.port 8501 --server.address 0.0.0.0 --server.headless true > "%LOGS_DIR%\streamlit-service.out.log" 2> "%LOGS_DIR%\streamlit-service.err.log"

echo.
echo ==========================================
echo   Services Started!
echo   FastAPI:    http://localhost:8000/docs
echo   Streamlit:  http://localhost:8501
echo ==========================================
echo.
echo   Logs:       %LOGS_DIR%
echo ==========================================
echo.
echo Services are running independently; this launcher can now exit.
