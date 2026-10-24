"""Garmin Connect authentication with an intentionally narrow API transport."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote


class GarminError(RuntimeError):
    """Base error whose message is safe to show to an application user."""


class GarminAuthenticationError(GarminError):
    """Garmin rejected a login or the stored session is no longer valid."""


class GarminConnectionError(GarminError):
    """Garmin could not be reached or did not complete an operation."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GarminMFARequired(GarminError):
    """A code was sent and ``GarminAuth.complete_mfa`` can resume login."""

    def __init__(self) -> None:
        super().__init__("Garmin requires a verification code. Submit it with complete_mfa().")


class GarminAPI:
    """Authenticated, path-only access to Garmin Connect's API transport.

    Instances come from :meth:`GarminAuth.login` or
    :meth:`GarminAuth.complete_mfa`. The underlying library handles token
    refresh; every call stays on its configured Garmin Connect API host.
    """

    def __init__(self, client: Any, lock: threading.RLock) -> None:
        self._client = client
        self._lock = lock

    def request(
        self,
        method: str,
        path: str,
        *,
        raw: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Make an authenticated request and return JSON, or a raw response.

        ``path`` must be a relative connectapi path such as
        ``/wellness-service/wellness/dailySleepData/2026-10-03``. Supply query
        values through ``params``. This prevents callers from redirecting the
        authenticated transport to an arbitrary host.
        """
        normalized_path = _validate_api_path(path)
        normalized_method = _validate_method(method)
        try:
            with self._lock:
                response = self._client.client.request(
                    normalized_method, "connectapi", normalized_path, **kwargs
                )
            return response if raw else response.json()
        except GarminError:
            raise
        except Exception as exc:
            raise _safe_request_error(exc) from exc

    def get(self, path: str, *, raw: bool = False, **kwargs: Any) -> Any:
        return self.request("GET", path, raw=raw, **kwargs)

    def post(self, path: str, *, raw: bool = False, **kwargs: Any) -> Any:
        return self.request("POST", path, raw=raw, **kwargs)

    def put(self, path: str, *, raw: bool = False, **kwargs: Any) -> Any:
        return self.request("PUT", path, raw=raw, **kwargs)

    def delete(self, path: str, *, raw: bool = False, **kwargs: Any) -> Any:
        return self.request("DELETE", path, raw=raw, **kwargs)

    def fetch(self, resource: str, *args: Any, **kwargs: Any) -> Any:
        """Use vetted read-only library adapters without exposing the session."""
        allowed = {
            "get_activities", "get_activity", "get_activity_splits",
            "get_activity_details", "get_stats", "get_sleep_data", "get_hrv_data",
            "get_training_readiness", "get_training_status", "get_max_metrics",
            "get_body_battery", "get_stress_data", "get_respiration_data",
            "get_spo2_data", "get_user_profile", "get_devices", "get_heart_rates",
            "get_personal_record",
        }
        if resource not in allowed:
            raise ValueError("Unsupported Garmin read resource")
        try:
            with self._lock:
                adapter = getattr(self._client, resource, None)
                if adapter is None:
                    raise GarminConnectionError("This Garmin adapter is unavailable.", status_code=501)
                return adapter(*args, **kwargs)
        except GarminError:
            raise
        except Exception as exc:
            raise _safe_request_error(exc) from exc


class GarminAuth:
    """Own one Garmin account session, token store, and pending MFA challenge.

    The object is deliberately per-account. Retain it between ``login`` and
    ``complete_mfa`` when Garmin requests MFA; do not share one instance across
    accounts.
    """

    def __init__(
        self,
        email: str,
        password: str,
        *,
        token_store: str | Path = "data/garmin_tokens",
        is_cn: bool = False,
    ) -> None:
        if not isinstance(email, str) or not email.strip():
            raise ValueError("email must be a non-empty string")
        if not isinstance(password, str) or not password:
            raise ValueError("password must be a non-empty string")
        if not isinstance(is_cn, bool):
            raise ValueError("is_cn must be a boolean")
        self.email = email
        self._password = password
        self.token_store = Path(token_store).expanduser()
        self.is_cn = is_cn
        self._lock = threading.RLock()
        self._pending_client: Any | None = None
        self._api: GarminAPI | None = None

    @classmethod
    def from_environment(cls, *, is_cn: bool = False) -> "GarminAuth":
        """Build an account session from ``GARMIN_EMAIL`` and ``GARMIN_PASSWORD``."""
        email = os.getenv("GARMIN_EMAIL")
        password = os.getenv("GARMIN_PASSWORD")
        if not email or not password:
            raise GarminAuthenticationError(
                "Set GARMIN_EMAIL and GARMIN_PASSWORD before connecting to Garmin."
            )
        return cls(
            email,
            password,
            token_store=os.getenv("GARMIN_TOKEN_STORE", "data/garmin_tokens"),
            is_cn=is_cn,
        )

    @property
    def api(self) -> GarminAPI:
        """Return the current authenticated API client."""
        if self._api is None:
            raise GarminAuthenticationError("Log in to Garmin before making API calls.")
        return self._api

    @property
    def mfa_pending(self) -> bool:
        return self._pending_client is not None

    def login(self) -> GarminAPI:
        """Use saved tokens or credentials; raise ``GarminMFARequired`` if needed."""
        with self._lock:
            if self._pending_client is not None:
                raise GarminMFARequired()
            client = self._new_client()
            used_cache = _has_cached_tokens(self.token_store)
            try:
                status, _ = client.login(tokenstore=str(self.token_store))
            except Exception as exc:
                # In return-on-MFA mode, garminconnect does not always retry
                # credential login after a rejected cached token. Do so once.
                if not used_cache or not _is_authentication_error(exc):
                    raise _safe_auth_error(exc) from exc
                try:
                    status, _ = self._login_with_credentials(client)
                except Exception as fresh_exc:
                    raise _safe_auth_error(fresh_exc) from fresh_exc
            if status == "needs_mfa":
                # The library retains the SSO state; password need not remain
                # attached to that client while the caller collects a code.
                client.password = None
                self._pending_client = client
                raise GarminMFARequired()
            if status is not None:
                raise GarminConnectionError("Garmin sign-in returned an unexpected status.")
            try:
                self._initialize_session(client)
            except Exception as exc:
                # A cached session may load successfully but fail only when its
                # profile is fetched. Retry the full credential flow once.
                if not used_cache or not _is_authentication_error(exc):
                    raise _safe_auth_error(exc) from exc
                client = self._new_client()
                try:
                    status, _ = self._login_with_credentials(client)
                except Exception as fresh_exc:
                    raise _safe_auth_error(fresh_exc) from fresh_exc
                if status == "needs_mfa":
                    client.password = None
                    self._pending_client = client
                    raise GarminMFARequired()
                if status is not None:
                    raise GarminConnectionError("Garmin sign-in returned an unexpected status.")
                try:
                    self._initialize_session(client)
                except Exception as fresh_exc:
                    raise _safe_auth_error(fresh_exc) from fresh_exc
            self._api = GarminAPI(client, self._lock)
            return self._api

    def complete_mfa(self, code: str) -> GarminAPI:
        """Resume an in-memory MFA challenge. Invalid codes can be retried."""
        if not isinstance(code, str) or not code.strip() or len(code.strip()) > 32:
            raise ValueError("code must be a non-empty verification code of at most 32 characters")
        with self._lock:
            client = self._pending_client
            if client is None:
                raise GarminAuthenticationError("Start a Garmin login before submitting a verification code.")
            try:
                client.resume_login({}, code.strip())
                self._initialize_session(client)
            except Exception as exc:
                if _is_invalid_mfa_code(exc):
                    raise GarminMFARequired() from exc
                self._pending_client = None
                raise _safe_auth_error(exc) from exc
            self._pending_client = None
            self._api = GarminAPI(client, self._lock)
            return self._api

    def _new_client(self) -> Any:
        try:
            from garminconnect import Garmin
        except ImportError as exc:
            raise GarminConnectionError(
                "Install the garminconnect dependency before connecting to Garmin."
            ) from exc
        return Garmin(self.email, self._password, is_cn=self.is_cn, return_on_mfa=True)

    def _login_with_credentials(self, client: Any) -> tuple[Any, Any]:
        # Do not pass the cache path here: this is explicitly the stale-cache
        # fallback. The verified session is persisted by _initialize_session.
        return client.login()

    def _initialize_session(self, client: Any) -> None:
        # return_on_mfa skips garminconnect's normal profile and token-writing
        # branch after a clean password login. Do both before exposing transport.
        if not getattr(client, "display_name", None):
            client._load_profile_and_settings()
        client.client._tokenstore_path = str(self.token_store)
        client.client.dump(str(self.token_store))


def _has_cached_tokens(token_store: Path) -> bool:
    token_file = token_store if token_store.suffix.casefold() == ".json" else token_store / "garmin_tokens.json"
    return token_file.is_file()


def _validate_api_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("path must be a relative Garmin Connect API path beginning with '/'")
    decoded = unquote(path)
    if (
        "//" in decoded
        or "?" in decoded
        or "#" in decoded
        or "\\" in decoded
        or any(segment.split(";", 1)[0] == ".." for segment in decoded.split("/"))
    ):
        raise ValueError("path must not contain a host, query string, fragment, or traversal segment")
    return path


def _validate_method(method: str) -> str:
    if not isinstance(method, str) or not method.strip():
        raise ValueError("method must be a non-empty HTTP method")
    normalized = method.upper()
    if not normalized.isalpha():
        raise ValueError("method must contain letters only")
    return normalized


def _http_status(exc: Exception) -> int | None:
    for source in (getattr(exc, "response", None), exc):
        value = getattr(source, "status_code", None)
        if type(value) is int and 400 <= value <= 599:
            return value
    # Older adapters wrap HTTP errors as text. Match complete status numbers,
    # so digits inside activity IDs do not turn a timeout into a login failure.
    match = re.search(r"\b(401|403|404|429)\b", str(exc))
    return int(match[1]) if match else None


def _is_authentication_error(exc: Exception) -> bool:
    name, detail = type(exc).__name__.lower(), str(exc).lower()
    return "authentication" in name or _http_status(exc) == 401 or "unauthorized" in detail or "invalid credential" in detail


def _is_invalid_mfa_code(exc: Exception) -> bool:
    detail = str(exc).lower()
    transient = ("connection error", "timeout", "socket", "http 429", "non-json")
    return "mfa verification failed" in detail and not any(marker in detail for marker in transient)


def _safe_auth_error(exc: Exception) -> GarminError:
    name, detail = type(exc).__name__.lower(), str(exc).lower()
    if "toomanyrequests" in name or _http_status(exc) == 429 or "too many" in detail:
        return GarminConnectionError("Garmin temporarily limited sign-in attempts. Wait a few minutes, then retry.", status_code=429)
    if _is_authentication_error(exc):
        return GarminAuthenticationError("Garmin rejected the sign-in. Check the account credentials and retry.")
    if "connection" in name or any(word in detail for word in ("connection", "timeout", "network", "socket", "dns")):
        return GarminConnectionError("Could not reach Garmin Connect. Check network access and retry.")
    return GarminConnectionError("Garmin sign-in did not complete. Check account access and retry.")


def _safe_request_error(exc: Exception) -> GarminError:
    detail, name, status = str(exc).lower(), type(exc).__name__.lower(), _http_status(exc)
    if _is_authentication_error(exc):
        return GarminAuthenticationError("Garmin rejected the authenticated request. Log in again and retry.")
    if status == 404 or (status is None and "notfound" in name):
        return GarminConnectionError("Garmin Connect could not find that API resource.", status_code=404)
    if status == 429 or (status is None and ("toomanyrequests" in name or "too many" in detail)):
        return GarminConnectionError("Garmin temporarily limited API requests. Wait a few minutes, then retry.", status_code=429)
    if status == 403:
        return GarminConnectionError("Garmin denied access to this data resource.", status_code=403)
    if any(word in detail for word in ("connection", "timeout", "network", "socket", "dns")):
        return GarminConnectionError("Could not reach Garmin Connect. Check network access and retry.", status_code=status)
    return GarminConnectionError("Garmin API request failed.", status_code=status)
