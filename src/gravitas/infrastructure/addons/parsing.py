"""Pure functions mapping Stremio addon JSON to domain models. No I/O."""

from __future__ import annotations

import logging
import re
from typing import Any, get_args
from urllib.parse import quote

from gravitas.domain.errors import InvalidManifest, InvalidResponse
from gravitas.domain.models import (
    AddonBehaviorHints,
    AddonManifest,
    CatalogRef,
    ExtraSpec,
    MediaItem,
    MediaType,
    MetaDetail,
    PosterShape,
    ResourceSpec,
    Stream,
    Video,
)

_log = logging.getLogger(__name__)

_VALID_TYPES: frozenset[str] = frozenset(get_args(MediaType))
_VALID_POSTER_SHAPES: frozenset[str] = frozenset(get_args(PosterShape))


def _poster_shape(raw: Any) -> PosterShape:
    """`posterShape` if the addon named a known one, else the protocol default."""
    if isinstance(raw, str) and raw in _VALID_POSTER_SHAPES:
        return raw  # type: ignore[return-value]
    return "poster"


def _parse_extras(raw: dict[str, Any]) -> tuple[ExtraSpec, ...]:
    """Catalog `extra`, in either the modern or the legacy shape.

    Modern: [{"name": "genre", "isRequired": true, "options": [...],
    "optionsLimit": 1}]. Legacy: parallel extraSupported/extraRequired/genres
    arrays. Addons in the wild still ship the legacy form.
    """
    extra = raw.get("extra")
    if isinstance(extra, list):
        specs: list[ExtraSpec] = []
        for entry in extra:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            limit = entry.get("optionsLimit")
            specs.append(
                ExtraSpec(
                    name=name,
                    is_required=bool(entry.get("isRequired", False)),
                    options=tuple(str(o) for o in entry.get("options", []) or ()),
                    options_limit=limit if isinstance(limit, int) and limit > 0 else 1,
                )
            )
        return tuple(specs)
    supported = raw.get("extraSupported")
    if isinstance(supported, list):
        required = raw.get("extraRequired")
        required_names = {str(r) for r in required} if isinstance(required, list) else set()
        genres = tuple(str(g) for g in raw.get("genres", []) or ())
        return tuple(
            ExtraSpec(
                name=str(name),
                is_required=str(name) in required_names,
                options=genres if name == "genre" else (),
            )
            for name in supported
        )
    return ()


def _require(data: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in data:
        raise InvalidManifest(f"missing '{key}' in {ctx}")
    return data[key]


def _parse_resources(raw: Any) -> tuple[ResourceSpec, ...]:
    # Stremio resources are either short strings ("stream") or full objects
    # ({"name": "stream", "types": [...], "idPrefixes": [...]}). The object form
    # narrows what the addon will answer for; dropping it means calling addons
    # that can only 404.
    specs: list[ResourceSpec] = []
    if isinstance(raw, list):
        for r in raw:
            if isinstance(r, str):
                specs.append(ResourceSpec(name=r))
            elif isinstance(r, dict) and isinstance(r.get("name"), str):
                specs.append(
                    ResourceSpec(
                        name=r["name"],
                        types=_str_tuple(r.get("types")),
                        id_prefixes=_str_tuple(r.get("idPrefixes")),
                    )
                )
    return tuple(specs)


def _str_or_none(v: Any) -> str | None:
    if isinstance(v, (str, int, float)):
        s = str(v)
        return s if s else None
    return None


def _str_tuple(v: Any) -> tuple[str, ...]:
    if isinstance(v, list):
        return tuple(x for x in v if isinstance(x, str))
    return ()


def _parse_behavior_hints(raw: Any) -> AddonBehaviorHints:
    if not isinstance(raw, dict):
        return AddonBehaviorHints()
    return AddonBehaviorHints(
        adult=bool(raw.get("adult", False)),
        p2p=bool(raw.get("p2p", False)),
        configurable=bool(raw.get("configurable", False)),
        configuration_required=bool(raw.get("configurationRequired", False)),
    )


def parse_manifest(data: dict[str, Any], base_url: str) -> AddonManifest:
    manifest_id = _require(data, "id", "manifest")
    name = _require(data, "name", "manifest")
    catalogs: list[CatalogRef] = []
    for raw in data.get("catalogs", []):
        c_type = raw.get("type")
        if c_type not in _VALID_TYPES:
            # "channel" and "tv" catalogs are dropped on purpose: supporting
            # them would make MediaType four-way and Gravitas an IPTV client.
            # A product decision (see MediaType), not an oversight -- an addon
            # serving only those installs fine and shows nothing, so say so.
            _log.info(
                "addon %s: ignoring %r catalog %r (only movie/series are supported)",
                manifest_id,
                c_type,
                raw.get("id", ""),
            )
            continue
        catalogs.append(
            CatalogRef(
                type=c_type,
                id=raw.get("id", ""),
                name=raw.get("name", raw.get("id", "")),
                extra=_parse_extras(raw),
            )
        )
    return AddonManifest(
        id=str(manifest_id),
        name=str(name),
        version=str(data.get("version", "0.0.0")),
        resources=_parse_resources(data.get("resources", [])),
        types=tuple(str(t) for t in data.get("types", [])),
        catalogs=tuple(catalogs),
        base_url=base_url if base_url.endswith("/") else base_url + "/",
        id_prefixes=_str_tuple(data.get("idPrefixes")),
        description=_str_or_none(data.get("description")),
        logo=_str_or_none(data.get("logo")),
        behavior_hints=_parse_behavior_hints(data.get("behaviorHints")),
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
                poster_shape=_poster_shape(raw.get("posterShape")),
            )
        )
    return items


