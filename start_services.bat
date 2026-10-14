@echo off
chcp 65001 >nul
echo ==========================================
echo   Code Review Agent - Service Launcher
echo ==========================================
echo.

rem 从脚本所在目录运行。原先硬编码了某台机器的绝对路径，
rem 换台机器就失效；%~dp0 始终是本脚本所在目录（含结尾反斜杠）。
cd /d "%~dp0"

rem 解释器选择：PYTHON 环境变量 > 项目内 .venv > PATH 中的 python。
rem 原先硬编码了某个 conda 环境的绝对路径，它只在一台机器上存在，
rem 对其它环境等于必然回退，不如直接按顺序探测。
if not defined PYTHON (
    if exist "%~dp0.venv\Scripts\python.exe" (
        set "PYTHON=%~dp0.venv\Scripts\python.exe"
    ) else (
        set "PYTHON=python"
    )
)

set "LOGS_DIR=%CD%\logs"
if not exist "%LOGS_DIR%" mkdir "%LOGS_DIR%"

rem 代理按需启用：原先默认把 HTTP(S)_PROXY 指向开发机上的本地代理，
rem 其它环境没有该代理时会直接把所有模型 API 请求打挂。
rem 现在只继承调用者已设置的变量；需要代理请自行 set HTTPS_PROXY=...

echo   解释器: %PYTHON%
echo   工作目录: %CD%
echo.

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
