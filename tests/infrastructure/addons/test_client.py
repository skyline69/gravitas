import httpx
import pytest
import respx

from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import CatalogRef
from gravitas.infrastructure.addons.client import AddonClient


@respx.mock
async def test_fetch_manifest_strips_suffix() -> None:
    respx.get("https://cin.strem.io/manifest.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c",
                "name": "Cinemeta",
                "version": "3.0",
                "types": ["movie"],
                "resources": ["catalog"],
                "catalogs": [],
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        manifest = await client.fetch_manifest("https://cin.strem.io/manifest.json")
    assert manifest.base_url == "https://cin.strem.io/"


@respx.mock
async def test_fetch_catalog_builds_path() -> None:
    respx.get("https://cin.strem.io/manifest.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c",
                "name": "C",
                "version": "1",
                "types": ["movie"],
                "resources": ["catalog"],
                "catalogs": [{"type": "movie", "id": "top", "name": "T"}],
            },
        )
    )
    respx.get("https://cin.strem.io/catalog/movie/top.json").mock(
        return_value=httpx.Response(
            200, json={"metas": [{"id": "tt1", "type": "movie", "name": "A", "poster": "p"}]}
        )
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        manifest = await client.fetch_manifest("https://cin.strem.io/manifest.json")
        items = await client.fetch_catalog(manifest, CatalogRef(type="movie", id="top", name="T"))
    assert items[0].id == "tt1"


@respx.mock
async def test_transport_error_becomes_addon_unreachable() -> None:
    respx.get("https://down/manifest.json").mock(side_effect=httpx.ConnectError("nope"))
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with pytest.raises(AddonUnreachable):
            await client.fetch_manifest("https://down/manifest.json")
