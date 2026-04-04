@echo off
cd /d %~dp0

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 package_app.py %*
) else (
  python package_app.py %*
)
