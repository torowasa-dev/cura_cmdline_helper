@echo off
setlocal

::python "%~dp0cura_reslice.py" --list-printers
python "%~dp0cura_reslice.py" --help

:start

if "%~1"=="" (
    exit /b 0
)
python "%~dp0cura_reslice.py" "%~1" --recursive --overwrite --printer "Anycubic i3 Mega S/Pro ORG" --insert-before-end "%~dp0UNLOAD50.gcode"

shift
goto start
