@echo off
REM Flash the merged ESP-IDF image. No Arduino IDE build is needed.
REM Hardware validation status is in TEST_REPORT.md.
setlocal

if "%~1"=="" (
  echo Usage: flash_prebuilt_windows.bat COM6
  echo.
  echo Find the COM number under "Ports (COM ^& LPT)" in Device Manager.
  echo Hold the reset button for ~2 seconds until the green LED lights up
  echo BEFORE running this, or esptool cannot reach the bootloader.
  exit /b 2
)

set "IMAGE=%~dp0release\atoms3r_cam_uvc_imu_v6_0_4.bin"
if not exist "%IMAGE%" (
  echo Firmware image not found: %IMAGE%
  exit /b 2
)

py -m esptool version >nul 2>&1
if errorlevel 1 (
  echo esptool is not installed. Run:  py -m pip install "esptool==4.8.1"
  exit /b 2
)

REM Erase stale factory settings first: an old NVS or partition left behind is
REM read back by the new firmware as if it were its own configuration.
echo === Erasing flash on %1 ===
py -m esptool --chip esp32s3 --port %1 erase_flash
if errorlevel 1 goto :nolink

REM The merged image already carries these in its header, but esptool has
REM changed its own defaults between major versions. Writing them explicitly
REM keeps the write settings aligned with this release build.
REM Values match firmware/merge_firmware.sh and firmware/sdkconfig:
REM   dio  - release bootloader flash mode
REM   80m  - CONFIG_ESPTOOLPY_FLASHFREQ
REM   8MB  - ESP32-S3-PICO-1-N8R8
set "FLAGS=--flash_mode dio --flash_freq 80m --flash_size 8MB"

echo === Writing %IMAGE% at 0x0 ===
for %%B in (1500000 921600 460800 115200) do (
  echo --- trying %%B baud ---
  py -m esptool --chip esp32s3 --port %1 --baud %%B write_flash -z %FLAGS% 0x0 "%IMAGE%"
  if not errorlevel 1 goto :done
  echo %%B baud failed, dropping down...
)
goto :nolink

:done
echo.
echo Flash complete. Unplug and reconnect the USB-C cable.
echo On Windows, the Camera app should now see the AtomS3R.
echo NOTE: v6 replaces the download port with UVC+CDC. To reflash, you MUST
echo       hold reset for ~2 seconds again first.
endlocal
exit /b 0

:nolink
echo.
echo Could not talk to the board.
echo   1. Hold the reset button ~2 seconds until the green LED lights, release.
echo   2. Re-check the COM number in Device Manager.
echo   3. Try a different USB-C cable - some are charge-only.
endlocal
exit /b 1
