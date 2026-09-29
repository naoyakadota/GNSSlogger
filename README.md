# GPS Logger 2.2.0

English | [日本語](README.ja.md)

A small Raspberry Pi service that records `$`-prefixed NMEA sentences from a
USB GNSS receiver into daily text files and uploads completed files to an FTP
server.

Original author and maintainer: **Naoya Kadota** — https://nkadota.com

## 1. Main features

- one daily log file per device/site;
- automatic USB serial-port and baud-rate detection;
- optional text startup commands for receivers that do not output NMEA until
  commanded;
- automatic serial reconnection;
- optional best-effort GNSS reset through BCM GPIO17;
- daily FTP upload with hourly retry after failure;
- FTP path validation during installation;
- automatic startup and restart through systemd;
- device settings and FTP credentials stored outside the Git repository.

The logger considers a line valid for logging when its first character is `$`.
It does not perform NMEA checksum validation.

## 2. Supported environment

The target is the current Raspberry Pi OS based on Debian 13 (Trixie):

- Raspberry Pi OS Lite or Desktop;
- 32-bit or 64-bit;
- a Raspberry Pi with network access during installation;
- a USB serial GNSS receiver that can output text NMEA sentences.

The installer creates a dedicated Python virtual environment. It does not use
`sudo pip` to modify the system Python installation.

Official Raspberry Pi OS downloads:
https://www.raspberrypi.com/software/operating-systems/

## 3. Hardware

### Required

- Raspberry Pi;
- USB-connected GNSS receiver;
- local storage and power;
- network access when FTP upload is required.

### Optional GPIO reset

Some deployments connect **BCM GPIO17** (physical header pin 11) to the
receiver's active-low `RESET_N` input. The Raspberry Pi and receiver must share
ground.

If no `$`-prefixed sentence is observed for 300 seconds, the logger drives
GPIO17 Low for 200 ms, then returns it High and reconnects serial. The operation
is best-effort: missing wiring, unavailable GPIO, or a reset error is logged but
does not stop the program.

If GPIO17 is used by another circuit, set `enabled = false` in the `[reset]`
section of `config.ini` before operating the logger.

The u-blox ZED-F9P integration manual specifies active-low RESET_N and a minimum
100 ms Low pulse; this package uses 200 ms:
https://content.u-blox.com/sites/default/files/ZED-F9P_IntegrationManual_UBX-18010802.pdf

No external hardware watchdog HAT is required by version 2.2.0.

## 4. Installation from GitHub


```bash
sudo apt update
sudo apt install -y git python3-venv python3-lgpio
git clone https://github.com/naoyakadota/GNSSlogger
cd GNSSlogger
sudo ./setup.sh
```

`python3-lgpio` supplies the GPIO backend used by gpiozero on current
Raspberry Pi models. The installer exposes that system package to its dedicated
virtual environment.

The installer asks for:

1. device/site name;
2. FTP server address;
3. FTP user ID;
4. FTP password (input is hidden);
5. FTP parent directory (the device/site name is appended automatically);
6. the local GNSS data directory.

Default local data location:

```text
/home/<installing-user>/GPS_data/<device-name>/
```

Baud defaults to `auto`. GPIO reset defaults to enabled with a five-minute
timeout. Upload defaults to 00:10 local system time. These advanced settings
are edited in `config.ini`, so the basic installer stays short.

For example, entering `ELORA1` as the device/site name and
`/Naoya_FieldSensors/GPS` as the FTP parent directory produces this final
destination:

```text
/Naoya_FieldSensors/GPS/ELORA1
```

### FTP preflight

Before enabling the service, the installer:

1. connects and logs in;
2. enters each existing component of the remote path;
3. creates only missing components;
4. asks for confirmation if the final device/site folder already exists;
5. uploads a small randomized test file;
6. checks its remote size;
7. deletes the test file.

