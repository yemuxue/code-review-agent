@echo off
echo ==========================================
echo   Code Review Agent - Service Launcher
echo ==========================================
echo.

set PYTHON=C:\tools\anaconda3\envs\pytorch\python.exe
cd /d X:\VScode\code-review-agent

echo [1/2] Starting FastAPI (port 8000)...
start "FastAPI" %PYTHON% -m uvicorn src.api.server:app --host 0.0.0.0 --port 8000

echo [2/2] Starting Streamlit (port 8501)...
start "Streamlit" %PYTHON% -m streamlit run src/app/streamlit_app.py --server.port 8501 --server.address 0.0.0.0

echo.
echo ==========================================
echo   Services Started!
echo   FastAPI:    http://localhost:8000/docs
echo   Streamlit:  http://localhost:8501
echo ==========================================
echo.
echo Close this window to keep services running.
pause
