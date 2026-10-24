"""Linux credential encryption fails closed and detects tampering."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from cryptography.fernet import Fernet
from fivek.calendar_sync import CalendarError, protect


class LinuxCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.key = Path(self.temp.name) / 'calendar.key'
        self.key.write_bytes(Fernet.generate_key())
        self.env = SimpleNamespace(name='posix', getenv=lambda _: str(self.key))

    def test_round_trip_and_tampering(self):
        with patch('fivek.calendar_sync.os', self.env), patch.object(Path, 'stat', return_value=SimpleNamespace(st_mode=0o100600)):
            encrypted = protect(b'private calendar credentials')
            self.assertNotIn(b'private calendar credentials', encrypted)
            self.assertEqual(protect(encrypted, decrypt=True), b'private calendar credentials')
            with self.assertRaises(CalendarError):
                protect(encrypted[:-3] + b'bad', decrypt=True)
            self.key.write_bytes(Fernet.generate_key())
            with self.assertRaises(CalendarError):
                protect(encrypted, decrypt=True)

    def test_missing_key_and_open_permissions_rejected(self):
        with patch('fivek.calendar_sync.os', SimpleNamespace(name='posix', getenv=lambda _: None)):
            with self.assertRaises(CalendarError):
                protect(b'secret')
        with patch('fivek.calendar_sync.os', self.env), patch.object(Path, 'stat', return_value=SimpleNamespace(st_mode=0o100644)):
            with self.assertRaises(CalendarError):
                protect(b'secret')
