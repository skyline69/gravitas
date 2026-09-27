"""LinkResolver over httpx: walk a stream URL's redirects without downloading.

Measured on a real AIOStreams link (TorBox behind it): the addon's playback
endpoint took 1330 ms to answer with its redirect -- it asks the debrid
service for the link -- and the CDN 782 ms more for its first byte. mpv pays
both after the click. Asking once while the viewer is still reading the list
moves the first of them out of the wait.

Each hop is a GET for one byte (`Range: bytes=0-0`), not a HEAD: a signing
endpoint is only obliged to answer the request a player makes, and several
answer HEAD differently or not at all. The body is never read.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

import httpx

from gravitas.logging_setup import abbreviate_url

_log = logging.getLogger(__name__)

# A signing endpoint redirects once, a CDN sometimes once more; past this it
# is a loop, and not ours to follow.
_MAX_HOPS = 5
# Long enough for a debrid lookup (1.3s measured), short enough that a dead
# endpoint cannot keep a request open for the life of the page.
_TIMEOUT_S = 8.0


class HttpLinkResolver:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def resolve(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> str | None:
        if not url.startswith(("http://", "https://")):
            return None
        started = time.monotonic()
        current = url
        request_headers = {**dict(headers), "Range": "bytes=0-0"}
        try:
            for _ in range(_MAX_HOPS):
                async with self._client.stream(
                    "GET",
                    current,
                    headers=request_headers,
                    follow_redirects=False,
                    timeout=_TIMEOUT_S,
                ) as response:
                    if response.is_redirect and "location" in response.headers:
                        current = str(response.url.join(response.headers["location"]))
                        continue
                    if response.status_code not in (200, 206):
                        return None
                    break
            else:
                return None
        except httpx.HTTPError as exc:
            _log.debug("could not resolve %s ahead of playback: %s", abbreviate_url(url), exc)
            return None
        if current == url:
            return None
        _log.info(
            "resolved %s ahead of playback in %.0f ms",
            abbreviate_url(url),
            (time.monotonic() - started) * 1000,
        )
        return current
