#!/usr/bin/env python3
"""Validate the configured FTP destination before enabling the service."""

import configparser
import ftplib
import io
import secrets
import sys
from pathlib import Path, PurePosixPath


def enter_or_create(ftp: ftplib.FTP, remote_dir: str) -> bool:
    """Enter the path recursively and return True if its final folder existed."""
    path = PurePosixPath("/" + remote_dir.strip("/"))
    if path == PurePosixPath("/"):
        raise ValueError("FTP remote directory must not be the server root")

    ftp.cwd("/")
    parts = path.parts[1:]
    final_existed = False
    for index, part in enumerate(parts):
        is_final = index == len(parts) - 1
        try:
            ftp.cwd(part)
            if is_final:
                final_existed = True
        except ftplib.error_perm as exc:
            if not str(exc).startswith("550"):
                raise
            # Existing parent folders are only entered. We request mkdir only
            # for components that are actually missing.
            ftp.mkd(part)
            ftp.cwd(part)
    return final_existed


def count_remote_files(ftp: ftplib.FTP) -> tuple[int, int]:
    names = [PurePosixPath(name).name for name in ftp.nlst()]
    names = [name for name in names if name not in {".", ".."}]
    log_count = sum(name.lower().endswith(".log") for name in names)
    return log_count, len(names) - log_count


def test_upload(ftp: ftplib.FTP) -> None:
    name = f".gps-logger-install-test-{secrets.token_hex(6)}.tmp"
    content = b"GPS logger FTP installation test\r\n"
    try:
        ftp.storbinary(f"STOR {name}", io.BytesIO(content))
        remote_size = ftp.size(name)
        if remote_size != len(content):
            raise RuntimeError(
                f"FTP test size mismatch: local {len(content)}, remote {remote_size}"
            )
    finally:
        try:
            ftp.delete(name)
        except ftplib.all_errors:
            pass


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: ftp_preflight.py CONFIG_PATH", file=sys.stderr)
        return 2

    config = configparser.ConfigParser(interpolation=None)
    if not config.read(Path(sys.argv[1]), encoding="utf-8"):
        print("FTP preflight: configuration could not be read", file=sys.stderr)
        return 2

    remote_dir = config.get("ftp", "remote_dir")
    ftp = ftplib.FTP()
    try:
        print(f"FTP preflight: checking {remote_dir}")
        ftp.connect(
            config.get("ftp", "host"),
            port=config.getint("ftp", "port", fallback=21),
            timeout=20,
        )
        ftp.login(config.get("ftp", "user"), config.get("ftp", "password"))
        ftp.voidcmd("TYPE I")
        final_existed = enter_or_create(ftp, remote_dir)

        if final_existed:
            log_count, other_count = count_remote_files(ftp)
            print(f"Remote directory already exists: {remote_dir}")
            print(f"Existing .log files: {log_count}; other entries: {other_count}")
            answer = input("Use this existing directory for this device? [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Installation cancelled; existing FTP directory was not approved.")
                return 3

        test_upload(ftp)
        print("FTP preflight passed: upload, size check, and delete succeeded.")
        return 0
    except Exception as exc:
        print(f"FTP preflight failed: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
