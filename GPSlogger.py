#!/usr/bin/env python3
"""Simple GNSS NMEA logger with daily FTP upload.

Original code: Naoya Kadota, 2020
Portfolio: https://nkadota.com
Version: 2.2.0
"""

import configparser
import ftplib
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path, PurePosixPath

import schedule
import serial
from gpiozero import OutputDevice
from serial.tools import list_ports

CONFIG_PATH = Path(os.environ.get("GPS_LOGGER_CONFIG", "/etc/gps-logger/config.ini"))


def load_config(path: Path = CONFIG_PATH) -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    if not config.read(path, encoding="utf-8"):
        raise RuntimeError(f"GPS logger config not found: {path}")
    return config


config = load_config()

# Device-specific settings live outside this file so program updates do not
# replace credentials or deployment settings.
device_name = config.get("device", "name")
ftp_host = config.get("ftp", "host")
ftp_user = config.get("ftp", "user")
ftp_password = config.get("ftp", "password")
ftp_port = config.getint("ftp", "port", fallback=21)
ftp_remote_dir = config.get("ftp", "remote_dir")

serial_port_setting = config.get("serial", "port", fallback="auto").strip()
serial_baud_setting = config.get("serial", "baud", fallback="auto").strip().lower()
serial_auto_baud_candidates = config.get(
    "serial", "auto_baud_candidates", fallback="115200,9600,38400,57600,230400,460800"
)
serial_auto_probe_sec = config.getfloat("serial", "auto_probe_sec", fallback=3.0)
serial_read_timeout_sec = config.getfloat("serial", "read_timeout_sec", fallback=1.0)
startup_commands_file = Path(
    config.get("serial", "startup_commands_file", fallback="/etc/gps-logger/startup_commands.txt")
)
startup_command_delay_sec = config.getfloat(
    "serial", "startup_command_delay_sec", fallback=0.2
)
startup_command_terminator = config.get(
    "serial", "startup_command_terminator", fallback="CRLF"
).strip().upper()

reset_enabled = config.getboolean("reset", "enabled", fallback=True)
reset_gpio = config.getint("reset", "gpio_bcm", fallback=17)
reset_after_no_nmea_sec = config.getfloat(
    "reset", "after_no_valid_nmea_sec", fallback=300.0
)
reset_pulse_sec = config.getfloat("reset", "pulse_sec", fallback=0.2)
reset_reconnect_wait_sec = config.getfloat("reset", "reconnect_wait_sec", fallback=5.0)

upload_time = config.get("upload", "daily_at", fallback="00:10")
upload_retry_minutes = config.getint("upload", "retry_interval_minutes", fallback=60)
log_dir = Path(config.get("storage", "log_dir"))


class OptionalGPSReset:
    """Best-effort RESET_N control; GPIO failures must not stop logging."""

    def __init__(self, enabled: bool, gpio_bcm: int, pulse_sec: float) -> None:
        self.gpio_bcm = gpio_bcm
        self.pulse_sec = max(0.1, pulse_sec)
        self.output = None
        if not enabled:
            print("GNSS reset: disabled")
            return
        try:
            # RESET_N is inactive High and asserted Low.
            self.output = OutputDevice(gpio_bcm, active_high=True, initial_value=True)
            print(f"GNSS reset: BCM GPIO{gpio_bcm} initialized")
        except Exception as exc:
            print(f"GNSS reset unavailable; logger will continue: {exc}")

    def pulse(self) -> bool:
        if self.output is None:
            print("GNSS reset requested but GPIO is unavailable; continuing")
            return False
        try:
            print(f"Pulsing BCM GPIO{self.gpio_bcm} Low to reset the GNSS receiver")
            self.output.off()
            time.sleep(self.pulse_sec)
            self.output.on()
            print("GNSS reset pulse completed")
            return True
        except Exception as exc:
            print(f"GNSS reset failed; logger will continue: {exc}")
            try:
                self.output.on()
            except Exception:
                pass
            return False


gps_reset = OptionalGPSReset(reset_enabled, reset_gpio, reset_pulse_sec)


def baud_candidates() -> list[int]:
    """Return one configured baud or the ordered auto-detection list."""
    if serial_baud_setting != "auto":
        value = int(serial_baud_setting)
        if value <= 0:
            raise ValueError("serial baud must be positive")
        return [value]
    result = []
    for text in serial_auto_baud_candidates.split(","):
        if not text.strip():
            continue
        value = int(text.strip())
        if value <= 0:
            raise ValueError("auto baud candidates must be positive")
        if value not in result:
            result.append(value)
    if not result:
        raise ValueError("auto baud candidate list is empty")
    return result


