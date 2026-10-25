@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

title Bili Audio Studio

set "TMPDIR=%~dp0workspace\.tmp"
if not exist "%TMPDIR%" mkdir "%TMPDIR%"
set "TEMP=%TMPDIR%"
set "TMP=%TMPDIR%"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [错误] 未检测到 Python。
  echo   请安装 Python 3.10 或更高版本，安装时勾选 "Add Python to PATH"。
  echo.
  pause
  exit /b 1
)

if not exist "vendor\ffmpeg\ffmpeg.exe" (
  echo.
  echo   首次运行：准备 ffmpeg（约 87MB，只需下载一次）...
  echo.
  python scripts\fetch_ffmpeg.py
  if errorlevel 1 (
    echo.
    echo   [提示] ffmpeg 未就绪：可以解析和下载音频，但无法合并。
    echo   稍后可重新运行 run.bat 自动重试。
    echo.
  )
)

python app.py %*
set "EXITCODE=%errorlevel%"
if not "%EXITCODE%"=="0" (
  echo.
  echo   程序退出，代码 %EXITCODE%
  pause
)
exit /b %EXITCODE%
