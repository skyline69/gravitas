"""JSON-file SettingsStore adapter (XDG config dir by default)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from gravitas.domain.models import PersistedSettings, SubtitleStyle, TraktAuth

_log = logging.getLogger(__name__)


def default_settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(base) if base else Path.home() / ".config"
    return root / "gravitas" / "settings.json"


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
            trakt_auth=_trakt_auth_from(data.get("trakt")),
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
            "trakt": trakt,
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
