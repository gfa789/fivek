from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from garmin_api import GarminAuthenticationError
from fivek.account import save_connection, auth_from_store
from fivek.config import Config


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(data_dir=Path(self.temp.name))

    def test_saved_connection_is_encrypted_and_recovers_after_restart(self):
        # Exercise the shared storage contract independently of the host crypto
        # backend, which has separate round-trip and tampering tests.
        from cryptography.fernet import Fernet
        cipher = Fernet(Fernet.generate_key())
        def crypt(data, decrypt=False):
            return cipher.decrypt(data) if decrypt else cipher.encrypt(data)
        with patch('fivek.account.protect', crypt):
            save_connection(self.config, {'email':'first@example.com', 'password':'example-password'})
            payload = (self.config.data_dir / 'garmin-credentials.bin').read_bytes()
            self.assertNotIn(b'example-password', payload)
            first = auth_from_store(self.config)
            self.assertEqual(first.email, 'first@example.com')
            self.assertEqual(first._password, 'example-password')
            from fivek.webapp import LocalServer
            server = LocalServer(self.config, 0)
            try:
                self.assertEqual(server.app.auth_factory().email, 'first@example.com')
            finally:
                server.server_close()
            save_connection(self.config, {'email':'second@example.com', 'password':'different-password'})
            second = auth_from_store(self.config)
            self.assertNotEqual(first.token_store, second.token_store)

    def test_missing_connection_prompts_dashboard_setup(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(GarminAuthenticationError, 'Settings'):
                auth_from_store(self.config)

    def test_invalid_credentials_do_not_create_a_connection(self):
        for body in [{'email':'invalid','password':'x'}, {'email':'a@example.com','password':''}]:
            with self.assertRaises(ValueError):
                save_connection(self.config, body)
        self.assertFalse((self.config.data_dir / 'garmin-credentials.bin').exists())
