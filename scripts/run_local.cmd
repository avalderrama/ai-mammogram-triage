@echo off
rem Starts the MCP server over HTTP on 127.0.0.1:8765 using the settings in .env (never committed).
cd /d "%~dp0.."
if "%MAMMO_PYTHON%"=="" set "MAMMO_PYTHON=%USERPROFILE%\.venvs\birads-poc\Scripts\python.exe"
"%MAMMO_PYTHON%" -m mammo_host http %*
