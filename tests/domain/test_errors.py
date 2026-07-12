import pytest

from gravitas.domain.errors import (
    AddonUnreachable,
    GravitasError,
    InvalidManifest,
    InvalidResponse,
    NoStreams,
    PlaybackFailed,
)


@pytest.mark.parametrize(
    "exc",
    [AddonUnreachable, InvalidManifest, InvalidResponse, NoStreams, PlaybackFailed],
)
def test_all_errors_subclass_base(exc: type[GravitasError]) -> None:
    instance = exc("boom")
    assert isinstance(instance, GravitasError)
    assert str(instance) == "boom"
