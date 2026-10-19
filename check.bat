@echo off
cd /d "%~dp0"
title Ricochet - Tripwire engine
set PY=python
where py >nul 2>nul
if %errorlevel%==0 set PY=py -3
%PY% -c "import truststore" >nul 2>nul
if errorlevel 1 (
  echo Installing truststore, one time only...
  %PY% -m pip install --quiet --disable-pip-version-check truststore
)
%PY% -m tracker.check
pause
