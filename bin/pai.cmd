@echo off
rem Launcher for pai on Windows, from cmd.exe or PowerShell; works from any directory.
rem PAI_PYTHON selects the interpreter: the full path of python.exe. Otherwise the
rem py launcher (py -3), then python.exe, then python3.exe, searched on PATH only,
rem never in the current directory. Python 3.11 or newer is required.
rem Arguments are passed through as cmd.exe received them (see INSTALL.md for quoting).
setlocal EnableExtensions DisableDelayedExpansion
set "NoDefaultCurrentDirectoryInExePath=1"
set "PAI_SCRIPT=%~dp0pai.py"
set "PAI_EXE="
set "PAI_EXE_ARGS="
if defined PAI_PYTHON goto explicit
for %%I in (py.exe) do set "PAI_EXE=%%~$PATH:I"
if defined PAI_EXE set "PAI_EXE_ARGS=-3"
if defined PAI_EXE goto run
for %%I in (python.exe) do set "PAI_EXE=%%~$PATH:I"
if defined PAI_EXE goto run
for %%I in (python3.exe) do set "PAI_EXE=%%~$PATH:I"
if defined PAI_EXE goto run
>&2 echo pai: Python 3.11 or newer is required, but py.exe, python.exe and python3.exe are not on PATH.
>&2 echo      Install it from python.org (a per-user install needs no administrator rights),
>&2 echo      or set PAI_PYTHON to the full path of python.exe.
exit /b 9009

:explicit
set "PAI_EXE=%PAI_PYTHON%"
if exist "%PAI_EXE%" goto run
>&2 echo pai: PAI_PYTHON was not found: "%PAI_EXE%"
>&2 echo      Set PAI_PYTHON to the full path of python.exe, version 3.11 or newer.
exit /b 9009

:run
"%PAI_EXE%" %PAI_EXE_ARGS% -B "%PAI_SCRIPT%" %*
set "PAI_RC=%ERRORLEVEL%"
if "%PAI_RC%"=="9009" >&2 echo pai: "%PAI_EXE%" did not start Python. If it is the Microsoft Store placeholder, install Python 3.11+ or set PAI_PYTHON.
exit /b %PAI_RC%