def _parse_video(raw: dict[str, Any]) -> Video:
    # Cinemeta uses "overview"; some addons use "description". "released" is an
    # ISO timestamp — keep the date part only for display.
    released = raw.get("released")
    return Video(
        id=str(raw.get("id", "")),
        title=str(raw.get("title", raw.get("name", ""))),
        season=raw.get("season"),
        episode=raw.get("episode"),
        thumbnail=_str_or_none(raw.get("thumbnail")),
        overview=_str_or_none(raw.get("overview")) or _str_or_none(raw.get("description")),
        released=released[:10] if isinstance(released, str) and released else None,
    )


def _link_names(links: Any, category: str) -> tuple[str, ...]:
    """Names of a `links` category, e.g. "Cast" -> ("Keanu Reeves", ...).

    `links` is the modern replacement for the flat genres/cast/director arrays.
    Cinemeta sends both, but addons emitting only links would otherwise show
    empty fields.
    """
    if not isinstance(links, list):
        return ()
    return tuple(
        str(link["name"])
        for link in links
        if isinstance(link, dict) and link.get("category") == category and "name" in link
    )


def _trailer_yt_id(meta: dict[str, Any]) -> str | None:
    # trailerStreams is [{"title": ..., "ytId": ...}]; the legacy `trailers` is
    # [{"source": <ytId>, "type": "Trailer"}].
    streams = meta.get("trailerStreams")
    if isinstance(streams, list):
        for entry in streams:
            if isinstance(entry, dict):
                yt_id = _str_or_none(entry.get("ytId"))
                if yt_id:
                    return yt_id
    trailers = meta.get("trailers")
    if isinstance(trailers, list):
        for entry in trailers:
            if isinstance(entry, dict):
                source = _str_or_none(entry.get("source"))
                if source:
                    return source
    return None


def parse_meta(data: dict[str, Any]) -> MetaDetail:
    meta = data.get("meta")
    if not isinstance(meta, dict):
        raise InvalidResponse("meta response missing 'meta' object")
    m_type = meta.get("type")
    if m_type not in _VALID_TYPES:
        raise InvalidResponse(f"unsupported meta type: {m_type!r}")
    videos = tuple(_parse_video(v) for v in meta.get("videos", []) if isinstance(v, dict))
    links = meta.get("links")
    hints = meta.get("behaviorHints")
    default_video_id = (
        _str_or_none(hints.get("defaultVideoId")) if isinstance(hints, dict) else None
    )
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
        # Legacy arrays win when present -- they are what Cinemeta and most
        # addons still send; links is the documented fallback, not an override.
        genres=_str_tuple(meta.get("genres")) or _link_names(links, "Genres"),
        cast=_str_tuple(meta.get("cast")) or _link_names(links, "Cast"),
        directors=_str_tuple(meta.get("director")) or _link_names(links, "Directors"),
        writers=_str_tuple(meta.get("writer")) or _link_names(links, "Writers"),
        poster_shape=_poster_shape(meta.get("posterShape")),
        trailer_yt_id=_trailer_yt_id(meta),
        default_video_id=default_video_id,
    )


# Zero-width joiners, variation selectors, and regional-indicator (flag)
# pairs: no font in a typical fallback chain covers them, so Qt's FreeType
# engine spams "load glyph failed ... glyph=65535" for every frame they are
# on screen. Torrent addons pack all three into stream titles.
_UNRENDERABLE = re.compile("[\u200d\ufe0f\U0001f1e6-\U0001f1ff]")


def _clean_stream_text(value: str) -> str:
    """Single-line, renderable display text for addon-supplied stream labels."""
    return " ".join(_UNRENDERABLE.sub("", value).split())


def _proxy_headers(raw: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """behaviorHints.proxyHeaders.request: headers the addon's URL needs.

    Some addons only serve their stream with a specific Referer/User-Agent and
    403 without it. Response headers are for a proxying client to forward; the
    player only needs the request side.
    """
    hints = raw.get("behaviorHints")
    if not isinstance(hints, dict):
        return ()
    proxy = hints.get("proxyHeaders")
    if not isinstance(proxy, dict):
        return ()
    request = proxy.get("request")
    if not isinstance(request, dict):
        return ()
    return tuple(
        (str(key), str(value))
        for key, value in request.items()
        if isinstance(key, str) and isinstance(value, (str, int, float))
    )


def parse_streams(data: dict[str, Any]) -> list[Stream]:
    raw_streams = data.get("streams")
    if not isinstance(raw_streams, list):
        raise InvalidResponse("stream response missing 'streams' list")
    streams: list[Stream] = []
    for raw in raw_streams:
        # The SDK deprecates `title` in favour of `description`; addons emitting
        # only the latter would otherwise render as a bare name, losing the
        # quality/size line.
        detail = raw.get("description") or raw.get("title") or raw.get("name", "")
        streams.append(
            Stream(
                name=_clean_stream_text(str(raw.get("name", ""))),
                title=_clean_stream_text(str(detail)),
                url=raw.get("url"),
                info_hash=raw.get("infoHash"),
                file_idx=raw.get("fileIdx"),
                yt_id=_str_or_none(raw.get("ytId")),
                external_url=_str_or_none(raw.get("externalUrl")),
                proxy_headers=_proxy_headers(raw),
            )
        )
    return streams


def catalog_path(ref: CatalogRef) -> str:
    return f"catalog/{ref.type}/{ref.id}.json"


def catalog_path_extra(
    ref: CatalogRef, genre: str | None, skip: int, search: str | None = None
) -> str:
    # A catalog declaring genre as isRequired must never be asked without one:
    # lenient addons (Cinemeta) answer anyway, strict ones reject the request.
    # The addon's own first option is the only default we can justify.
    if genre is None and ref.requires_genre and ref.genres:
        genre = ref.genres[0]
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
