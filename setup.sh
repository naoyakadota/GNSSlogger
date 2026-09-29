#!/usr/bin/env bash
set -euo pipefail

# Re-run as root, but remember the human user who launched the installer.
if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install_dir="/opt/gps-logger"
config_dir="/etc/gps-logger"
config_path="$config_dir/config.ini"
startup_commands_path="$config_dir/startup_commands.txt"
service_path="/etc/systemd/system/gps-logger.service"

install_user="${SUDO_USER:-}"
if [[ -z "$install_user" || "$install_user" == "root" ]]; then
  install_user="$(logname 2>/dev/null || printf 'root')"
fi
install_home="$(getent passwd "$install_user" | cut -d: -f6)"
if [[ -z "$install_home" ]]; then
  echo "Could not determine the home directory for user: $install_user" >&2
  exit 2
fi
install_group="$(id -gn "$install_user")"

install -d -m 0755 "$config_dir"

existing_value() {
  local section="$1"
  local key="$2"
  [[ -f "$config_path" ]] || return 0
  python3 - "$config_path" "$section" "$key" <<'PY'
import configparser
import sys

config = configparser.ConfigParser(interpolation=None)
config.read(sys.argv[1], encoding="utf-8")
print(config.get(sys.argv[2], sys.argv[3], fallback=""))
PY
}

prompt_default() {
  local prompt="$1"
  local default="$2"
  local value
  read -r -p "$prompt [$default]: " value
  printf '%s' "${value:-$default}"
}

remote_parent_from_existing() {
  local remote_dir="$1"
  [[ -n "$remote_dir" ]] || return 0
  python3 - "$remote_dir" <<'PY'
import sys
from pathlib import PurePosixPath

remote_dir = PurePosixPath("/" + sys.argv[1].strip("/"))
print(remote_dir.parent)
PY
}

build_remote_dir() {
  local parent="$1"
  local device="$2"
  python3 - "$parent" "$device" <<'PY'
import sys
from pathlib import PurePosixPath

parent = PurePosixPath("/" + sys.argv[1].strip("/"))
print(parent / sys.argv[2])
PY
}

old_device="$(existing_value device name)"
old_host="$(existing_value ftp host)"
old_user="$(existing_value ftp user)"
old_password="$(existing_value ftp password)"
old_ftp_port="$(existing_value ftp port)"
old_remote_dir="$(existing_value ftp remote_dir)"
old_port="$(existing_value serial port)"
old_baud="$(existing_value serial baud)"
old_baud_candidates="$(existing_value serial auto_baud_candidates)"
old_probe_sec="$(existing_value serial auto_probe_sec)"
old_read_timeout="$(existing_value serial read_timeout_sec)"
old_commands_file="$(existing_value serial startup_commands_file)"
old_command_delay="$(existing_value serial startup_command_delay_sec)"
old_command_terminator="$(existing_value serial startup_command_terminator)"
old_log_dir="$(existing_value storage log_dir)"
old_reset_enabled="$(existing_value reset enabled)"
old_reset_gpio="$(existing_value reset gpio_bcm)"
old_reset_timeout="$(existing_value reset after_no_valid_nmea_sec)"
old_reset_pulse="$(existing_value reset pulse_sec)"
old_reconnect_wait="$(existing_value reset reconnect_wait_sec)"
old_upload_time="$(existing_value upload daily_at)"
old_retry_minutes="$(existing_value upload retry_interval_minutes)"

# Older 2.2.0 drafts stored the complete per-device FTP path. Present its
# parent on update so re-running setup does not append the device name twice.
old_remote_parent="$(remote_parent_from_existing "$old_remote_dir")"

