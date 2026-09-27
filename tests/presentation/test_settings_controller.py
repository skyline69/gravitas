from gravitas.application.addon_repository import AddonRepository
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.presentation.controllers.settings_controller import SettingsController
from gravitas.presentation.models.addon_list_model import AddonListModel
from tests.application.test_addon_repository import FakeSource


class FakeCatalogController:
    def __init__(self) -> None:
        self.refresh_calls = 0

    async def load_catalog(self) -> None:
        self.refresh_calls += 1


def _build(
    repo: AddonRepository,
) -> tuple[SettingsController, AddonListModel, FakeCatalogController]:
    model = AddonListModel()
    catalog = FakeCatalogController()
    controller = SettingsController(UninstallAddon(repo), repo, model, catalog)  # type: ignore[arg-type]
    return controller, model, catalog


async def test_refresh_populates_model_with_protected_flag(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    protected = await repo.install("https://a/", protected=True)
    controller, model, _ = _build(repo)
    controller.refreshAddons()
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0), AddonListModel.RemovableRole) is False
    assert model.data(model.index(0, 0), AddonListModel.IdRole) == protected.id


async def test_addons_loading_true_until_first_refresh(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    controller, _model, _ = _build(repo)
    # Settings opened mid-bootstrap: the list is not primed yet.
    assert controller.addonsLoading is True
    controller.refreshAddons()
    assert controller.addonsLoading is False


async def test_remove_addon_uninstalls_and_refreshes(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")  # not protected
    controller, model, catalog = _build(repo)
    controller.refreshAddons()
    await controller.removeAddon(manifest.id)
    assert repo.installed() == []
    assert catalog.refresh_calls == 1
    assert model.rowCount() == 0


async def test_remove_protected_emits_error(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/", protected=True)
    controller, _model, catalog = _build(repo)
    controller.refreshAddons()
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    await controller.removeAddon(manifest.id)
    assert len(errors) == 1
    assert repo.installed() != []
    assert catalog.refresh_calls == 0


class _KeyHolder:
    key: str | None = None


async def test_set_tmdb_key_updates_holder(qapp: object) -> None:
    from gravitas.application.addon_repository import AddonRepository
    from gravitas.application.uninstall_addon import UninstallAddon
    from gravitas.presentation.controllers.settings_controller import SettingsController
    from gravitas.presentation.models.addon_list_model import AddonListModel
    from tests.application.test_addon_repository import FakeSource

    repo = AddonRepository(FakeSource())
    holder = _KeyHolder()
    c = SettingsController(
        UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController(), holder
    )
    c.setTmdbKey("ABC")
    assert holder.key == "ABC"


class _FakeStore:
    def __init__(self) -> None:
        self.saved: list[object] = []

    def load(self) -> object:
        raise NotImplementedError

    def save(self, settings: object) -> None:
        self.saved.append(settings)


async def test_set_tmdb_key_persists(qapp: object) -> None:
    from gravitas.domain.models import PersistedSettings

    repo = AddonRepository(FakeSource())
    holder = _KeyHolder()
    store = _FakeStore()
    c = SettingsController(
        UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController(), holder, store
    )
    c.setTmdbKey("ABC")
    assert store.saved == [PersistedSettings(addon_urls=(), tmdb_key="ABC")]
    assert c.tmdbKey == "ABC"


async def test_remove_addon_persists_remaining_urls(qapp: object) -> None:
    from gravitas.domain.models import PersistedSettings

    repo = AddonRepository(FakeSource())
    await repo.install("https://a/")
    await repo.install("https://b/")
    store = _FakeStore()
    c = SettingsController(
        UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController(), None, store
    )
    await c.removeAddon("https://a/")
    assert store.saved == [PersistedSettings(addon_urls=("https://b/",), tmdb_key=None)]


class _StyleHolder:
    def __init__(self) -> None:
        from gravitas.domain.models import SubtitleStyle

        self.style = SubtitleStyle()


async def test_set_mdblist_key_updates_and_persists(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    tmdb_holder = _KeyHolder()
    mdb_holder = _KeyHolder()
    store = _FakeStore()
    styles = _StyleHolder()
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        tmdb_holder,
        store,
        styles,
        mdb_holder,
    )

    assert c.mdblistKey == ""
    c.setMdblistKey("  key-xyz  ")
    assert mdb_holder.key == "key-xyz"
    assert c.mdblistKey == "key-xyz"
    assert store.saved[-1].mdblist_key == "key-xyz"  # type: ignore[attr-defined]

    c.setMdblistKey("")
    assert mdb_holder.key is None


async def test_subtitle_style_updates_persist_and_notify(qapp: object) -> None:
    from gravitas.domain.models import SubtitleStyle

    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    styles = _StyleHolder()
    c = SettingsController(
        UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController(), None, store, styles
    )
    fired: list[None] = []
    c.subtitleStyleChanged.connect(lambda: fired.append(None))

    c.setSubFontSize(70)
    c.setSubColor("#FFE400")
    c.setSubBold(True)
    assert styles.style.font_size == 70
    assert styles.style.color == "#FFE400"
    assert styles.style.bold is True
    assert len(fired) == 3
    assert len(store.saved) == 3

    c.setSubFontSize(999)  # clamped
    assert styles.style.font_size == 100

    c.resetSubtitleStyle()
    assert styles.style == SubtitleStyle()


async def test_open_link_routes_through_env_scrubbed_browser_helper(qapp: object) -> None:
    """Settings' external links must go through open_in_browser, not
    Qt.openUrlExternally — the QML path inherits the frozen bundle's
    LD_LIBRARY_PATH and the spawned browser dies against bundled libs."""
    from unittest.mock import patch

    repo = AddonRepository(FakeSource())
    c = SettingsController(UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController())
    with patch("gravitas.presentation.controllers.settings_controller.open_in_browser") as opened:
        c.openLink("https://github.com/skyline69/gravitas")
    opened.assert_called_once_with("https://github.com/skyline69/gravitas")


class _OnboardingHolder:
    done: bool = False


async def test_persist_includes_onboarding_flag(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    onboarding = _OnboardingHolder()
    onboarding.done = True
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        store=store,
        onboarding_holder=onboarding,
    )
    c.persist()
    assert store.saved[-1].onboarding_done is True  # type: ignore[attr-defined]


class _PipWidthHolder:
    width: int = 480


async def test_pip_width_defaults_without_a_holder(qapp: object) -> None:
    from gravitas.domain.models import PersistedSettings

    repo = AddonRepository(FakeSource())
    c, _model, _catalog = _build(repo)
    assert c.pipWidth == PersistedSettings().pip_width
    # No holder: the setter is inert rather than raising.
    c.setPipWidth(700)
    assert c.pipWidth == PersistedSettings().pip_width


async def test_set_pip_width_updates_holder_and_clamps(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    holder = _PipWidthHolder()
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        None,
        None,
        None,
        None,
        None,
        None,
        holder,
    )
    c.setPipWidth(700)
    assert holder.width == 700
    assert c.pipWidth == 700
    c.setPipWidth(50)
    assert holder.width == 240
    c.setPipWidth(9000)
    assert holder.width == 1280


async def test_pip_width_is_persisted(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    holder = _PipWidthHolder()
    store = _FakeStore()
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        None,
        store,
        None,
        None,
        None,
        None,
        holder,
    )
    # setPipWidth alone stores nothing: the write rides the caller's persist().
    c.setPipWidth(560)
    assert store.saved == []
    c.persist()
    assert store.saved[-1].pip_width == 560  # type: ignore[attr-defined]


async def test_connection_sort_toggle_persists(qapp: object) -> None:
    from gravitas.application.connection_speed import ConnectionSpeed

    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    connection = ConnectionSpeed(lambda: "wifi")
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        store=store,
        connection=connection,
    )
    assert c.sortByConnection is False

    c.setSortByConnection(True)

    assert c.sortByConnection is True
    assert connection.enabled is True
    assert store.saved[-1].sort_by_connection is True


async def test_measured_samples_reach_the_store(qapp: object) -> None:
    from gravitas.application.connection_speed import ConnectionSpeed

    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    connection = ConnectionSpeed(lambda: "wifi")
    connection.record(42_000)
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        store=store,
        connection=connection,
    )
    c.persist()
    assert store.saved[-1].connection_samples == connection.samples


async def test_measured_speed_reads_as_whole_mbps(qapp: object) -> None:
    from gravitas.application.connection_speed import ConnectionSpeed

    repo = AddonRepository(FakeSource())
    connection = ConnectionSpeed(lambda: "wifi")
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        connection=connection,
    )
    # Nothing measured: 0 is the "not measured yet" the page prints.
    assert c.connectionMbps == 0

    for _ in range(4):
        connection.record(84_400)

    assert c.connectionMbps == 84


