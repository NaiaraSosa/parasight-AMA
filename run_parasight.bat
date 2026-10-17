@echo off
setlocal
title Parasight - UNSAM

set "RHO_HOST=10.1.103.91"
set "LOCAL_PORT=8010"
set "REMOTE_HOST=127.0.0.1"
set "REMOTE_PORT=8010"

echo.
set /p "RHO_USER=Usuario de rho: "

echo.
echo Se va a abrir una ventana negra de SSH.
echo Si pregunta "Are you sure...", escribi yes.
echo Despues escribi tu password.
echo NO cierres esa ventana mientras uses Parasight.
echo.

start "Parasight SSH - NO CERRAR" cmd /k "ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -N -L %LOCAL_PORT%:%REMOTE_HOST%:%REMOTE_PORT% %RHO_USER%@%RHO_HOST%"

echo.
echo Esperando conexion...
timeout /t 8 /nobreak >nul

start "" "http://localhost:%LOCAL_PORT%/"
exit /b 0