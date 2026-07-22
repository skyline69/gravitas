import contextlib
import os
import sys

from pytest import MonkeyPatch

from gravitas.presentation.external_url import open_in_browser


def test_unfrozen_run_launches_with_env_untouched(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("LD_LIBRARY_PATH", "/dev/env")
    seen: list[str | None] = []

    def launch(url: str) -> bool:
        seen.append(os.environ.get("LD_LIBRARY_PATH"))
        return True

    assert open_in_browser("https://x", launch=launch) is True
    assert seen == ["/dev/env"]
    assert os.environ["LD_LIBRARY_PATH"] == "/dev/env"


def test_frozen_run_restores_the_host_library_path_for_the_child(
    monkeypatch: MonkeyPatch,
) -> None:
    """PyInstaller's bootloader points LD_LIBRARY_PATH at the bundle and saves
    the host's value as LD_LIBRARY_PATH_ORIG. xdg-open (and the browser it
    execs) inherit the spawn-time environment, so the bundle path must be
    swapped out around the launch — linking a browser against bundled libs is
    why Authenticate silently did nothing in the AppImage."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/libs")
    seen: list[str | None] = []

    def launch(url: str) -> bool:
        seen.append(os.environ.get("LD_LIBRARY_PATH"))
        return True

    assert open_in_browser("https://x", launch=launch) is True
    assert seen == ["/host/libs"]
    # The app's own environment is put back — mpv/yt-dlp still need the bundle.
    assert os.environ["LD_LIBRARY_PATH"] == "/bundle/_internal"


def test_frozen_run_without_saved_original_unsets_the_variable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    seen: list[str | None] = []

    def launch(url: str) -> bool:
        seen.append(os.environ.get("LD_LIBRARY_PATH"))
        return True

    open_in_browser("https://x", launch=launch)
    assert seen == [None]
    assert os.environ["LD_LIBRARY_PATH"] == "/bundle/_internal"


def test_env_restored_even_when_the_launch_raises(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")

    def launch(url: str) -> bool:
        raise RuntimeError("boom")

    with contextlib.suppress(RuntimeError):
        open_in_browser("https://x", launch=launch)
    assert os.environ["LD_LIBRARY_PATH"] == "/bundle/_internal"


def test_empty_url_is_a_noop(monkeypatch: MonkeyPatch) -> None:
    calls: list[str] = []
    assert open_in_browser("", launch=lambda u: calls.append(u) or True) is False
    assert calls == []
