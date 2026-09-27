"""NativePlayer on the real engine module, playing a generated file.

Skipped unless scripts/build_native_player.py has been run and the ffmpeg CLI
is there to make a sample. Everything is local: the engine's silent output
drives the clock, and nothing touches the network.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gravitas.domain.models import TrackLanguages
from gravitas.infrastructure.player.native_player import EventCallback, NativePlayer

module: Any = pytest.importorskip("gravitas.infrastructure.player.gravitas_player")


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("the ffmpeg CLI is needed to make a sample")
    folder = tmp_path_factory.mktemp("native")
    path = folder / "sample.mkv"
    srt = folder / "sample.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nEmbedded line\n")
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=6",
            "-i", str(srt),
            "-map", "0", "-map", "1", "-map", "2", "-map", "3",
            "-c:v", "mpeg4", "-c:a", "mp2", "-c:s", "srt",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
            "-metadata:s:a:1", "title=Dub", "-metadata:s:s:0", "language=eng",
            str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def _silent(on_event: EventCallback) -> Any:
    return module.Player(on_event, audio="null")


def _wait(condition: Callable[[], bool], what: str, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def test_a_file_plays_through_the_port(sample: Path) -> None:
    player = NativePlayer(_silent)
    opened = threading.Event()
    ended = threading.Event()
    player.set_opened_callback(opened.set)
    player.set_stream_ended_callback(ended.set)
    try:
        player.set_track_languages(TrackLanguages(audio="de", subtitle=""))
        player.play(str(sample), start=1.0)
        assert opened.wait(5)
        assert player.audio_tracks() == [(1, "English · Mono"), (2, "German · Dub · Mono")]
        assert player.current_audio_track() == 2
        assert player.duration() == pytest.approx(6.0, abs=0.2)
        assert player.video_size() == (320, 180)

        # Frames are pulled the way the video item pulls them.
        engine = player.render_handle()
        pixels = bytearray(320 * 180 * 4)
        _wait(lambda: engine.render(memoryview(pixels), 320, 180, 320 * 4), "a frame")  # type: ignore[attr-defined]
        assert any(pixels)

        _wait(lambda: player.position() > 1.5, "playback to advance")
        player.seek(5.0)
        _wait(lambda: not player.is_loading(), "the seek")
        assert 5.0 <= player.position() < 5.8
        assert ended.wait(5)
    finally:
        player.shutdown()


def test_a_missing_file_reports_a_failed_load(sample: Path) -> None:
    player = NativePlayer(_silent)
    failed = threading.Event()
    player.set_load_failed_callback(failed.set)
    try:
        player.play(str(sample.with_name("missing.mkv")))
        assert failed.wait(5)
    finally:
        player.shutdown()


def test_gravitas_hwdec_no_decodes_in_software(sample: Path) -> None:
    player = NativePlayer(_silent, environ={"GRAVITAS_HWDEC": "no"})
    try:
        player.play(str(sample))
        engine = player.render_handle()
        pixels = bytearray(320 * 180 * 4)
        _wait(lambda: engine.render(memoryview(pixels), 320, 180, 320 * 4), "a frame")  # type: ignore[attr-defined]
        assert engine.hardware_decoder() is None  # type: ignore[attr-defined]
    finally:
        player.shutdown()


def test_the_module_reports_its_ffmpeg() -> None:
    engine = importlib.import_module("gravitas.infrastructure.player.gravitas_player")
    assert "libavformat" in engine.ffmpeg_versions()


def test_subtitles_embedded_and_from_a_file(sample: Path) -> None:
    player = NativePlayer(_silent)
    opened = threading.Event()
    player.set_opened_callback(opened.set)
    addon = sample.with_name("addon.srt")
    addon.write_text("1\n00:00:02,000 --> 00:00:04,000\nFrom an addon\n")
    try:
        player.set_track_languages(TrackLanguages(audio="en", subtitle="en"))
        player.play(str(sample))
        assert opened.wait(5)
        assert player.subtitle_tracks() == [(1, "English")]
        assert player.current_subtitle_track() == 1
        assert player.has_preferred_subtitle()

        player.add_subtitle(str(addon), "English · Addon", "eng", select=True)
        assert player.subtitle_tracks() == [(1, "English"), (2, "English · Addon (Online)")]
        assert player.current_subtitle_track() == 2

        player.set_subtitle_track(None)  # "Off" hides
        assert player.current_subtitle_track() is None
        player.set_subtitle_track(1)
        assert player.current_subtitle_track() == 1

        player.set_subtitle_delay(0.4)
        assert player.subtitle_delay() == pytest.approx(0.4)
    finally:
        player.shutdown()
