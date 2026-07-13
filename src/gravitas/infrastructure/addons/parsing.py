"""Pure functions mapping Stremio addon JSON to domain models. No I/O."""

from __future__ import annotations

from typing import Any, get_args
from urllib.parse import quote

from gravitas.domain.errors import InvalidManifest, InvalidResponse
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
    Video,
)

_VALID_TYPES: frozenset[str] = frozenset(get_args(MediaType))


def _parse_catalog_extra(raw: dict[str, Any]) -> tuple[tuple[str, ...], bool, bool]:
    extra = raw.get("extra")
    if isinstance(extra, list):
        genres: tuple[str, ...] = ()
        supports_skip = False
        supports_search = False
        for entry in extra:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if name == "genre":
                genres = tuple(str(o) for o in entry.get("options", []))
            elif name == "skip":
                supports_skip = True
            elif name == "search":
                supports_search = True
        return genres, supports_skip, supports_search
    supported = raw.get("extraSupported")
    if isinstance(supported, list):
        genres = tuple(str(g) for g in raw.get("genres", [])) if "genre" in supported else ()
        return genres, "skip" in supported, "search" in supported
    return (), False, False


def _require(data: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in data:
        raise InvalidManifest(f"missing '{key}' in {ctx}")
    return data[key]


def _resource_names(raw: Any) -> tuple[str, ...]:
    # Stremio resources are either short strings ("stream") or full objects
    # ({"name": "stream", "types": [...], "idPrefixes": [...]}); collect names.
    names: list[str] = []
    if isinstance(raw, list):
        for r in raw:
            if isinstance(r, str):
                names.append(r)
            elif isinstance(r, dict) and isinstance(r.get("name"), str):
                names.append(r["name"])
    return tuple(names)


def _str_or_none(v: Any) -> str | None:
    if isinstance(v, (str, int, float)):
        s = str(v)
        return s if s else None
    return None


def _str_tuple(v: Any) -> tuple[str, ...]:
    if isinstance(v, list):
        return tuple(x for x in v if isinstance(x, str))
    return ()


def parse_manifest(data: dict[str, Any], base_url: str) -> AddonManifest:
    manifest_id = _require(data, "id", "manifest")
    name = _require(data, "name", "manifest")
    catalogs: list[CatalogRef] = []
    for raw in data.get("catalogs", []):
        c_type = raw.get("type")
        if c_type not in _VALID_TYPES:
            continue
        genres, supports_skip, supports_search = _parse_catalog_extra(raw)
        catalogs.append(
            CatalogRef(
                type=c_type,
                id=raw.get("id", ""),
                name=raw.get("name", raw.get("id", "")),
                genres=genres,
                supports_skip=supports_skip,
                supports_search=supports_search,
            )
        )
    return AddonManifest(
        id=str(manifest_id),
        name=str(name),
        version=str(data.get("version", "0.0.0")),
        resources=_resource_names(data.get("resources", [])),
        types=tuple(str(t) for t in data.get("types", [])),
        catalogs=tuple(catalogs),
        base_url=base_url if base_url.endswith("/") else base_url + "/",
    )


def parse_catalog(data: dict[str, Any]) -> list[MediaItem]:
    metas = data.get("metas")
    if not isinstance(metas, list):
        raise InvalidResponse("catalog response missing 'metas' list")
    items: list[MediaItem] = []
    for raw in metas:
        m_type = raw.get("type")
        if m_type not in _VALID_TYPES:
            continue
        release = raw.get("releaseInfo")
        year = release[:4] if isinstance(release, str) and release[:4].isdigit() else None
        rating = raw.get("imdbRating")
        items.append(
            MediaItem(
                id=str(raw.get("id", "")),
                type=m_type,
                name=str(raw.get("name", "")),
                poster=raw.get("poster"),
                year=year,
                imdb_rating=str(rating) if rating is not None else None,
            )
        )
    return items


def _parse_video(raw: dict[str, Any]) -> Video:
    return Video(
        id=str(raw.get("id", "")),
        title=str(raw.get("title", raw.get("name", ""))),
        season=raw.get("season"),
        episode=raw.get("episode"),
    )


def parse_meta(data: dict[str, Any]) -> MetaDetail:
    meta = data.get("meta")
    if not isinstance(meta, dict):
        raise InvalidResponse("meta response missing 'meta' object")
    m_type = meta.get("type")
    if m_type not in _VALID_TYPES:
        raise InvalidResponse(f"unsupported meta type: {m_type!r}")
    videos = tuple(_parse_video(v) for v in meta.get("videos", []) if isinstance(v, dict))
    return MetaDetail(
        id=str(meta.get("id", "")),
        type=m_type,
        name=str(meta.get("name", "")),
        description=meta.get("description"),
        poster=meta.get("poster"),
        background=meta.get("background"),
        videos=videos,
        logo=_str_or_none(meta.get("logo")),
        year=_str_or_none(meta.get("releaseInfo")) or _str_or_none(meta.get("year")),
        runtime=_str_or_none(meta.get("runtime")),
        imdb_rating=_str_or_none(meta.get("imdbRating")),
        genres=_str_tuple(meta.get("genres")),
        cast=_str_tuple(meta.get("cast")),
        directors=_str_tuple(meta.get("director")),
    )


def parse_streams(data: dict[str, Any]) -> list[Stream]:
    raw_streams = data.get("streams")
    if not isinstance(raw_streams, list):
        raise InvalidResponse("stream response missing 'streams' list")
    streams: list[Stream] = []
    for raw in raw_streams:
        streams.append(
            Stream(
                name=str(raw.get("name", "")),
                title=str(raw.get("title", raw.get("name", ""))),
                url=raw.get("url"),
                info_hash=raw.get("infoHash"),
                file_idx=raw.get("fileIdx"),
            )
        )
    return streams


def catalog_path(ref: CatalogRef) -> str:
    return f"catalog/{ref.type}/{ref.id}.json"


def catalog_path_extra(
    ref: CatalogRef, genre: str | None, skip: int, search: str | None = None
) -> str:
    parts: list[str] = []
    if genre:
        parts.append(f"genre={quote(genre, safe='')}")
    if skip:
        parts.append(f"skip={skip}")
    if search:
        parts.append(f"search={quote(search, safe='')}")
    if not parts:
        return catalog_path(ref)
    return f"catalog/{ref.type}/{ref.id}/{'&'.join(parts)}.json"


def meta_path(type: MediaType, id: str) -> str:
    return f"meta/{type}/{id}.json"


def stream_path(type: MediaType, id: str) -> str:
    return f"stream/{type}/{id}.json"
