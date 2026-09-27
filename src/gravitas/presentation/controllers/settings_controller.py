"""QObject bridge: manage installed addons for the Settings page."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from PySide6.QtCore import Property, QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.compatibility import IncompatibleSources
from gravitas.application.connection_speed import ConnectionSpeed
from gravitas.application.playback_capability import PlaybackCapability
from gravitas.application.recommendation import SourceRecommendations
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.domain import languages
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import (
    PIP_WIDTH_MAX,
    PIP_WIDTH_MIN,
    VIDEO_PLAYERS,
    PersistedSettings,
    SubtitleStyle,
    TrackLanguages,
    TraktAuth,
    VideoPlayer,
)
from gravitas.domain.ports import SettingsStore
from gravitas.presentation.external_url import open_in_browser
from gravitas.presentation.models.addon_list_model import AddonListModel


def _format_bucket(key: str) -> str:
    """ "hevc:2160" as a person would say it. An unrecognised key is shown as
    it is rather than dropped: a line the user cannot parse still tells them
    something is remembered, and a silently missing one does not."""
    codec, _, height = key.partition(":")
    if not height.isdigit():
        return key
    tier = "4K" if int(height) >= 2160 else f"{height}p"
    return f"{tier} {codec.upper()}"


class _Accelerator(Protocol):
    """The multi-connection stream proxy, as Settings needs it (a
    SegmentedStreamProxy satisfies it): a toggle, and nothing else."""

    enabled: bool


class _RefreshesCatalog(Protocol):
    async def load_catalog(self) -> None: ...


class _KeyHolder(Protocol):
    key: str | None


class _StyleHolder(Protocol):
    style: SubtitleStyle


class _Toggle(Protocol):
    enabled: bool


class _VideoPlayerHolder(Protocol):
    """The video player setting: what the user chose, the engine this run
    plays with (the choice applies at start-up), and whether the native
    engine is built into this copy at all."""

    choice: str
    running: str
    native_available: bool


class _LanguagesHolder(Protocol):
    languages: TrackLanguages


def _language_options(*extra: tuple[str, str]) -> list[dict[str, str]]:
    """The rows of a language menu: `extra` first (the choices that are not a
    language, with no flag), then every language. `flag` names an image under
    qml/flags/ -- the country, or "globe" for a language with none."""
    rows = [{"code": code, "label": label, "flag": ""} for code, label in extra]
    rows.extend(
        {
            "code": language.code,
            "label": language.name,
            "flag": language.country.lower() if language.country else "globe",
        }
        for language in languages.LANGUAGES
    )
    return rows


# Built once: the catalog is a constant, and QML reads these on every visit.
_AUDIO_LANGUAGE_OPTIONS = _language_options(("", "As the file says"))
_SUBTITLE_LANGUAGE_OPTIONS = _language_options(
    ("", "As the file says"), (languages.SUBTITLES_OFF, "Off")
)


class _OnboardingHolder(Protocol):
    done: bool


class _PipWidthHolder(Protocol):
    width: int


class _TraktHolder(Protocol):
    """What persist() reads off the Trakt account (TraktAccount satisfies it).
    Only the granted session and the user's mirror policy — app credentials
    are build-level, never user state."""

    auth: TraktAuth | None
    sync_forgets: bool
    sync_watched: bool


class SettingsController(QObject):
    errorOccurred = Signal(str)
    addonsChanged = Signal()
    tmdbKeyChanged = Signal()
    subtitleStyleChanged = Signal()
    mdblistKeyChanged = Signal()
    pipWidthChanged = Signal()
    connectionSortChanged = Signal()
    trackLanguagesChanged = Signal()
    # Never emitted: the language menus are constants. Declared because the
    # generated qmltypes drop `constant=True`, and qmllint rejects reading a
    # property with neither.
    languageOptionsChanged = Signal()

    def __init__(
        self,
        uninstall: UninstallAddon,
        repo: AddonRepository,
        model: AddonListModel,
        catalog_controller: _RefreshesCatalog,
        key_holder: _KeyHolder | None = None,
        store: SettingsStore | None = None,
        style_holder: _StyleHolder | None = None,
        mdblist_key_holder: _KeyHolder | None = None,
        trakt_holder: _TraktHolder | None = None,
        onboarding_holder: _OnboardingHolder | None = None,
        pip_width_holder: _PipWidthHolder | None = None,
        connection: ConnectionSpeed | None = None,
        display_height: Callable[[], int] | None = None,
        incompatible: IncompatibleSources | None = None,
        recommendations: SourceRecommendations | None = None,
        capability: PlaybackCapability | None = None,
        accelerator: _Accelerator | None = None,
        languages_holder: _LanguagesHolder | None = None,
        online_segments: _Toggle | None = None,
        video_player: _VideoPlayerHolder | None = None,
    ) -> None:
        super().__init__()
        self._uninstall = uninstall
        self._repo = repo
        self._model = model
        self._catalog_controller = catalog_controller
        self._key_holder = key_holder
        self._store = store
        self._style_holder = style_holder
        self._mdblist_key_holder = mdblist_key_holder
        self._trakt_holder = trakt_holder
        self._onboarding_holder = onboarding_holder
        self._pip_width_holder = pip_width_holder
        self._connection = connection
        self._display_height = display_height
        self._incompatible = incompatible
        self._recommendations = recommendations
        self._capability = capability
        self._accelerator = accelerator
        self._languages_holder = languages_holder
        self._online_segments = online_segments
        self._video_player = video_player
        # True until bootstrap primes the list: opening Settings mid-startup
        # shows a spinner instead of an empty card that pops full moments
        # later. The first refreshAddons() clears it.
        self._addons_loading = True

    @Property(str, notify=tmdbKeyChanged)
    def tmdbKey(self) -> str:
        if self._key_holder is not None and self._key_holder.key:
            return self._key_holder.key
        return ""

    @Slot(str)
    def setTmdbKey(self, key: str) -> None:
        if self._key_holder is not None:
            self._key_holder.key = key.strip() or None
            self.tmdbKeyChanged.emit()
        self.persist()

    @Property(str, notify=mdblistKeyChanged)
    def mdblistKey(self) -> str:
        if self._mdblist_key_holder is not None and self._mdblist_key_holder.key:
            return self._mdblist_key_holder.key
        return ""

    @Slot(str)
    def setMdblistKey(self, key: str) -> None:
        if self._mdblist_key_holder is not None:
            self._mdblist_key_holder.key = key.strip() or None
            self.mdblistKeyChanged.emit()
        self.persist()

    @Property(int, notify=pipWidthChanged)
    def pipWidth(self) -> int:
        """Remembered width of the picture-in-picture tile. Main.qml sizes the
        window with it on entry and writes the resized value back on exit."""
        if self._pip_width_holder is not None:
            return self._pip_width_holder.width
        return PersistedSettings().pip_width

    @Slot(int)
    def setPipWidth(self, width: int) -> None:
        """Store only — persisting is the caller's persist() call, as with
        every other setting written on the way out of a page."""
        if self._pip_width_holder is None:
            return
        self._pip_width_holder.width = max(PIP_WIDTH_MIN, min(PIP_WIDTH_MAX, width))
        self.pipWidthChanged.emit()

    @Property(bool, notify=connectionSortChanged)
    def sortByConnection(self) -> bool:
        return self._connection.enabled if self._connection is not None else False

    @Slot(bool)
    def setSortByConnection(self, enabled: bool) -> None:
        if self._connection is None:
            return
        self._connection.enabled = enabled
        self.connectionSortChanged.emit()
        self.persist()

    @Property(int, notify=connectionSortChanged)
    def connectionMbps(self) -> int:
        """Measured downstream bandwidth, rounded to whole Mbps; 0 when nothing
        has been measured on this link yet. Rounded because the number is an
        estimate from playback, and printing 84.3 would claim a precision the
        measurement does not have."""
        if self._connection is None:
            return 0
        kbps = self._connection.estimate_kbps()
        return round(kbps / 1000) if kbps else 0

    @Property(bool, notify=connectionSortChanged)
    def hideIncompatible(self) -> bool:
        return self._incompatible.enabled if self._incompatible is not None else False

    @Slot(bool)
    def setHideIncompatible(self, enabled: bool) -> None:
        if self._incompatible is None:
            return
        self._incompatible.enabled = enabled
        self.connectionSortChanged.emit()
        self.persist()

    @Property(int, notify=connectionSortChanged)
    def knownBadSourceCount(self) -> int:
        """How many releases the player has caught rendering wrong. Shown so
        the list is auditable rather than a silent filter."""
        return len(self._incompatible.signatures) if self._incompatible is not None else 0

    @Slot()
    def forgetBadSources(self) -> None:
        """Drop every learned verdict. The label heuristic still applies; this
        only clears what playback taught, for when a mpv or driver update
        changes what renders."""
        if self._incompatible is None:
            return
        self._incompatible.signatures = ()
        self.connectionSortChanged.emit()
        self.persist()

    @Property(bool, notify=connectionSortChanged)
    def recommendSources(self) -> bool:
        return self._recommendations.enabled if self._recommendations is not None else False

    @Slot(bool)
    def setRecommendSources(self, enabled: bool) -> None:
        if self._recommendations is None:
            return
        self._recommendations.enabled = enabled
        self.connectionSortChanged.emit()
        self.persist()

    @Property(int, notify=connectionSortChanged)
    def strainedFormatCount(self) -> int:
        """How many codec/size combinations playback has caught this machine
        dropping frames in. Shown for the same reason as the unplayable count:
        a filter nobody can inspect is one nobody can correct."""
        return len(self._capability.strained) if self._capability is not None else 0

    @Property(str, notify=connectionSortChanged)
    def strainedFormats(self) -> str:
        """Those combinations, spelled out ("4K HEVC, 4K AV1"). The bucket key
        is an identifier; this is the sentence a person can check against what
        they remember stuttering."""
        if self._capability is None:
            return ""
        return ", ".join(_format_bucket(key) for key in self._capability.strained)

    @Slot()
    def forgetDecodeVerdicts(self) -> None:
        """Drop what playback taught about this machine's decoding. For a
        driver or mpv update, or a machine that was simply busy."""
        if self._capability is None:
            return
        self._capability.strained = ()
        self.connectionSortChanged.emit()
        self.persist()

    @Property(bool, notify=connectionSortChanged)
    def parallelStreaming(self) -> bool:
        return self._accelerator.enabled if self._accelerator is not None else False

    @Slot(bool)
    def setParallelStreaming(self, enabled: bool) -> None:
        """Takes effect on the next playback: the URL mpv is already reading
        was chosen when it started, and switching a live stream's transport
        under it would be a stall to fix a stall."""
        if self._accelerator is None:
            return
        self._accelerator.enabled = enabled
        self.connectionSortChanged.emit()
        self.persist()

    @Property(bool, notify=connectionSortChanged)
    def onlineSegments(self) -> bool:
        return self._online_segments.enabled if self._online_segments is not None else False

    @Slot(bool)
    def setOnlineSegments(self, enabled: bool) -> None:
        """From the next file played; the one playing keeps what it has."""
        if self._online_segments is None:
            return
        self._online_segments.enabled = enabled
        self.connectionSortChanged.emit()
        self.persist()

    # --- video player ---

    videoPlayerChanged = Signal()

    @Property("QVariantList", notify=languageOptionsChanged)  # type: ignore[arg-type]
    def videoPlayerOptions(self) -> list[dict[str, str]]:
        """The engines to choose from, as {code, label} rows."""
        return [
            {"code": "native", "label": "Gravitas (native)"},
            {"code": "mpv", "label": "mpv"},
        ]

    @Property(str, notify=videoPlayerChanged)
    def videoPlayer(self) -> str:
        return self._video_player.choice if self._video_player is not None else "native"

    @Property(str, notify=languageOptionsChanged)
    def videoPlayerRunning(self) -> str:
        """The engine this run plays with, which a new choice does not change
        until the next start."""
        return self._video_player.running if self._video_player is not None else "mpv"

    @Property(bool, notify=languageOptionsChanged)
    def nativePlayerAvailable(self) -> bool:
        return self._video_player.native_available if self._video_player is not None else False

    @Slot(str)
    def setVideoPlayer(self, choice: str) -> None:
        """From the next start: the engine shapes how the app starts."""
        if self._video_player is None or choice not in VIDEO_PLAYERS:
            return
        if choice == self._video_player.choice:
            return
        self._video_player.choice = choice
        self.videoPlayerChanged.emit()
        self.persist()

    @Property(int, notify=connectionSortChanged)
    def screenHeight(self) -> int:
        """Physical pixel height of the best screen, 0 when unknown. Shown next
        to the toggle so the ordering is explainable: it is the number that
        decides which sources are downscaled before anyone sees them."""
        return self._display_height() if self._display_height is not None else 0

    @Slot(str)
    def openLink(self, url: str) -> None:
        """External links from the Settings page. Routed through
        open_in_browser: QML's Qt.openUrlExternally spawns the browser under
        the frozen bundle's LD_LIBRARY_PATH, where it dies silently."""
        open_in_browser(url)

    @Slot()
    def persist(self) -> None:
        """Write the current user state to the store."""
        if self._store is None:
            return
        key = self._key_holder.key if self._key_holder is not None else None
        mdb = self._mdblist_key_holder.key if self._mdblist_key_holder is not None else None
        style = self._style_holder.style if self._style_holder is not None else SubtitleStyle()
        trakt = self._trakt_holder
        self._store.save(
            PersistedSettings(
                addon_urls=tuple(self._repo.user_addon_urls()),
                tmdb_key=key,
                mdblist_key=mdb,
                subtitle_style=style,
                track_languages=self._languages(),
                trakt_auth=trakt.auth if trakt is not None else None,
                trakt_sync_forgets=trakt.sync_forgets if trakt is not None else True,
                trakt_sync_watched=trakt.sync_watched if trakt is not None else True,
                # False when unwired: a mid-onboarding persist (e.g. an addon
                # install) must not mark the wizard finished.
                onboarding_done=(
                    self._onboarding_holder.done if self._onboarding_holder is not None else False
                ),
                # The holder, not the Property: a QML Property is not readable
                # as a plain attribute from Python.
                pip_width=(
                    self._pip_width_holder.width
                    if self._pip_width_holder is not None
                    else PersistedSettings().pip_width
                ),
                sort_by_connection=(
                    self._connection.enabled if self._connection is not None else False
                ),
                connection_samples=(
                    self._connection.samples if self._connection is not None else ()
                ),
                hide_incompatible=(
                    self._incompatible.enabled if self._incompatible is not None else False
                ),
                incompatible_sources=(
                    self._incompatible.signatures if self._incompatible is not None else ()
                ),
                recommend_sources=(
                    self._recommendations.enabled
                    if self._recommendations is not None
                    else PersistedSettings().recommend_sources
                ),
                decode_strain=(self._capability.strained if self._capability is not None else ()),
                parallel_streaming=(
                    self._accelerator.enabled
                    if self._accelerator is not None
                    else PersistedSettings().parallel_streaming
                ),
                online_segments=(
                    self._online_segments.enabled
                    if self._online_segments is not None
                    else PersistedSettings().online_segments
                ),
                video_player=self._chosen_player(),
            )
        )

    def _chosen_player(self) -> VideoPlayer:
        choice = self._video_player.choice if self._video_player is not None else ""
        for player in VIDEO_PLAYERS:
            if choice == player:
                return player
        return PersistedSettings().video_player

    # --- track languages ---

    def _languages(self) -> TrackLanguages:
        if self._languages_holder is None:
            return TrackLanguages()
        return self._languages_holder.languages

    def _update_languages(self, **changes: str) -> None:
        if self._languages_holder is None:
            return
        updated = replace(self._languages_holder.languages, **changes)
        if updated == self._languages_holder.languages:
            return
        self._languages_holder.languages = updated
        self.trackLanguagesChanged.emit()
        self.persist()

    @Property(str, notify=trackLanguagesChanged)
    def audioLanguage(self) -> str:
        return self._languages().audio

    @Property(str, notify=trackLanguagesChanged)
    def subtitleLanguage(self) -> str:
        return self._languages().subtitle

    @Property("QVariantList", notify=languageOptionsChanged)  # type: ignore[arg-type]
    def audioLanguageOptions(self) -> list[dict[str, str]]:
        """{code, label} rows for the audio menu; code "" is no preference."""
        return _AUDIO_LANGUAGE_OPTIONS

    @Property("QVariantList", notify=languageOptionsChanged)  # type: ignore[arg-type]
    def subtitleLanguageOptions(self) -> list[dict[str, str]]:
        """As audioLanguageOptions, plus "Off" (languages.SUBTITLES_OFF)."""
        return _SUBTITLE_LANGUAGE_OPTIONS

    @Slot(str)
    def setAudioLanguage(self, code: str) -> None:
        """Takes effect from the next file opened; the one playing keeps its
        tracks, and the player's own menu is the way to change those."""
        self._update_languages(audio=languages.normalized(code))

    @Slot(str)
    def setSubtitleLanguage(self, code: str) -> None:
        self._update_languages(subtitle=languages.normalized(code, subtitles=True))

    # --- subtitle style ---

    def _style(self) -> SubtitleStyle:
        return self._style_holder.style if self._style_holder is not None else SubtitleStyle()

    def _update_style(self, **changes: object) -> None:
        if self._style_holder is None:
            return
        self._style_holder.style = replace(self._style_holder.style, **changes)  # type: ignore[arg-type]
        self.subtitleStyleChanged.emit()
        self.persist()

    @Property(int, notify=subtitleStyleChanged)
    def subFontSize(self) -> int:
        return self._style().font_size

    @Property(str, notify=subtitleStyleChanged)
    def subColor(self) -> str:
        return self._style().color

    @Property(int, notify=subtitleStyleChanged)
    def subBorderSize(self) -> int:
        return self._style().border_size

    @Property(int, notify=subtitleStyleChanged)
    def subBackOpacity(self) -> int:
        return self._style().back_opacity

    @Property(bool, notify=subtitleStyleChanged)
    def subBold(self) -> bool:
        return self._style().bold

    @Slot(int)
    def setSubFontSize(self, size: int) -> None:
        self._update_style(font_size=max(20, min(100, size)))

    @Slot(str)
    def setSubColor(self, color: str) -> None:
        self._update_style(color=color)

    @Slot(int)
    def setSubBorderSize(self, size: int) -> None:
        self._update_style(border_size=max(0, min(8, size)))

    @Slot(int)
    def setSubBackOpacity(self, opacity: int) -> None:
        self._update_style(back_opacity=max(0, min(100, opacity)))

    @Slot(bool)
    def setSubBold(self, bold: bool) -> None:
        self._update_style(bold=bold)

    @Slot()
    def resetSubtitleStyle(self) -> None:
        if self._style_holder is None:
            return
        self._style_holder.style = SubtitleStyle()
        self.subtitleStyleChanged.emit()
        self.persist()

    @Property(bool, notify=addonsChanged)
    def addonsLoading(self) -> bool:
        return self._addons_loading

    @Slot()
    def refreshAddons(self) -> None:
        installed = self._repo.installed()
        protected = {m.id for m in installed if self._repo.is_protected(m.id)}
        self._model.set_addons(installed, protected)
        self._addons_loading = False
        self.addonsChanged.emit()

    @asyncSlot(str)  # type: ignore[untyped-decorator]
    async def removeAddon(self, addon_id: str) -> None:
        try:
            await self._uninstall(addon_id)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
            return
        await self._catalog_controller.load_catalog()
        self.refreshAddons()
        self.persist()