async def test_without_a_connection_the_toggle_is_inert(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    c = SettingsController(UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController())
    c.setSortByConnection(True)
    assert c.sortByConnection is False
    assert c.connectionMbps == 0


async def test_screen_height_is_reported_for_the_sources_card(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        display_height=lambda: 1440,
    )
    assert c.screenHeight == 1440


async def test_screen_height_is_zero_when_nothing_answers(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    c = SettingsController(UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController())
    assert c.screenHeight == 0


async def test_hide_incompatible_toggle_persists(qapp: object) -> None:
    from gravitas.application.compatibility import IncompatibleSources

    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    incompatible = IncompatibleSources()
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        store=store,
        incompatible=incompatible,
    )
    assert c.hideIncompatible is False

    c.setHideIncompatible(True)

    assert incompatible.enabled is True
    assert store.saved[-1].hide_incompatible is True


async def test_learned_verdicts_persist_and_can_be_forgotten(qapp: object) -> None:
    from gravitas.application.compatibility import IncompatibleSources

    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    incompatible = IncompatibleSources()
    incompatible.remember("Silo S01E01 DV", "")
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        store=store,
        incompatible=incompatible,
    )
    assert c.knownBadSourceCount == 1
    c.persist()
    assert store.saved[-1].incompatible_sources == incompatible.signatures

    c.forgetBadSources()

    assert c.knownBadSourceCount == 0
    assert store.saved[-1].incompatible_sources == ()


