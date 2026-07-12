from pathlib import Path

import httpx
import respx

from gravitas.infrastructure.cache.disk_cache import DiskCache


@respx.mock
async def test_fetches_then_caches(tmp_path: Path) -> None:
    route = respx.get("http://img/1.jpg").mock(
        return_value=httpx.Response(200, content=b"JPEGBYTES")
    )
    async with httpx.AsyncClient() as http:
        cache = DiskCache(http, root=tmp_path)
        first = await cache.get_or_fetch("http://img/1.jpg")
        second = await cache.get_or_fetch("http://img/1.jpg")
    assert first == b"JPEGBYTES"
    assert second == b"JPEGBYTES"
    assert route.call_count == 1  # second call served from disk