def serial_port_candidates() -> list[str]:
    """Return a fixed port or discover likely USB serial ports."""
    if serial_port_setting.lower() != "auto":
        return [serial_port_setting]
    result = []
    # /dev/serial/by-id names normally remain stable if ttyACM numbers change.
    by_id_dir = Path("/dev/serial/by-id")
    if by_id_dir.is_dir():
        result.extend(str(path) for path in sorted(by_id_dir.iterdir()))
    resolved = set()
    for path in result:
        try:
            resolved.add(str(Path(path).resolve()))
        except OSError:
            pass
    # Fall back to pySerial discovery for receivers without a by-id link.
    for info in sorted(list_ports.comports(), key=lambda item: item.device):
        if not (info.device.startswith("/dev/ttyACM") or info.device.startswith("/dev/ttyUSB")):
            continue
        try:
            target = str(Path(info.device).resolve())
        except OSError:
            target = info.device
        if target not in resolved:
            result.append(info.device)
            resolved.add(target)
    if not result:
        raise serial.SerialException("no USB serial device found")
    return result


def command_terminator() -> bytes:
    values = {"CRLF": b"\r\n", "CR": b"\r", "LF": b"\n", "NONE": b""}
    if startup_command_terminator not in values:
        raise ValueError("startup_command_terminator must be CRLF, CR, LF, or NONE")
    return values[startup_command_terminator]


def load_startup_commands() -> list[str]:
    """Read one text serial command per non-empty, non-comment line."""
    if not startup_commands_file.exists():
        return []
    commands = []
    with startup_commands_file.open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if line and not line.startswith("#"):
                commands.append(line)
    return commands


def send_startup_commands(ser) -> None:
    """Send optional receiver-start commands after every serial connection."""
    commands = load_startup_commands()
    if not commands:
        return
    ending = command_terminator()
    print(f"sending {len(commands)} startup serial command(s)")
    for command in commands:
        ser.write(command.encode("utf-8") + ending)
        ser.flush()
        time.sleep(max(0.0, startup_command_delay_sec))


def open_serial():
    """Open a receiver and optionally identify its port and baud from NMEA."""
    fixed_port = serial_port_setting.lower() != "auto"
    fixed_baud = serial_baud_setting != "auto"
    last_error = None
    for port in serial_port_candidates():
        for baud in baud_candidates():
            ser = None
            try:
                print(f"serial probe: {port} at {baud} baud")
                ser = serial.Serial(port, baud, timeout=serial_read_timeout_sec)
                send_startup_commands(ser)
                # Fully fixed settings keep the old immediate-connect behavior.
                if fixed_port and fixed_baud:
                    return ser, None, port, baud
                deadline = time.monotonic() + max(1.0, serial_auto_probe_sec)
                while time.monotonic() < deadline:
                    raw_line = ser.readline()
                    if raw_line.startswith(b"$"):
                        print(f"serial detected: {port} at {baud} baud")
                        return ser, raw_line, port, baud
            except Exception as exc:
                last_error = exc
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
    message = "serial detection failed: no $-prefixed NMEA sentence received"
    if last_error is not None:
        message += f"; last error: {last_error}"
    raise serial.SerialException(message)


def current_log_path(timestamp: datetime) -> Path:
    return log_dir / f"{device_name.lower()}_{timestamp:%Y-%m-%d}.log"


def append_sentence(line: str, timestamp: datetime) -> None:
    path = current_log_path(timestamp)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8") as file:
        file.write(line)
    if is_new:
        print(f"new log file created: {path}")


def GNSSlogger() -> None:
    """Continuously reconnect, read NMEA, and append it to a daily file."""
    serial_error = 0
    last_valid_sentence = time.monotonic()
    log_dir.mkdir(parents=True, exist_ok=True)
    while True:
        ser = None
        try:
            ser, pending_raw, selected_port, selected_baud = open_serial()
            print(f"serial connected: {selected_port} at {selected_baud} baud")
            serial_error = 0
            while True:
                raw_line = pending_raw if pending_raw is not None else ser.readline()
                pending_raw = None
                if raw_line:
                    try:
                        line = raw_line.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        print(f"serial decode error; line ignored: {exc}")
                        line = ""
                    if line.startswith("$"):
                        last_valid_sentence = time.monotonic()
                        append_sentence(line, datetime.now())
                # Finite serial timeout allows this check even when input is silent.
                if (
                    reset_after_no_nmea_sec > 0
                    and time.monotonic() - last_valid_sentence
                    >= reset_after_no_nmea_sec
                ):
                    print("no valid $-prefixed NMEA sentence within the reset interval")
                    gps_reset.pulse()
                    last_valid_sentence = time.monotonic()
                    break
        except Exception as exc:
            print(f"serial connection failed (attempt {serial_error}): {exc}")
            serial_error += 1
            if (
                reset_after_no_nmea_sec > 0
                and time.monotonic() - last_valid_sentence
                >= reset_after_no_nmea_sec
            ):
                gps_reset.pulse()
                last_valid_sentence = time.monotonic()
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
        time.sleep(max(1.0, reset_reconnect_wait_sec))