An existing parent directory does not require create permission. If a missing
directory cannot be created, or upload/size/delete validation fails, the
installer stops before enabling the service.

When the final directory already exists, a prompt similar to this appears:

```text
Remote directory already exists: /Naoya_FieldSensors/GPS/ELORA1
Existing .log files: 127; other entries: 2
Use this existing directory for this device? [y/N]:
```

The default is No to reduce the risk of mixing two devices into one folder.

## 5. Installed files and generated files

The files are separated by purpose so an update does not overwrite settings or
observations:

```text
Git clone (source)       ~/GNSSlogger/
Installed program       /opt/gps-logger/GPSlogger.py
Python environment      /opt/gps-logger/.venv/
Private configuration   /etc/gps-logger/config.ini
Startup commands        /etc/gps-logger/startup_commands.txt
systemd unit            /etc/systemd/system/gps-logger.service
Generated GNSS logs     ~/GPS_data/<device-name>/
```

There are no symlinks. The exact local data path is printed at the end of
installation.

Generated files look like:

```text
/home/pi/GPS_data/ELORA1/
├── elora1_2026-09-26.log        # current day; still being written
└── Uploaded/
    └── elora1_2026-09-25.log    # uploaded and locally archived
```

The current-day file is never uploaded. Files from completed dates move to
`Uploaded/` only after successful remote verification.

## 6. Confirm that files are being generated

The installer prints commands containing the actual selected path. They can
also be run manually:

```bash
ls -lh /home/pi/GPS_data/ELORA1/
tail -f /home/pi/GPS_data/ELORA1/elora1_$(date +%F).log
systemctl status gps-logger.service
journalctl -u gps-logger.service -f
```

Expected signs of normal operation:

- `systemctl status` shows `active (running)`;
- the file for today's date exists;
- `tail -f` prints new `$...` sentences;
- file size increases while the receiver is active.

Press `Ctrl+C` to exit `tail` or `journalctl -f`; this does not stop the
service.

## 7. Configuration

Edit the configuration as root:

```bash
sudo nano /etc/gps-logger/config.ini
```

Save in nano with `Ctrl+O`, Enter, then exit with `Ctrl+X`. Apply changes:

```bash
sudo systemctl restart gps-logger.service
```

The file is owned by root with mode `0600`. Root can read and edit it, while
other users cannot read the FTP password.

### Important settings

```ini
[device]
name = ELORA1

[ftp]
host = ftp.example.org
port = 21
user = gps-logger
password = local-secret
remote_dir = /Naoya_FieldSensors/GPS/ELORA1

[serial]
port = auto
baud = auto
auto_baud_candidates = 115200,9600,38400,57600,230400,460800
auto_probe_sec = 3.0
read_timeout_sec = 1.0
startup_commands_file = /etc/gps-logger/startup_commands.txt
startup_command_delay_sec = 0.2
startup_command_terminator = CRLF

[reset]
enabled = true
gpio_bcm = 17
after_no_valid_nmea_sec = 300
pulse_sec = 0.2
reconnect_wait_sec = 5

[upload]
daily_at = 00:10
retry_interval_minutes = 60

[storage]
log_dir = /home/pi/GPS_data/ELORA1
```

Do not copy a real password into Git, README files, screenshots, or issue
reports.

## 8. Serial-port and baud detection

New installations save `port = auto`. At startup and after every disconnect,
the logger checks:

1. `/dev/serial/by-id/*`;
2. USB serial devices reported by pySerial under `/dev/ttyACM*` and
   `/dev/ttyUSB*`.

This allows a receiver to be replaced even when its `/dev/serial/by-id/` name
changes. If a system has multiple NMEA serial devices, set `port` manually to
the intended `/dev/serial/by-id/...` path.

For `baud = auto`, each configured baud is tried until a `$`-prefixed sentence
is received. A known configuration may instead use a fixed path and/or fixed
integer baud.

## 9. Startup serial commands

