@echo off
rem Builds a mod release archive into dist\ and adds the version to mods.json.
rem Usage: build.bat [mod id]. The id can be left out while mods.json has a single mod.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python 3 was not found. Install it from https://www.python.org/downloads/
    goto end
)

set "XDELTA="
if exist "tools\xdelta3.exe" set "XDELTA=tools\xdelta3.exe"
if not defined XDELTA where xdelta3 >nul 2>nul && set "XDELTA=xdelta3"
if not defined XDELTA (
    echo xdelta3 was not found. Download it from https://github.com/jmacd/xdelta-gpl/releases
    echo and save it as tools\xdelta3.exe.
    goto end
)

set "GAME="
if exist game_dir.txt set /p GAME=<game_dir.txt
if defined GAME goto version
:ask_game
set /p "GAME=Extracted game folder, with default.xbe and GAMEDATA.WAD: "
if not defined GAME goto ask_game
set "GAME=%GAME:"=%"
>game_dir.txt echo %GAME%

:version
set "VERSION="
set /p "VERSION=Version, for example 1.0.0: "
if not defined VERSION goto version

%PY% tools\build_mod.py %* --version "%VERSION%" --game "%GAME%" --xdelta "%XDELTA%"
if errorlevel 1 (
    echo.
    echo The build failed. To use another game folder, delete game_dir.txt.
)

:end
pause
