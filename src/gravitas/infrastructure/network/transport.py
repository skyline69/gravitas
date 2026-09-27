"""Which kind of link the app is currently on, as a bucket name.

Bandwidth samples are kept per bucket, so that a week of ethernet history
cannot answer a question asked over a phone hotspot. Qt already knows this
(QNetworkInformation), and it is the only source here -- no SSID, no interface
name, no address is read: the bucket is a category, not an identity.

Every failure path lands on "unknown", which is a real bucket of its own. A
platform with no backend therefore behaves like a machine with one connection,
which is what such a machine usually is.
"""

from __future__ import annotations

import logging

from PySide6.QtNetwork import QNetworkInformation

_log = logging.getLogger(__name__)

_MEDIUM_BUCKETS = {
    QNetworkInformation.TransportMedium.Ethernet: "ethernet",
    QNetworkInformation.TransportMedium.WiFi: "wifi",
    QNetworkInformation.TransportMedium.Cellular: "cellular",
    # Bluetooth tethering is a phone's connection wearing another hat, and
    # sorting it with cellular is closer to the truth than calling it unknown.
    QNetworkInformation.TransportMedium.Bluetooth: "cellular",
}

_loaded: bool | None = None


def _backend() -> QNetworkInformation | None:
    """The shared QNetworkInformation instance, loading its backend once.

    loadDefaultBackend() is idempotent but not free, and on a platform without
    one it fails every time; the outcome is cached so a cold Sources open does
    not keep retrying a backend that is not there.
    """
    global _loaded
    if _loaded is None:
        try:
            _loaded = bool(QNetworkInformation.loadDefaultBackend())
        except Exception:  # pragma: no cover - defensive: Qt build without the module
            _loaded = False
        if not _loaded:
            _log.info("no QNetworkInformation backend; connection samples share one bucket")
    if not _loaded:
        return None
    return QNetworkInformation.instance()


def current_bucket() -> str:
    """One of TRANSPORT_BUCKETS, describing the link in use right now."""
    info = _backend()
    if info is None:
        return "unknown"
    try:
        if not info.supports(QNetworkInformation.Feature.TransportMedium):
            return "unknown"
        return _MEDIUM_BUCKETS.get(info.transportMedium(), "unknown")
    except Exception:  # pragma: no cover - defensive: backend disappearing mid-run
        return "unknown"
