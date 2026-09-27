"""A stremio:// URL is untrusted input: it arrives from any web page the user
visits, and what it names gets installed as a data source. Parsing is total --
anything that is not an addon install link is refused, loudly."""

from __future__ import annotations

import pytest

from gravitas.application.deep_link import parse_deep_link
from gravitas.domain.errors import UnsupportedLink


def test_parses_a_plain_install_link() -> None:
    assert (
        parse_deep_link("stremio://ptube.ers.pw/manifest.json")
        == "https://ptube.ers.pw/manifest.json"
    )


def test_preserves_the_path_of_a_configured_addon() -> None:
    # Configured addons carry their settings in the path; dropping it installs
    # a different (unconfigured) addon.
    assert (
        parse_deep_link("stremio://addon.example/abc123/manifest.json")
        == "https://addon.example/abc123/manifest.json"
    )


def test_preserves_port_and_query() -> None:
    assert (
        parse_deep_link("stremio://addon.example:8080/manifest.json?token=x")
        == "https://addon.example:8080/manifest.json?token=x"
    )


def test_accepts_uppercase_scheme() -> None:
    assert (
        parse_deep_link("STREMIO://addon.example/manifest.json")
        == "https://addon.example/manifest.json"
    )


def test_tolerates_surrounding_whitespace() -> None:
    assert (
        parse_deep_link("  stremio://addon.example/manifest.json\n")
        == "https://addon.example/manifest.json"
    )


@pytest.mark.parametrize(
    "raw",
    [
        # Deep links Gravitas deliberately does not implement yet. Refusing
        # them explicitly keeps one obvious place to add them.
        "stremio:///detail/movie/tt0133093",
        "stremio:///search?search=dune",
        "stremio://detail/movie/tt0133093",
    ],
)
def test_rejects_non_install_stremio_links(raw: str) -> None:
    with pytest.raises(UnsupportedLink):
        parse_deep_link(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not a url",
        # Another scheme must never be rewritten to https and fetched.
        "https://addon.example/manifest.json",
        "http://addon.example/manifest.json",
        "javascript:alert(1)",
        "file:///etc/passwd",
        # The scheme appearing later in the string must not count as a match.
        "https://evil.example/stremio://addon.example/manifest.json",
        # Right scheme, wrong resource.
        "stremio://addon.example/catalog/movie/top.json",
        "stremio://addon.example/",
        "stremio://addon.example",
        # A host is required: this names no addon to install.
        "stremio:///manifest.json",
    ],
)
def test_rejects_anything_that_is_not_an_install_link(raw: str) -> None:
    with pytest.raises(UnsupportedLink):
        parse_deep_link(raw)


def test_error_message_names_the_link() -> None:
    # The toast has to tell the user what was refused.
    with pytest.raises(UnsupportedLink, match="detail"):
        parse_deep_link("stremio:///detail/movie/tt1")