Edit the optional command file:

```bash
sudo nano /etc/gps-logger/startup_commands.txt
sudo systemctl restart gps-logger.service
```

Rules:

- one text serial message per line;
- blank lines are ignored;
- lines beginning with `#` are comments;
- CRLF is appended by default;
- commands are sent after every serial connection or reconnection;
- a 0.2-second delay is used between commands.

Leave the file with comments only if the receiver outputs NMEA automatically.
This file supports UTF-8/ASCII text commands, not binary command packets.

## 10. FTP upload behavior

- First normal attempt: every day at 00:10 local Raspberry Pi time.
- A previous-day log found at service startup is attempted immediately.
- After failure: retry every 60 minutes until successful.
- Current-day file: never uploaded.
- Upload name: hidden `.partial` file first.
- Verification: local and remote byte sizes must match.
- Publication: verified partial file is renamed to the final `.log` name.
- Local archive: moved into `Uploaded/` only after verification.

If the final remote file already exists with the same size, it is treated as a
completed prior upload and the local file is archived. A different size is a
conflict: the local file is retained and the error is logged.

FTP failures do not stop GNSS logging or terminate the upload scheduler.

## 11. Service operation

```bash
sudo systemctl start gps-logger.service
sudo systemctl stop gps-logger.service
sudo systemctl restart gps-logger.service
systemctl status gps-logger.service
journalctl -u gps-logger.service -f
```

The service runs as root to access the root-only configuration and optional
GPIO. It uses `Restart=always` and waits 10 seconds before restarting after an
exit.

## 12. Updating

From the original clone directory:

```bash
cd ~/gps-logger
git pull
sudo ./setup.sh
```

Existing values are offered again or retained. The startup command file is not
overwritten. The FTP preflight runs again, including confirmation of the
existing final directory.

## 13. Migration from version 2.1.0

Automatic detection or removal of old service units is intentionally not
implemented. Before installing 2.2.0, list likely units and manually disable
the old one:

```bash
systemctl list-unit-files | grep -i gps
sudo systemctl disable --now <old-service-name>
```

Do not run the old and new logger services simultaneously. They may compete
for the same serial port and write duplicate files.

The installer does not delete or move old logs automatically. Enter the old
data directory during setup if that location should continue to be used, or
move the files manually after stopping the old service.

## 14. Troubleshooting

### Service repeatedly restarts

```bash
systemctl status gps-logger.service
journalctl -u gps-logger.service -n 100 --no-pager
```

Check configuration syntax and file paths first.

### No serial device is found

```bash
ls -l /dev/serial/by-id/ 2>/dev/null
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

Reconnect USB, check receiver power, and review the journal. If necessary, set
`port` to the correct `/dev/serial/by-id/...` path.

### Serial connects but no log appears

- confirm that the receiver emits text NMEA lines beginning with `$`;
- check baud detection messages in the journal;
- add required text commands to `startup_commands.txt`;
- verify that binary-only output is not being used.

### Repeated GPIO warnings

An unwired reset pin is not fatal. If BCM17 belongs to another circuit, set
`enabled = false` under `[reset]` and restart the service.

### FTP fails

Review the journal for login, path, permission, size-check, or filename-conflict
errors. Confirm that the complete `remote_dir` is correct. The logger retains
unverified local files and retries hourly.

## 15. Security and limitations

- FTP transmits credentials and data without encryption. Use only on an
  appropriate trusted/network-controlled deployment. SFTP is not implemented.
- `config.ini` contains the recoverable FTP password and must remain mode
  `0600`.
- The service runs as root.
- NMEA checksums are not validated.
- Only text startup commands are supported.
- Old service conflicts are resolved manually.
- Actual receiver/FTP/GPIO behavior must be verified on deployment hardware;
  unit tests cannot prove electrical wiring or server policy.

See [CHANGELOG.md](CHANGELOG.md) for release history.