device_name="$(prompt_default 'Device/site name' "${old_device:-GPS1}")"
if [[ ! "$device_name" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Device/site name may contain only letters, numbers, dot, underscore, and hyphen." >&2
  exit 2
fi

ftp_host="$(prompt_default 'FTP server address' "${old_host:-ftp.example.org}")"
ftp_user="$(prompt_default 'FTP user ID' "${old_user:-gps-logger}")"
if [[ -n "$old_password" ]]; then
  read -r -s -p "FTP password [Enter keeps the stored password]: " ftp_password
  printf '\n'
  ftp_password="${ftp_password:-$old_password}"
else
  read -r -s -p "FTP password: " ftp_password
  printf '\n'
fi
if [[ -z "$ftp_password" ]]; then
  echo "FTP password must not be empty." >&2
  exit 2
fi

ftp_parent_dir="$(prompt_default 'FTP parent directory' "${old_remote_parent:-/Naoya_FieldSensors/GPS}")"
ftp_remote_dir="$(build_remote_dir "$ftp_parent_dir" "$device_name")"
echo "FTP device directory: $ftp_remote_dir"
# New installations use runtime discovery so replacing a receiver does not
# leave the service pinned to a stale /dev/serial/by-id path. Preserve an
# existing explicit setting during updates; it can be changed to auto in
# config.ini when device replacement is desired.
serial_port="${old_port:-auto}"
serial_baud="${old_baud:-auto}"
ftp_port="${old_ftp_port:-21}"
baud_candidates="${old_baud_candidates:-115200,9600,38400,57600,230400,460800}"
probe_sec="${old_probe_sec:-3.0}"
read_timeout="${old_read_timeout:-1.0}"
commands_file="${old_commands_file:-$startup_commands_path}"
command_delay="${old_command_delay:-0.2}"
command_terminator="${old_command_terminator:-CRLF}"
default_log_dir="${old_log_dir:-$install_home/GPS_data/$device_name}"
log_dir="$(prompt_default 'GNSS data directory' "$default_log_dir")"
reset_enabled="${old_reset_enabled:-true}"
reset_gpio="${old_reset_gpio:-17}"
reset_timeout="${old_reset_timeout:-300}"
reset_pulse="${old_reset_pulse:-0.2}"
reconnect_wait="${old_reconnect_wait:-5}"
upload_time="${old_upload_time:-00:10}"
retry_minutes="${old_retry_minutes:-60}"

# ConfigParser values must remain single-line. Password input stays hidden and
# is written only to the root-readable temporary config below.
for value in "$device_name" "$ftp_host" "$ftp_user" "$ftp_password" "$ftp_parent_dir" "$ftp_remote_dir" "$serial_port" "$log_dir"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "Configuration values must not contain newlines." >&2
    exit 2
  fi
done

tmp_config="$(mktemp "$config_dir/config.ini.XXXXXX")"
cleanup() {
  rm -f -- "$tmp_config"
}
trap cleanup EXIT
chmod 0600 "$tmp_config"
{
  printf '[device]\nname = %s\n\n' "$device_name"
  printf '[ftp]\nhost = %s\nport = %s\nuser = %s\npassword = %s\nremote_dir = %s\n\n' \
    "$ftp_host" "$ftp_port" "$ftp_user" "$ftp_password" "$ftp_remote_dir"
  printf '[serial]\nport = %s\nbaud = %s\nauto_baud_candidates = %s\nauto_probe_sec = %s\nread_timeout_sec = %s\nstartup_commands_file = %s\nstartup_command_delay_sec = %s\nstartup_command_terminator = %s\n\n' \
    "$serial_port" "$serial_baud" "$baud_candidates" "$probe_sec" "$read_timeout" \
    "$commands_file" "$command_delay" "$command_terminator"
  printf '[reset]\nenabled = %s\ngpio_bcm = %s\nafter_no_valid_nmea_sec = %s\npulse_sec = %s\nreconnect_wait_sec = %s\n\n' \
    "$reset_enabled" "$reset_gpio" "$reset_timeout" "$reset_pulse" "$reconnect_wait"
  printf '[upload]\ndaily_at = %s\nretry_interval_minutes = %s\n\n' \
    "$upload_time" "$retry_minutes"
  printf '[storage]\nlog_dir = %s\n' "$log_dir"
} > "$tmp_config"

# Prove the destination before the logger service is installed or started.
python3 "$source_dir/ftp_preflight.py" "$tmp_config"

install -d -m 0755 "$install_dir" "$log_dir"
chown "$install_user:$install_group" "$log_dir"
install -m 0755 "$source_dir/GPSlogger.py" "$install_dir/GPSlogger.py"
install -m 0644 "$source_dir/requirements.txt" "$install_dir/requirements.txt"

# Raspberry Pi OS provides the all-model lgpio backend as the system package
# python3-lgpio. Expose system packages inside the otherwise dedicated venv so
# gpiozero can use that backend on Pi 3A+ and current Raspberry Pi models.
python3 -m venv --system-site-packages "$install_dir/.venv"
"$install_dir/.venv/bin/python" -m pip install --upgrade pip
"$install_dir/.venv/bin/python" -m pip install -r "$install_dir/requirements.txt"

chown root:root "$tmp_config"
mv -f "$tmp_config" "$config_path"
chmod 0600 "$config_path"

# Preserve receiver-specific commands when setup.sh is run again.
if [[ ! -e "$commands_file" ]]; then
  install -d -m 0755 "$(dirname "$commands_file")"
  install -m 0600 "$source_dir/startup_commands.example.txt" "$commands_file"
fi
chown root:root "$commands_file"
chmod 0600 "$commands_file"

install -m 0644 "$source_dir/gps-logger.service" "$service_path"
systemctl daemon-reload
systemctl enable --now gps-logger.service

today="$(date +%F)"
current_log="$log_dir/${device_name,,}_$today.log"
echo
echo "GPS Logger installation completed."
echo "GNSS data directory: $log_dir"
echo "Check generated files: ls -lh '$log_dir'"
echo "Monitor today's log: tail -f '$current_log'"
echo "Service status: systemctl status gps-logger.service"
echo "Service logs: journalctl -u gps-logger.service -f"
echo "Configuration: sudo nano $config_path"
echo "Startup commands: sudo nano $commands_file"
