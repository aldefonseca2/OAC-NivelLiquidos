@echo off
REM ==================================================================
REM  OAC - Nivel de Liquidos
REM  Crea un Python 3.12 portable en la carpeta  python\  del repo,
REM  con las librerias que necesita el bridge (asyncua, pyzmq, cbor2).
REM
REM  Uso: doble clic, o desde la terminal:  setup\crear_python_portable.bat
REM  Requiere conexion a internet solo durante la creacion.
REM ==================================================================
setlocal

set PYVER=3.12.10
for %%I in ("%~dp0..") do set ROOT=%%~fI
set PYDIR=%ROOT%\python
set ZIP=%TEMP%\python-%PYVER%-embed-amd64.zip

if exist "%PYDIR%\python.exe" (
    echo Ya existe un Python portable en "%PYDIR%". No se hace nada.
    goto :mostrar_config
)

echo [1/5] Descargando Python %PYVER% embeddable...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip' -OutFile '%ZIP%'"
if errorlevel 1 goto :error

echo [2/5] Descomprimiendo en "%PYDIR%"...
powershell -NoProfile -Command "Expand-Archive -Path '%ZIP%' -DestinationPath '%PYDIR%' -Force"
if errorlevel 1 goto :error

echo [3/5] Habilitando librerias externas (import site)...
powershell -NoProfile -Command "(Get-Content '%PYDIR%\python312._pth') -replace '^#\s*import site','import site' | Set-Content '%PYDIR%\python312._pth'"
if errorlevel 1 goto :error

echo [4/5] Instalando pip...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%PYDIR%\get-pip.py'"
if errorlevel 1 goto :error
"%PYDIR%\python.exe" "%PYDIR%\get-pip.py" --no-warn-script-location
if errorlevel 1 goto :error
del "%PYDIR%\get-pip.py"

echo [5/5] Instalando asyncua, pyzmq y cbor2...
"%PYDIR%\python.exe" -m pip install -r "%ROOT%\requirements.txt" --no-warn-script-location
if errorlevel 1 goto :error

del "%ZIP%" 2>nul
echo.
echo Python portable listo.

:mostrar_config
set PYEXE=%PYDIR%\python.exe
set PYEXE=%PYEXE:\=/%
echo.
echo ------------------------------------------------------------------
echo  Ultimo paso: indicarle a CoppeliaSim que use este Python.
echo  Abra usrset.txt (normalmente en %%APPDATA%%\CoppeliaSim\) y deje
echo  la linea defaultPython asi:
echo.
echo     defaultPython = %PYEXE%
echo.
echo  Luego reinicie CoppeliaSim.
echo ------------------------------------------------------------------
pause
exit /b 0

:error
echo.
echo *** Ocurrio un error. Revise el mensaje de arriba y su conexion a internet. ***
pause
exit /b 1
