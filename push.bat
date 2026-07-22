@echo off
cd /d X:\VScode\code-review-agent
git add .
set /p msg="Commit message: "
git commit -m "%msg%"
git push
echo.
echo Done! Check: https://github.com/yemuxue/code-review-agent/actions
pause
