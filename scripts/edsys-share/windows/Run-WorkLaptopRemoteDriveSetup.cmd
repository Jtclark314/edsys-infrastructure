@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-WorkLaptopRemoteDrives.ps1"
set "EDSYS_RESULT=%ERRORLEVEL%"
echo.
if "%EDSYS_RESULT%"=="0" (
  echo Work laptop remote-drive setup completed successfully.
) else (
  echo Work laptop remote-drive setup failed with exit %EDSYS_RESULT%.
)
echo Press any key to close this window.
pause >nul
exit /b %EDSYS_RESULT%