async def test_recommend_toggle_persists(qapp: object) -> None:
    from gravitas.application.recommendation import SourceRecommendations

    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    recommendations = SourceRecommendations()
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        store=store,
        recommendations=recommendations,
    )
    assert c.recommendSources is True  # on by default, unlike the sort

    c.setRecommendSources(False)

    assert recommendations.enabled is False
    assert store.saved[-1].recommend_sources is False


async def test_decode_verdicts_are_named_persisted_and_forgettable(qapp: object) -> None:
    from gravitas.application.playback_capability import PlaybackCapability

    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    capability = PlaybackCapability()
    capability.strained = ("av1:2160", "hevc:1080")
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        store=store,
        capability=capability,
    )
    assert c.strainedFormatCount == 2
    # Spelled out, not keyed: the user can check this against what stuttered.
    assert c.strainedFormats == "4K AV1, 1080p HEVC"
    c.persist()
    assert store.saved[-1].decode_strain == ("av1:2160", "hevc:1080")

    c.forgetDecodeVerdicts()

    assert c.strainedFormatCount == 0
    assert store.saved[-1].decode_strain == ()


async def test_an_unwired_recommender_reports_off_and_persists_the_default(
    qapp: object,
) -> None:
    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    c = SettingsController(
        UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController(), store=store
    )
    assert c.recommendSources is False
    assert c.strainedFormats == ""
    c.persist()
    # Nothing wired must not write "the user turned it off".
    assert store.saved[-1].recommend_sources is True


