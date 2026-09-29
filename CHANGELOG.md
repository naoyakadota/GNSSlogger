# Changelog

## 2.2.0 - 2026-09-26

- Made rather radical changes to the codebase. The aim of this update is to increase usability and simplifying system setup by adding further automations to the setup/config processes. 
- The pakcage now have setup.sh to help you set up the system with help of command-line interface(CLI). The code will be uploaded to GitHub so installation and future software updates can be done through `git clone https://github.com/naoyakadota/GNSSlogger`.
- Moved device, serial, storage, reset, and FTP settings into `/etc/gps-logger/config.ini`.
- Fixed the installed program path is now at `/opt/gps-logger/GPSlogger.py`.
- Changed the default observed GPS NMEA data location to the installing user's
  `/home/<user>/GPS_data/<device>/` directory.
- Added USB serial discovery using stable `/dev/serial/by-id/` paths first,
  with pySerial `ttyACM`/`ttyUSB` fallback and multi-device selection during
  installation.
- Added automatic baud probing based on a `$`-prefixed NMEA sentence while
  retaining fixed port and fixed baud configuration.
- Added optional `/etc/gps-logger/startup_commands.txt`: one text command per
  line, comments and blank lines ignored, CRLF terminator by default, sent on
  every startup.
- Added optional, non-fatal ZED-F9P RESET_N recovery on BCM GPIO17 after a
  configurable interval without a `$`-prefixed NMEA sentence. The default is
  300 seconds and a 200 ms Low pulse.
- Made the installer expose Raspberry Pi OS system packages inside the virtual
  environment so gpiozero can use the `python3-lgpio` backend, this "should" also make the code deployable to Pi5s.
- Added a per-device FTP path prompt and installation preflight that traverses
  or recursively creates path components, confirms an existing final folder,
  and proves upload, remote-size check, and delete permissions.
- Simplified the FTP prompt to request only the parent directory. The installer
  appends the validated device/site name and stores the resulting complete
  per-device path in `config.ini`; updates migrate earlier complete paths
  without appending the device name twice.
- Changed the default daily upload time to 00:10 and added hourly retries after
  failure, including an immediate attempt for pending logs at service startup.
- Made FTP errors non-fatal to both GNSS logging and the uploader scheduler.
- Added `.partial` upload scheme for ongoing upload, byte-size verification, final rename, and
  name conflict handling before moving local files into `Uploaded/`.
- Added [English](README.md) and [Japanese](README.ja.md) documentations; Release history will be in
  this separate changelog.md.

## 2.1.0

- Restored the FTP uploader. No Google Drive anymore!

## 2.0.0

- Simplified the program for data acquisition in an offline environment.

## 1.3.1

- Added `Pic_logger` and GNSS device reconnection support.

## 1.2.1

- Added multi-day upload support.

## 1.2.0

- Added concurrent logger and uploader operation.

## 1.1.0

- Initial GPS logger release.
