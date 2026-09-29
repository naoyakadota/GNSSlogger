import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TEMP = tempfile.TemporaryDirectory()
TEMP_PATH = Path(TEMP.name)
CONFIG_PATH = TEMP_PATH / "config.ini"
CONFIG_PATH.write_text(
    f"""[device]
name = TEST1

[ftp]
host = ftp.example.invalid
port = 21
user = test
password = test
remote_dir = /parent/TEST1

[serial]
port = auto
baud = auto
auto_baud_candidates = 115200,9600
auto_probe_sec = 1
read_timeout_sec = 0.1
startup_commands_file = {TEMP_PATH / 'startup_commands.txt'}
startup_command_delay_sec = 0
startup_command_terminator = CRLF

[reset]
enabled = false
after_no_valid_nmea_sec = 300

[upload]
daily_at = 00:10
retry_interval_minutes = 60

[storage]
log_dir = {TEMP_PATH / 'data'}
""",
    encoding="utf-8",
)
os.environ["GPS_LOGGER_CONFIG"] = str(CONFIG_PATH)
os.environ["GPIOZERO_PIN_FACTORY"] = "mock"

SPEC = importlib.util.spec_from_file_location("gpslogger_under_test", ROOT / "GPSlogger.py")
gps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gps)


class FakeSerial:
    def __init__(self, baud, lines):
        self.baud = baud
        self.lines = list(lines)
        self.writes = []
        self.closed = False

    def readline(self):
        return self.lines.pop(0) if self.lines else b""

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeFTP:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.current = "/"
        self.created = []

    def cwd(self, part):
        if part == "/":
            self.current = "/"
            return
        target = self.current.rstrip("/") + "/" + part
        if target not in self.existing:
            raise gps.ftplib.error_perm("550 missing")
        self.current = target

    def mkd(self, part):
        target = self.current.rstrip("/") + "/" + part
        self.existing.add(target)
        self.created.append(target)


class UploadFTP:
    def __init__(self):
        self.files = {}

    def size(self, name):
        if name not in self.files:
            raise gps.ftplib.error_perm("550 missing")
        return len(self.files[name])

    def delete(self, name):
        if name not in self.files:
            raise gps.ftplib.error_perm("550 missing")
        del self.files[name]

    def storbinary(self, command, file):
        self.files[command.removeprefix("STOR ")] = file.read()

    def rename(self, source, destination):
        self.files[destination] = self.files.pop(source)


class GPSLoggerTests(unittest.TestCase):
    def setUp(self):
        gps.startup_commands_file = TEMP_PATH / "startup_commands.txt"
        gps.startup_command_delay_sec = 0

    def test_startup_commands_ignore_comments_and_append_crlf(self):
        gps.startup_commands_file.write_text(
            "# comment\n\nCMD ONE\nCMD TWO\n", encoding="utf-8"
        )
        serial_device = FakeSerial(115200, [])
        gps.send_startup_commands(serial_device)
        self.assertEqual(serial_device.writes, [b"CMD ONE\r\n", b"CMD TWO\r\n"])

    def test_auto_baud_selects_first_baud_with_nmea(self):
        gps.startup_commands_file.write_text("", encoding="utf-8")
        devices = []

        def build_serial(_port, baud, timeout):
            lines = [b""] if baud == 115200 else [b"$GPGGA,test\r\n"]
            device = FakeSerial(baud, lines)
            devices.append(device)
            return device

        with mock.patch.object(gps, "serial_port_candidates", return_value=["/dev/test"]), \
             mock.patch.object(gps.serial, "Serial", side_effect=build_serial), \
             mock.patch.object(gps.time, "monotonic", side_effect=[0, 0.1, 2, 3, 3.1]):
            device, pending, port, baud = gps.open_serial()

        self.assertEqual((port, baud), ("/dev/test", 9600))
        self.assertEqual(pending, b"$GPGGA,test\r\n")
        self.assertTrue(devices[0].closed)
        self.assertIs(device, devices[1])

    def test_remote_directory_creates_only_missing_components(self):
        ftp = FakeFTP(existing={"/parent"})
        gps.ensure_remote_directory(ftp, "/parent/TEST1")
        self.assertEqual(ftp.created, ["/parent/TEST1"])
        self.assertEqual(ftp.current, "/parent/TEST1")

    def test_pending_logs_exclude_today(self):
        gps.log_dir.mkdir(exist_ok=True)
        today = gps.datetime.now().strftime("%Y-%m-%d")
        (gps.log_dir / f"test1_{today}.log").write_text("today", encoding="utf-8")
        old = gps.log_dir / "test1_2020-01-01.log"
        old.write_text("old", encoding="utf-8")
        self.assertEqual(gps.pending_log_files(), [old])

    def test_upload_is_verified_renamed_and_archived(self):
        gps.log_dir.mkdir(exist_ok=True)
        local_file = gps.log_dir / "test1_2019-01-01.log"
        local_file.write_bytes(b"nmea-data")
        ftp = UploadFTP()

        gps.upload_one_file(ftp, local_file)

        self.assertEqual(ftp.files[local_file.name], b"nmea-data")
        self.assertFalse(local_file.exists())
        self.assertEqual(
            (gps.log_dir / "Uploaded" / local_file.name).read_bytes(), b"nmea-data"
        )


if __name__ == "__main__":
    unittest.main()
