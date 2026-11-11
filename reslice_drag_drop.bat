@echo off
setlocal
if "%~1"=="" (
    echo Drag 3MF files or a folder onto this BAT file.
    pause
    exit /b 2
)
where py >nul 2>nul
if errorlevel 1 (
    python "%~dp0cura_reslice.py" %*
) else (
    py -3 "%~dp0cura_reslice.py" %*
)
set "reslice_exit_code=%errorlevel%"
echo.
pause
exit /b %reslice_exit_code%
