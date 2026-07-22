@echo off
cd /d X:\VScode\code-review-agent
echo Running tests...
C:\tools\anaconda3\envs\pytorch\python.exe -m pytest tests/ -q
if %errorlevel% neq 0 (
    echo TESTS FAILED! Push aborted.
    pause
    exit /b 1
)
echo Tests passed!
git add .
set /p msg="Commit message: "
git commit -m "%msg%"
git push
echo.
echo Done! Check: https://github.com/yemuxue/code-review-agent/actions
pause