def pending_log_files() -> list[Path]:
    """Return complete daily logs; today's file is still being written."""
    today_suffix = datetime.now().strftime("_%Y-%m-%d.log")
    return sorted(path for path in log_dir.glob("*.log") if not path.name.endswith(today_suffix))


def ensure_remote_directory(ftp: ftplib.FTP, remote_dir: str) -> None:
    """Enter the configured FTP path, creating only missing components."""
    path = PurePosixPath("/" + remote_dir.strip("/"))
    ftp.cwd("/")
    for part in path.parts[1:]:
        try:
            ftp.cwd(part)
        except ftplib.error_perm as exc:
            if not str(exc).startswith("550"):
                raise
            ftp.mkd(part)
            ftp.cwd(part)


def remote_size(ftp: ftplib.FTP, name: str):
    """Return remote size, or None if SIZE is unsupported or the file is absent."""
    try:
        return ftp.size(name)
    except ftplib.all_errors:
        return None


def archive_uploaded(path: Path) -> None:
    archive_dir = log_dir / "Uploaded"
    archive_dir.mkdir(mode=0o755, exist_ok=True)
    destination = archive_dir / path.name
    if destination.exists():
        if destination.stat().st_size == path.stat().st_size:
            path.unlink()
            return
        raise RuntimeError(f"local archive conflict: {destination}")
    shutil.move(str(path), str(destination))


def upload_one_file(ftp: ftplib.FTP, path: Path) -> None:
    """Upload through a temporary name, verify size, then publish."""
    local_size = path.stat().st_size
    existing_size = remote_size(ftp, path.name)
    if existing_size is not None:
        if existing_size == local_size:
            print(f"remote file already matches; archiving local file: {path.name}")
            archive_uploaded(path)
            return
        raise RuntimeError(
            f"remote file has a different size: {path.name} "
            f"(local {local_size}, remote {existing_size})"
        )
    temporary_name = f".{path.name}.partial"
    try:
        try:
            ftp.delete(temporary_name)
        except ftplib.all_errors:
            pass
        print(f"uploading {path.name}...")
        with path.open("rb") as file:
            ftp.storbinary(f"STOR {temporary_name}", file)
        uploaded_size = remote_size(ftp, temporary_name)
        if uploaded_size != local_size:
            raise RuntimeError(
                f"FTP size verification failed for {path.name}: "
                f"local {local_size}, remote {uploaded_size}"
            )
        ftp.rename(temporary_name, path.name)
        archive_uploaded(path)
        print(f"upload completed: {path.name}")
    except Exception:
        try:
            ftp.delete(temporary_name)
        except ftplib.all_errors:
            pass
        raise


def ftp_uploader() -> bool:
    """Upload complete logs. False asks the scheduler to retry later."""
    files = pending_log_files()
    if not files:
        print("FTP: nothing to upload")
        return True
    ftp = ftplib.FTP()
    try:
        ftp.connect(ftp_host, port=ftp_port, timeout=20)
        ftp.login(ftp_user, ftp_password)
        ftp.voidcmd("TYPE I")
        ensure_remote_directory(ftp, ftp_remote_dir)
        for path in files:
            upload_one_file(ftp, path)
        return True
    except Exception as exc:
        print(f"FTP upload failed; will retry later: {exc}")
        return False
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


ftp_retry_needed = threading.Event()


def run_scheduled_upload() -> None:
    """Catch every FTP error so the uploader thread cannot silently die."""
    try:
        if ftp_uploader():
            ftp_retry_needed.clear()
        else:
            ftp_retry_needed.set()
    except Exception as exc:
        print(f"unexpected FTP error; will retry later: {exc}")
        ftp_retry_needed.set()


def retry_upload_if_needed() -> None:
    if ftp_retry_needed.is_set():
        run_scheduled_upload()


def uploader_scheduler() -> None:
    schedule.every().day.at(upload_time).do(run_scheduled_upload)
    schedule.every(max(1, upload_retry_minutes)).minutes.do(retry_upload_if_needed)
    # A reboot after the scheduled time should not delay old files another day.
    if pending_log_files():
        run_scheduled_upload()
    while True:
        try:
            schedule.run_pending()
        except Exception as exc:
            print(f"upload scheduler error; continuing: {exc}")
        time.sleep(1)


def main() -> None:
    logger_thread = threading.Thread(target=GNSSlogger, name="gnss-logger")
    uploader_thread = threading.Thread(target=uploader_scheduler, name="ftp-uploader", daemon=True)
    logger_thread.start()
    uploader_thread.start()
    logger_thread.join()


if __name__ == "__main__":
    main()
