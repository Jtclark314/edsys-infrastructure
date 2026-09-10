@echo off
setlocal
REM Run this from the ordinary Windows desktop account, not as administrator.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-WorkLaptopCodex.ps1" %*
set "EDS_UPDATE_EXIT=%ERRORLEVEL%"
echo.
echo Updater exit code: %EDS_UPDATE_EXIT% (0=completed, 2=partial failure, 1=stopped)
pause
exit /b %EDS_UPDATE_EXIT%
