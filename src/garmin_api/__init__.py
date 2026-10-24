"""Reusable authentication and authenticated transport for Garmin Connect."""

from .client import (
    GarminAPI,
    GarminAuth,
    GarminAuthenticationError,
    GarminConnectionError,
    GarminError,
    GarminMFARequired,
)

__all__ = [
    "GarminAPI",
    "GarminAuth",
    "GarminAuthenticationError",
    "GarminConnectionError",
    "GarminError",
    "GarminMFARequired",
]
