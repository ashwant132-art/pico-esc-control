@echo off
REM Builds one Windows exe from esc_control.py:
REM   dist\esc_control_gui.exe  - the ESC Control app
REM Needs Python 3 for Windows. Put this file next to esc_control.py.

echo Closing any running copy of the old exe...
taskkill /f /im esc_control_gui.exe >nul 2>&1
taskkill /f /im esc_control_cli.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo Making sure pip is installed...
py -m ensurepip --upgrade
py -m pip --version || goto :nopip

py -m pip install --upgrade pyinstaller pyserial || goto :err

py -m PyInstaller --onefile --noconsole --name esc_control_gui esc_control.py || goto :err

echo.
echo Done. esc_control_gui.exe is in the "dist" folder.
pause
exit /b 0

:nopip
echo.
echo pip is still missing. Re-run the Python installer, choose Modify,
echo tick "pip" under Optional Features, finish, then run this file again.
pause
exit /b 1

:err
echo.
echo Build failed - see the messages above.
pause
exit /b 1
