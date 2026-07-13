"""Typed domain errors. Every failure the domain raises subclasses GravitasError."""


class GravitasError(Exception):
    """Base class for all Gravitas domain errors."""


class AddonUnreachable(GravitasError):
    """An addon endpoint could not be reached (network/transport failure)."""


class InvalidManifest(GravitasError):
    """An addon manifest was missing required fields or malformed."""


class InvalidResponse(GravitasError):
    """An addon resource response was malformed."""


class NoStreams(GravitasError):
    """No playable streams were returned for an item."""


class AddonRemovalError(GravitasError):
    """An addon could not be removed (protected default, or not installed)."""


class PlaybackFailed(GravitasError):
    """The media player failed to start or continue playback."""


class TmdbUnavailable(GravitasError):
    """TMDB could not resolve an external id (no API key, or the lookup failed)."""
