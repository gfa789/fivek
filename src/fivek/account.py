"""Private Garmin connection storage for dashboard-based onboarding."""
from pathlib import Path
import json
import os
import uuid
import hashlib

from garmin_api import GarminAuth, GarminAuthenticationError
from .calendar_sync import protect, CalendarError


def save_connection(config, body):
    email, password = body.get('email'), body.get('password')
    if not isinstance(email, str) or '@' not in email or not 3 <= len(email.strip()) <= 254:
        raise ValueError('Enter your Garmin account email')
    if not isinstance(password, str) or not 1 <= len(password) <= 4096:
        raise ValueError('Enter your Garmin password')
    config.data_dir.mkdir(parents=True, exist_ok=True)
    path = config.data_dir / 'garmin-credentials.bin'
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        encrypted = protect(json.dumps({'email': email.strip(), 'password': password}).encode())
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
            output.write(encrypted)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def auth_from_store(config):
    path = config.data_dir / 'garmin-credentials.bin'
    if not path.exists():
        if os.getenv('GARMIN_EMAIL') and os.getenv('GARMIN_PASSWORD'):
            return GarminAuth.from_environment()
        raise GarminAuthenticationError('Connect your Garmin account in Settings before refreshing.')
    try:
        values = json.loads(protect(path.read_bytes(), decrypt=True))
        token_root = Path(os.getenv('GARMIN_TOKEN_STORE', str(config.data_dir / 'garmin_tokens')))
        account = hashlib.sha256(values['email'].strip().lower().encode()).hexdigest()[:24]
        return GarminAuth(values['email'], values['password'], token_store=token_root / account)
    except (CalendarError, OSError, ValueError, KeyError):
        raise GarminAuthenticationError('Your saved Garmin connection could not be opened. Reconnect in Settings.') from None
