"""JSON-file SettingsStore adapter (the user config dir by default -- see paths.py)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from gravitas.domain import languages
from gravitas.domain.models import (
    PIP_WIDTH_MAX,
    PIP_WIDTH_MIN,
    TRANSPORT_BUCKETS,
    VIDEO_PLAYERS,
    ConnectionSample,
    PersistedSettings,
    SubtitleStyle,
    TrackLanguages,
    TraktAuth,
    VideoPlayer,
)
from gravitas.infrastructure.paths import config_dir

_log = logging.getLogger(__name__)

# How many "this source is unrenderable" verdicts are remembered. Each is a
# short string, and a user who has met 200 broken releases has a library
# problem this setting cannot solve.
_SIGNATURE_CAP = 200


def default_settings_path() -> Path:
    return config_dir() / "settings.json"


def _style_from(raw: object) -> SubtitleStyle:
    if not isinstance(raw, dict):
        return SubtitleStyle()
    defaults = SubtitleStyle()

    def _int(key: str, fallback: int) -> int:
        value = raw.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else fallback

    color = raw.get("color")
    return SubtitleStyle(
        font_size=_int("font_size", defaults.font_size),
        color=color if isinstance(color, str) and color.startswith("#") else defaults.color,
        border_size=_int("border_size", defaults.border_size),
        back_opacity=_int("back_opacity", defaults.back_opacity),
        bold=bool(raw.get("bold", defaults.bold)),
    )


def _track_languages_from(raw: object) -> TrackLanguages:
    """Unknown or malformed codes read as no preference: a file played in its
    own default language is the behaviour before this setting existed."""
    if not isinstance(raw, dict):
        return TrackLanguages()
    audio = raw.get("audio")
    subtitle = raw.get("subtitle")
    return TrackLanguages(
        audio=languages.normalized(audio) if isinstance(audio, str) else "",
        subtitle=(
            languages.normalized(subtitle, subtitles=True) if isinstance(subtitle, str) else ""
        ),
    )


def _trakt_auth_from(raw: object) -> TraktAuth | None:
    if not isinstance(raw, dict):
        return None
    access = raw.get("access_token")
    refresh = raw.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        return None
    expires = raw.get("expires_at")
    username = raw.get("username")
    return TraktAuth(
        access_token=access,
        refresh_token=refresh,
        expires_at=expires if isinstance(expires, int) and not isinstance(expires, bool) else 0,
        username=username if isinstance(username, str) else "",
    )


def _pip_width_from(raw: object) -> int:
    """A width outside the usable range is as unusable as a missing one, so
    both take the default rather than being clamped into something the user
    never chose."""
    fallback = PersistedSettings().pip_width
    if not isinstance(raw, int) or isinstance(raw, bool):
        return fallback
    if not PIP_WIDTH_MIN <= raw <= PIP_WIDTH_MAX:
        return fallback
    return raw


def _samples_from(raw: object) -> tuple[ConnectionSample, ...]:
    """Bandwidth samples, skipping any entry that is not a whole sample.

    A hand-edited or truncated file must cost the user their measurement
    history at worst -- never a failed launch, and never a sample with a
    plausible-looking rate attached to a bucket that does not exist.
    """
    if not isinstance(raw, list):
        return ()
    samples: list[ConnectionSample] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kbps = entry.get("kbps")
        at = entry.get("at")
        bucket = entry.get("bucket")
        if not isinstance(kbps, int) or isinstance(kbps, bool) or kbps <= 0:
            continue
        if not isinstance(at, int) or isinstance(at, bool) or at <= 0:
            continue
        if bucket not in TRANSPORT_BUCKETS:
            continue
        samples.append(ConnectionSample(kbps=kbps, at=at, bucket=bucket))
    return tuple(samples)


def _signatures_from(raw: object) -> tuple[str, ...]:
    """Remembered label signatures, skipping anything that is not one. Capped:
    the list only grows, and a settings file is not an archive."""
    if not isinstance(raw, list):
        return ()
    return tuple(s for s in raw if isinstance(s, str) and s)[-_SIGNATURE_CAP:]


# Where the parallel-streaming choice is kept. A new key, because the old one
# cannot say whether a user chose it: every settings file is written whole,
# so "parallel_streaming": false sits in every file saved while the default
# was off, chosen or not. That value is read as "never chosen" and takes the
# new default; an old true is kept; from now on only this key is written.
_STREAM_ACCELERATOR_KEY = "stream_accelerator"


def _parallel_streaming_from(data: dict[str, Any]) -> bool:
    chosen = data.get(_STREAM_ACCELERATOR_KEY)
    if isinstance(chosen, bool):
        return chosen
    if data.get("parallel_streaming") is True:
        return True
    return PersistedSettings().parallel_streaming


def _video_player_from(data: dict[str, Any]) -> VideoPlayer:
    chosen = data.get("video_player")
    for player in VIDEO_PLAYERS:
        if chosen == player:
            return player
    return PersistedSettings().video_player


class JsonSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else default_settings_path()

    def load(self) -> PersistedSettings:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return PersistedSettings()
        if not isinstance(data, dict):
            return PersistedSettings()
        raw_urls = data.get("addon_urls")
        urls = (
            tuple(u for u in raw_urls if isinstance(u, str)) if isinstance(raw_urls, list) else ()
        )
        raw_key = data.get("tmdb_key")
        key = raw_key if isinstance(raw_key, str) and raw_key else None
        raw_mdb = data.get("mdblist_key")
        mdb_key = raw_mdb if isinstance(raw_mdb, str) and raw_mdb else None
        return PersistedSettings(
            addon_urls=urls,
            tmdb_key=key,
            mdblist_key=mdb_key,
            subtitle_style=_style_from(data.get("subtitle_style")),
            track_languages=_track_languages_from(data.get("track_languages")),
            trakt_auth=_trakt_auth_from(data.get("trakt")),
            trakt_sync_forgets=bool(data.get("trakt_sync_forgets", True)),
            trakt_sync_watched=bool(data.get("trakt_sync_watched", True)),
            # Default True: a settings file written before onboarding existed
            # belongs to an established user. Fresh installs have no file at
            # all and take the dataclass default (False) above.
            onboarding_done=bool(data.get("onboarding_done", True)),
            pip_width=_pip_width_from(data.get("pip_width")),
            sort_by_connection=bool(data.get("sort_by_connection", False)),
            connection_samples=_samples_from(data.get("connection_samples")),
            hide_incompatible=bool(data.get("hide_incompatible", False)),
            incompatible_sources=_signatures_from(data.get("incompatible_sources")),
            # Default True: the mark is additive, and a settings file
            # written before it existed belongs to a user who never chose
            # to be without it.
            recommend_sources=bool(data.get("recommend_sources", True)),
            decode_strain=_signatures_from(data.get("decode_strain")),
            parallel_streaming=_parallel_streaming_from(data),
            # Default True: a file written before this existed belongs to a
            # user who never chose to be without it.
            online_segments=bool(data.get("online_segments", True)),
            video_player=_video_player_from(data),
        )

    def save(self, settings: PersistedSettings) -> None:
        style = settings.subtitle_style
        trakt: dict[str, object] = {}
        if settings.trakt_auth is not None:
            trakt = {
                "access_token": settings.trakt_auth.access_token,
                "refresh_token": settings.trakt_auth.refresh_token,
                "expires_at": settings.trakt_auth.expires_at,
                "username": settings.trakt_auth.username,
            }
        payload = {
            "addon_urls": list(settings.addon_urls),
            "tmdb_key": settings.tmdb_key,
            "mdblist_key": settings.mdblist_key,
            "subtitle_style": {
                "font_size": style.font_size,
                "color": style.color,
                "border_size": style.border_size,
                "back_opacity": style.back_opacity,
                "bold": style.bold,
            },
            "track_languages": {
                "audio": settings.track_languages.audio,
                "subtitle": settings.track_languages.subtitle,
            },
            "trakt": trakt,
            "trakt_sync_forgets": settings.trakt_sync_forgets,
            "trakt_sync_watched": settings.trakt_sync_watched,
            "onboarding_done": settings.onboarding_done,
            "pip_width": settings.pip_width,
            "sort_by_connection": settings.sort_by_connection,
            "hide_incompatible": settings.hide_incompatible,
            "incompatible_sources": list(settings.incompatible_sources),
            "recommend_sources": settings.recommend_sources,
            "decode_strain": list(settings.decode_strain),
            _STREAM_ACCELERATOR_KEY: settings.parallel_streaming,
            "online_segments": settings.online_segments,
            "video_player": settings.video_player,
            "connection_samples": [
                {"kbps": s.kbps, "at": s.at, "bucket": s.bucket}
                for s in settings.connection_samples
            ],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic replace: never leave a half-written settings file behind.
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            # Persistence is best-effort; the running session keeps working.
            _log.warning("failed to save settings to %s: %s", self._path, exc)
