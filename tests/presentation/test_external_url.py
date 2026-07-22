import os
import subprocess
import sys
from typing import Any

from pytest import MonkeyPatch

from gravitas.presentation import external_url
from gravitas.presentation.external_url import host_environment, open_in_browser


def test_unfrozen_run_passes_this_process_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("LD_LIBRARY_PATH", "/dev/env")
    seen: list[dict[str, str]] = []

    assert open_in_browser("https://x", launch=lambda url, env: bool(seen.append(env)) or True)
    assert seen[0]["LD_LIBRARY_PATH"] == "/dev/env"


def test_frozen_run_hands_the_child_the_host_library_path(monkeypatch: MonkeyPatch) -> None:
    """PyInstaller's bootloader points LD_LIBRARY_PATH at the bundle and saves
    the host's value as LD_LIBRARY_PATH_ORIG. A browser launched against the
    bundle's libraries dies before it shows a window."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/libs")

    env = host_environment()

    assert env["LD_LIBRARY_PATH"] == "/host/libs"
    assert "LD_LIBRARY_PATH_ORIG" not in env
    # This process keeps the bundle path — mpv and yt-dlp still need it.
    assert os.environ["LD_LIBRARY_PATH"] == "/bundle/_internal"


def test_frozen_run_without_a_saved_original_drops_the_variable(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("QT_PLUGIN_PATH", "/bundle/_internal/plugins")
    monkeypatch.delenv("QT_PLUGIN_PATH_ORIG", raising=False)

    assert "QT_PLUGIN_PATH" not in host_environment()


def test_frozen_run_launders_every_bundle_variable(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    for var in external_url._BUNDLE_VARS:
        monkeypatch.setenv(var, "/bundle")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = host_environment()

    assert not [var for var in external_url._BUNDLE_VARS if var in env]
    assert env["PATH"] == "/usr/bin"  # everything else is passed through


def test_the_spawn_gets_the_environment_not_this_process(monkeypatch: MonkeyPatch) -> None:
    """The whole point: mutating os.environ around the launch does not reach
    Qt's child, so the environment has to travel as an argument to Popen."""
    monkeypatch.setattr(sys, "platform", "linux")  # the opener path is Unix-only
    monkeypatch.setattr(external_url.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    calls: list[tuple[list[str], dict[str, str]]] = []

    class FakePopen:
        def __init__(self, argv: list[str], **kwargs: Any) -> None:
            calls.append((argv, kwargs["env"]))

    monkeypatch.setattr(external_url.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host/libs")

    assert open_in_browser("https://x") is True

    argv, env = calls[0]
    assert argv == ["/usr/bin/xdg-open", "https://x"]
    assert env["LD_LIBRARY_PATH"] == "/host/libs"


def test_the_child_is_detached_and_silenced(monkeypatch: MonkeyPatch) -> None:
    """A browser must outlive Gravitas, and an opener writing to a terminal
    that may not exist must not block on a full pipe."""
    monkeypatch.setattr(sys, "platform", "linux")  # the opener path is Unix-only
    monkeypatch.setattr(external_url.shutil, "which", lambda name, path=None: f"/usr/bin/{name}")
    kwargs: dict[str, Any] = {}

    class FakePopen:
        def __init__(self, argv: list[str], **kw: Any) -> None:
            kwargs.update(kw)

    monkeypatch.setattr(external_url.subprocess, "Popen", FakePopen)

    open_in_browser("https://x")

    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL


def test_falls_through_to_the_next_opener_when_one_is_missing(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")  # the opener path is Unix-only
    monkeypatch.setattr(
        external_url.shutil,
        "which",
        lambda name, path=None: "/usr/bin/gio" if name == "gio" else None,
    )
    argvs: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv: list[str], **kw: Any) -> None:
            argvs.append(argv)

    monkeypatch.setattr(external_url.subprocess, "Popen", FakePopen)

    assert open_in_browser("https://x") is True
    assert argvs == [["/usr/bin/gio", "open", "https://x"]]


def test_no_opener_on_path_falls_back_to_qt(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")  # the opener path is Unix-only
    monkeypatch.setattr(external_url.shutil, "which", lambda name, path=None: None)
    asked: list[str] = []
    monkeypatch.setattr(
        external_url.QDesktopServices,
        "openUrl",
        staticmethod(lambda qurl: bool(asked.append(qurl.toString())) or True),
    )

    assert open_in_browser("https://x") is True
    assert asked == ["https://x"]


def test_empty_url_is_a_noop() -> None:
    calls: list[str] = []
    assert open_in_browser("", launch=lambda u, env: bool(calls.append(u)) or True) is False
    assert calls == []


def test_windows_and_macos_go_straight_to_qt(monkeypatch: MonkeyPatch) -> None:
    # A Windows box with Git Bash or WSL interop on PATH HAS an xdg-open, and
    # it cannot open a Windows browser. Probing the opener list there would
    # find it, spawn it, and report success while nothing happened.
    def _boom(name: str, path: str | None = None) -> str:
        raise AssertionError(f"opener search must not run: {name}")

    monkeypatch.setattr(external_url.shutil, "which", _boom)
    asked: list[str] = []
    monkeypatch.setattr(
        external_url.QDesktopServices,
        "openUrl",
        staticmethod(lambda qurl: bool(asked.append(qurl.toString())) or True),
    )

    for platform in ("win32", "darwin"):
        monkeypatch.setattr(sys, "platform", platform)
        assert open_in_browser("https://x") is True
    assert asked == ["https://x", "https://x"]
