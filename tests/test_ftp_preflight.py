import unittest
from unittest import mock

import ftp_preflight


class FakeFTP:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.current = "/"
        self.created = []
        self.files = {}

    def cwd(self, part):
        if part == "/":
            self.current = "/"
            return
        target = self.current.rstrip("/") + "/" + part
        if target not in self.existing:
            raise ftp_preflight.ftplib.error_perm("550 missing")
        self.current = target

    def mkd(self, part):
        target = self.current.rstrip("/") + "/" + part
        self.existing.add(target)
        self.created.append(target)

    def storbinary(self, command, file):
        self.files[command.removeprefix("STOR ")] = file.read()

    def size(self, name):
        return len(self.files[name])

    def delete(self, name):
        self.files.pop(name, None)


class FTPPreflightTests(unittest.TestCase):
    def test_existing_final_directory_is_reported(self):
        ftp = FakeFTP(existing={"/parent", "/parent/SITE1"})
        self.assertTrue(ftp_preflight.enter_or_create(ftp, "/parent/SITE1"))
        self.assertEqual(ftp.created, [])

    def test_missing_components_are_created_in_order(self):
        ftp = FakeFTP(existing={"/parent"})
        self.assertFalse(ftp_preflight.enter_or_create(ftp, "/parent/sub/SITE1"))
        self.assertEqual(ftp.created, ["/parent/sub", "/parent/sub/SITE1"])

    def test_upload_test_verifies_and_removes_file(self):
        ftp = FakeFTP()
        with mock.patch.object(ftp_preflight.secrets, "token_hex", return_value="abc"):
            ftp_preflight.test_upload(ftp)
        self.assertEqual(ftp.files, {})


if __name__ == "__main__":
    unittest.main()
