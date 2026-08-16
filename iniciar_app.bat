@echo off
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY (
    echo No se encontro Python.
    echo Instale Python 3.11 o superior y marque "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo No se pudo crear el entorno virtual.
        pause
        exit /b 1
    )
    echo Instalando dependencias...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Fallo la instalacion de dependencias.
        pause
        exit /b 1
    )
)

echo Abriendo BITACORA...
".venv\Scripts\python.exe" -m app.main
if errorlevel 1 pause
