@echo off
:: Auto-elevate to Admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting admin privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo ==========================================
echo   Docker Setup - Windows 11
echo ==========================================
echo.

echo [1/4] Enabling Virtual Machine Platform...
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart

echo.
echo [2/4] Enabling Windows Subsystem for Linux...
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart

echo.
echo [3/5] Enabling Hypervisor...
bcdedit /set hypervisorlaunchtype auto

echo.
echo [4/5] Setting WSL2 as default...
wsl --set-default-version 2

echo.
echo [5/5] Updating WSL kernel...
wsl --update

echo.
echo ==========================================
echo   Setup complete!
echo   PLEASE RESTART your computer now.
echo   After restart, Docker Desktop will work.
echo ==========================================
echo.
pause
