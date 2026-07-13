"""JSON-file SettingsStore adapter (XDG config dir by default)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from gravitas.domain.models import PersistedSettings

_log = logging.getLogger(__name__)


def default_settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(base) if base else Path.home() / ".config"
    return root / "gravitas" / "settings.json"


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
        return PersistedSettings(addon_urls=urls, tmdb_key=key)

    def save(self, settings: PersistedSettings) -> None:
        payload = {
            "addon_urls": list(settings.addon_urls),
            "tmdb_key": settings.tmdb_key,
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
