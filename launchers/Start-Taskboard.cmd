@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
:check_python
py -3 -I -X utf8 -c "import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 goto run_py
python -I -X utf8 -c "import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 goto run_python
echo.
echo Taskboard needs Python 3.11 or newer.
echo 1 Open the official Python installer page
echo 2 Check again after installation
echo 0 Cancel
choice /c 120 /n /m "Choose 1, 2 or 0: "
if errorlevel 3 exit /b 1
if errorlevel 2 goto check_python
start "" "https://www.python.org/downloads/windows/"
echo Install Python for your user account, then choose 2. A new window may be needed after installation.
goto check_python
:run_py
py -3 -I -B -X utf8 "%~dp0bootstrap.py" "%~dp0request.json"
goto finished
:run_python
python -I -B -X utf8 "%~dp0bootstrap.py" "%~dp0request.json"
:finished
set "TASKBOARD_EXIT=%errorlevel%"
echo.
echo Press any key to close. Reopen this file to inspect or continue the saved task.
pause >nul
exit /b %TASKBOARD_EXIT%
