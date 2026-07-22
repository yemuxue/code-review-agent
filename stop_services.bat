@echo off
echo Stopping services...
taskkill /FI "WINDOWTITLE eq FastAPI*" /F 2>nul
taskkill /FI "WINDOWTITLE eq Streamlit*" /F 2>nul
echo Done.
pause
