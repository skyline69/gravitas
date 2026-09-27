"""Mapping Qt's transport medium onto a sample bucket."""

import pytest
from PySide6.QtNetwork import QNetworkInformation

from gravitas.domain.models import TRANSPORT_BUCKETS
from gravitas.infrastructure.network import transport


class FakeInfo:
    def __init__(self, medium, supports: bool = True) -> None:
        self._medium = medium
        self._supports = supports

    def supports(self, feature) -> bool:
        return self._supports

    def transportMedium(self):
        return self._medium


@pytest.fixture(autouse=True)
def _reset_backend_cache(monkeypatch: pytest.MonkeyPatch):
    # The loaded/not-loaded outcome is cached for the process; tests must not
    # inherit each other's answer.
    monkeypatch.setattr(transport, "_loaded", None)


def _use(monkeypatch: pytest.MonkeyPatch, info: object | None) -> None:
    monkeypatch.setattr(transport, "_backend", lambda: info)


@pytest.mark.parametrize(
    ("medium", "bucket"),
    [
        (QNetworkInformation.TransportMedium.Ethernet, "ethernet"),
        (QNetworkInformation.TransportMedium.WiFi, "wifi"),
        (QNetworkInformation.TransportMedium.Cellular, "cellular"),
        # Tethering over Bluetooth is a phone's connection either way.
        (QNetworkInformation.TransportMedium.Bluetooth, "cellular"),
        (QNetworkInformation.TransportMedium.Unknown, "unknown"),
    ],
)
def test_media_map_to_buckets(monkeypatch: pytest.MonkeyPatch, medium, bucket) -> None:
    _use(monkeypatch, FakeInfo(medium))
    assert transport.current_bucket() == bucket


def test_no_backend_is_its_own_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, None)
    assert transport.current_bucket() == "unknown"


def test_backend_without_the_feature_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(
        monkeypatch,
        FakeInfo(QNetworkInformation.TransportMedium.WiFi, supports=False),
    )
    assert transport.current_bucket() == "unknown"


def test_a_backend_that_throws_does_not_take_the_app_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Exploding:
        def supports(self, feature) -> bool:
            raise RuntimeError("backend gone")

    _use(monkeypatch, Exploding())
    assert transport.current_bucket() == "unknown"


def test_every_bucket_it_can_return_is_a_known_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    for medium in QNetworkInformation.TransportMedium.__members__.values():
        _use(monkeypatch, FakeInfo(medium))
        assert transport.current_bucket() in TRANSPORT_BUCKETS


def test_the_real_backend_answers_a_known_bucket() -> None:
    # No fake: whatever this machine has (or has not) must still be a bucket.
    assert transport.current_bucket() in TRANSPORT_BUCKETS