async def test_parallel_streaming_toggle_persists(qapp: object) -> None:
    class _Accelerator:
        enabled = False

    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    accelerator = _Accelerator()
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        store=store,
        accelerator=accelerator,
    )
    assert c.parallelStreaming is False

    c.setParallelStreaming(True)

    assert accelerator.enabled is True
    assert store.saved[-1].parallel_streaming is True


async def test_the_video_player_choice_persists_and_says_it_waits_for_a_restart(
    qapp: object,
) -> None:
    class _VideoPlayer:
        choice = "native"
        running = "native"
        native_available = True

    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    holder = _VideoPlayer()
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        store=store,
        video_player=holder,
    )
    assert c.videoPlayer == "native"
    assert [o["code"] for o in c.videoPlayerOptions] == ["native", "mpv"]

    c.setVideoPlayer("mpv")

    assert holder.choice == "mpv"
    assert store.saved[-1].video_player == "mpv"
    # This run keeps the engine it started with.
    assert c.videoPlayerRunning == "native"
    # Nothing that is not an engine is taken.
    c.setVideoPlayer("vlc")
    assert holder.choice == "mpv"


class _LanguagesHolder:
    def __init__(self) -> None:
        from gravitas.domain.models import TrackLanguages

        self.languages = TrackLanguages()


async def test_track_language_choices_are_held_and_persisted(qapp: object) -> None:
    from gravitas.domain.models import TrackLanguages

    repo = AddonRepository(FakeSource())
    holder = _LanguagesHolder()
    store = _FakeStore()
    controller = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),  # type: ignore[arg-type]
        store=store,  # type: ignore[arg-type]
        languages_holder=holder,
    )
    changes: list[None] = []
    controller.trackLanguagesChanged.connect(lambda: changes.append(None))

    controller.setAudioLanguage("ja")
    controller.setSubtitleLanguage("off")
    assert holder.languages == TrackLanguages(audio="ja", subtitle="off")
    assert controller.audioLanguage == "ja"
    assert controller.subtitleLanguage == "off"
    assert store.saved[-1].track_languages == holder.languages  # type: ignore[attr-defined]
    assert len(changes) == 2

    # Choosing what is already chosen writes nothing.
    controller.setAudioLanguage("ja")
    assert len(store.saved) == 2
    # A code the catalog does not know is no preference, and "off" is not an
    # audio choice.
    controller.setAudioLanguage("off")
    assert holder.languages.audio == ""


async def test_language_menus_lead_with_the_choices_that_are_not_languages(
    qapp: object,
) -> None:
    repo = AddonRepository(FakeSource())
    controller, _model, _ = _build(repo)
    audio = controller.audioLanguageOptions
    subtitles = controller.subtitleLanguageOptions
    assert [row["code"] for row in audio[:1]] == [""]
    assert [row["code"] for row in subtitles[:2]] == ["", "off"]
    german = next(row for row in audio if row["code"] == "de")
    assert german == {"code": "de", "label": "German", "flag": "de"}
    assert next(row for row in audio if row["code"] == "eo")["flag"] == "globe"
    assert audio[0]["flag"] == ""


def test_every_language_menu_flag_ships_with_the_app(qapp: object) -> None:
    """Flags are bundled images, not emoji; a flag the catalog names but the
    folder lacks is a blank square in the menu. Re-run scripts/vendor_flags.py
    after adding a language."""
    from pathlib import Path

    import gravitas.presentation as presentation

    flags = Path(presentation.__file__).parent / "qml" / "flags"
    repo = AddonRepository(FakeSource())
    controller, _model, _ = _build(repo)
    for row in controller.subtitleLanguageOptions:
        if row["flag"]:
            assert (flags / f"{row['flag']}.svg").is_file(), row
