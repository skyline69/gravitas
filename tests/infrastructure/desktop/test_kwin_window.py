from pathlib import Path

from gravitas.domain.ports import FloatingWindow
from gravitas.infrastructure.desktop.kwin_window import (
    KWinFloatingWindow,
    float_script,
    restore_script,
)


class FakeKWin:
    """Stands in for org.kde.KWin /Scripting: records every call."""

    def __init__(self, load_id: int = 0) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.load_id = load_id

    def __call__(self, method: str, *args: object) -> object:
        self.calls.append((method, *args))
        return self.load_id if method == "loadScript" else True


def _names(fake: FakeKWin) -> list[str]:
    return [call[0] for call in fake.calls]  # type: ignore[misc]


def test_floating_loads_a_script_that_stays_loaded(tmp_path: Path) -> None:
    kwin = FakeKWin()
    window: FloatingWindow = KWinFloatingWindow(kwin, tmp_path, "dev.skyline.Gravitas", pid=4242)
    window.set_floating(True)
    assert _names(kwin) == ["unloadScript", "loadScript", "start"]
    path = kwin.calls[1][1]
    source = Path(str(path)).read_text()
    assert '"dev.skyline.gravitas"' in source
    assert "keepAbove = true" in source and "noBorder = true" in source
    # A flag change can recreate the window; the script must claim that one too.
    assert "windowAdded.connect" in source


def test_leaving_unloads_the_float_script_before_restoring(tmp_path: Path) -> None:
    kwin = FakeKWin()
    window = KWinFloatingWindow(kwin, tmp_path, "dev.skyline.Gravitas", pid=4242)
    window.set_floating(True)
    float_name = kwin.calls[1][2]
    kwin.calls.clear()

    window.set_floating(False)
    assert kwin.calls[0] == ("unloadScript", float_name), "left loaded it would re-pin the window"
    assert _names(kwin)[1:] == ["unloadScript", "loadScript", "start"]
    restore = Path(str(kwin.calls[2][1])).read_text()
    assert "keepAbove = false" in restore and "noBorder = false" in restore
    assert "windowAdded" not in restore


def test_shutdown_unloads_what_is_left(tmp_path: Path) -> None:
    kwin = FakeKWin()
    window = KWinFloatingWindow(kwin, tmp_path, "dev.skyline.Gravitas", pid=4242)
    window.set_floating(True)
    window.shutdown()
    assert kwin.calls[-1][0] == "unloadScript"
    kwin.calls.clear()
    window.shutdown()
    assert kwin.calls == [], "nothing loaded, nothing to unload"


def test_a_refused_load_is_not_started_or_remembered(tmp_path: Path) -> None:
    kwin = FakeKWin(load_id=-1)
    window = KWinFloatingWindow(kwin, tmp_path, "dev.skyline.Gravitas", pid=4242)
    window.set_floating(True)
    assert _names(kwin) == ["unloadScript", "loadScript"]
    window.shutdown()
    assert _names(kwin) == ["unloadScript", "loadScript"]


def test_a_failing_bus_never_raises(tmp_path: Path) -> None:
    def broken(method: str, *args: object) -> object:
        raise RuntimeError("bus gone")

    KWinFloatingWindow(broken, tmp_path, "dev.skyline.Gravitas", pid=1).set_floating(True)


def test_scripts_match_the_app_id_not_the_pid() -> None:
    # Inside a Flatpak the app's pid is not the one KWin sees (its own pid
    # namespace): matched by pid, the script found nothing to pin.
    for source in (float_script("dev.skyline.Gravitas"), restore_script("dev.skyline.Gravitas")):
        assert '"dev.skyline.gravitas"' in source
        assert "resourceClass).toLowerCase()" in source
        assert "window.pid" not in source
        assert "!window.normalWindow" in source


def test_an_app_id_cannot_break_out_of_the_script() -> None:
    source = float_script('evil"; workspace.windowList().forEach(w => w.minimized = true); "')
    assert 'evil\\"; workspace' in source, "quoted as a JS string literal, not spliced in"


def test_a_name_left_loaded_by_a_dead_run_is_cleared_before_loading(tmp_path: Path) -> None:
    """In the Flatpak every run is pid 2, so a run killed without aboutToQuit
    leaves "gravitas-pip-restore-2" loaded, and KWin refuses to load that
    name again (-1): leaving PiP then restored nothing, and the window stayed
    frameless and on top."""

    class KWinWithLeftover(FakeKWin):
        def __init__(self) -> None:
            super().__init__()
            self.loaded = {"gravitas-pip-restore-2"}

        def __call__(self, method: str, *args: object) -> object:
            self.calls.append((method, *args))
            if method == "unloadScript":
                self.loaded.discard(str(args[0]))
                return True
            if method == "loadScript":
                name = str(args[1])
                if name in self.loaded:
                    return -1
                self.loaded.add(name)
                return 7
            return True

    kwin = KWinWithLeftover()
    window = KWinFloatingWindow(kwin, tmp_path, "dev.skyline.Gravitas", pid=2)
    window.set_floating(True)
    window.set_floating(False)
    assert "gravitas-pip-restore-2" in kwin.loaded
    assert ("start",) in kwin.calls[-1:]


def test_leftovers_from_earlier_runs_are_swept_at_startup(tmp_path: Path) -> None:
    """A float script left behind pins every Gravitas window opened after it;
    its name is the file this adapter wrote, so startup unloads it."""
    (tmp_path / "gravitas-pip-float-2.js").write_text("//")
    (tmp_path / "gravitas-pip-restore-133928.js").write_text("//")
    (tmp_path / "unrelated.js").write_text("//")
    kwin = FakeKWin()
    KWinFloatingWindow(kwin, tmp_path, "dev.skyline.Gravitas", pid=2).sweep_leftovers()
    assert kwin.calls == [
        ("unloadScript", "gravitas-pip-float-2"),
        ("unloadScript", "gravitas-pip-restore-133928"),
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["unrelated.js"]
