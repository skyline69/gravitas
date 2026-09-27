import httpx
import respx

from gravitas.infrastructure.network.link_resolver import HttpLinkResolver

_ADDON = "https://addon.example/playback/abc"
_CDN = "https://cdn.example/file.mkv"


@respx.mock
async def test_follows_the_addon_redirect_to_the_cdn() -> None:
    respx.get(_ADDON).mock(return_value=httpx.Response(307, headers={"location": _CDN}))
    cdn = respx.get(_CDN).mock(
        return_value=httpx.Response(206, headers={"content-range": "bytes 0-0/100"}, content=b"x")
    )
    async with httpx.AsyncClient() as http:
        assert await HttpLinkResolver(http).resolve(_ADDON, (("Referer", "r"),)) == _CDN
    # One byte asked for, and the stream's own headers carried along.
    request = cdn.calls[0].request
    assert request.headers["range"] == "bytes=0-0"
    assert request.headers["referer"] == "r"


@respx.mock
async def test_a_link_without_a_redirect_has_nothing_to_gain() -> None:
    respx.get(_CDN).mock(return_value=httpx.Response(206, content=b"x"))
    async with httpx.AsyncClient() as http:
        assert await HttpLinkResolver(http).resolve(_CDN) is None


@respx.mock
async def test_a_failing_endpoint_resolves_to_nothing() -> None:
    respx.get(_ADDON).mock(return_value=httpx.Response(307, headers={"location": _CDN}))
    respx.get(_CDN).mock(return_value=httpx.Response(403))
    async with httpx.AsyncClient() as http:
        assert await HttpLinkResolver(http).resolve(_ADDON) is None


@respx.mock
async def test_an_unreachable_host_never_raises() -> None:
    respx.get(_ADDON).mock(side_effect=httpx.ConnectError("refused"))
    async with httpx.AsyncClient() as http:
        assert await HttpLinkResolver(http).resolve(_ADDON) is None


@respx.mock
async def test_a_redirect_loop_is_not_followed_forever() -> None:
    respx.get(_ADDON).mock(return_value=httpx.Response(302, headers={"location": _ADDON}))
    async with httpx.AsyncClient() as http:
        assert await HttpLinkResolver(http).resolve(_ADDON) is None


async def test_non_http_links_are_left_alone() -> None:
    async with httpx.AsyncClient() as http:
        assert await HttpLinkResolver(http).resolve("ytdl://abc") is None
