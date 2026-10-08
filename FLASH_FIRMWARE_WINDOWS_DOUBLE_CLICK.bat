@echo off
REM Friendly wrapper for the verified v6.0.4 prebuilt image.
REM The lower-level script performs erase, write and verification.
setlocal
title AtomS3R-CAM Endoscope v6.0.4 Firmware
echo.
echo AtomS3R-CAM Endoscope v6.0.4 firmware installer
echo ------------------------------------------------
echo Close Camera, serial monitors and Endoscope first.
echo Hold the AtomS3R reset button for about 2 seconds until green,
echo then release it and read its COM number in Device Manager.
echo.
set /p "PORT=Enter COM port, for example COM6: "
if "%PORT%"=="" (
  echo No COM port entered.
  pause
  exit /b 2
)

py -m esptool version >nul 2>&1
if errorlevel 1 (
  echo Installing the required esptool 4.8.1...
  py -m pip install "esptool==4.8.1"
  if errorlevel 1 (
    echo esptool installation failed. Check Python and the Internet connection.
    pause
    exit /b 1
  )
)

call "%~dp0flash_prebuilt_windows.bat" "%PORT%"
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (
  echo SUCCESS. Unplug the USB-C cable and reconnect it to the Raspberry Pi.
) else (
  echo FAILED. Nothing else will be changed. Review the message above.
)
pause
endlocal & exit /b %RESULT%
