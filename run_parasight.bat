@echo off
setlocal
title Parasight - Segmentacion

cd /d "%~dp0"

set "ENV_NAME=parasight"
set "APP_URL=http://127.0.0.1:8000"

echo.
echo Iniciando Parasight...
echo Carpeta del programa: %CD%
echo.

where conda >nul 2>nul
if %ERRORLEVEL%==0 (
    call conda activate %ENV_NAME%
    goto run_app
)

set "ACTIVATE_BAT="
if exist "%USERPROFILE%\miniforge3\Scripts\activate.bat" set "ACTIVATE_BAT=%USERPROFILE%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE_BAT if exist "%USERPROFILE%\Miniforge3\Scripts\activate.bat" set "ACTIVATE_BAT=%USERPROFILE%\Miniforge3\Scripts\activate.bat"
if not defined ACTIVATE_BAT if exist "%LOCALAPPDATA%\miniforge3\Scripts\activate.bat" set "ACTIVATE_BAT=%LOCALAPPDATA%\miniforge3\Scripts\activate.bat"
if not defined ACTIVATE_BAT if exist "C:\ProgramData\miniforge3\Scripts\activate.bat" set "ACTIVATE_BAT=C:\ProgramData\miniforge3\Scripts\activate.bat"

if not defined ACTIVATE_BAT (
    echo No encuentro Miniforge/Conda.
    echo Abri "Miniforge Prompt" y ejecuta manualmente:
    echo.
    echo   conda activate %ENV_NAME%
    echo   parasight web --host 127.0.0.1 --port 8000
    echo.
    pause
    exit /b 1
)

call "%ACTIVATE_BAT%" %ENV_NAME%

:run_app
where parasight >nul 2>nul
if ERRORLEVEL 1 (
    echo No encuentro el comando "parasight".
    echo Probablemente falta instalar el programa en el entorno.
    echo.
    echo Ejecuta una vez, desde esta carpeta:
    echo.
    echo   pip install -e .
    echo.
    pause
    exit /b 1
)

echo Abriendo navegador en %APP_URL%
start "" "%APP_URL%"
echo.
echo Deja esta ventana abierta mientras uses el programa.
echo Para cerrar Parasight, cerra esta ventana o presiona Ctrl+C.
echo.

parasight web --host 127.0.0.1 --port 8000

pause
