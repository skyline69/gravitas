"""The colored console formatter and its TTY/env gating."""

import io
import logging

from gravitas.logging_setup import (
    ColorFormatter,
    _wants_color,
    abbreviate_url,
    configure_logging,
)


def _record(level: int, msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="gravitas.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_plain_format_has_no_escapes() -> None:
    line = ColorFormatter(color=False).format(_record(logging.INFO, "hello"))
    assert "\x1b[" not in line
    assert "INFO" in line and "gravitas.test" in line and line.endswith("hello")


def test_colored_format_wraps_level_and_resets() -> None:
    line = ColorFormatter(color=True).format(_record(logging.WARNING, "careful"))
    assert "\x1b[33m" in line  # yellow around WARNING
    assert line.count("\x1b[0m") >= 2  # every escape closed
    assert "careful" in line


def test_exception_appended() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record(logging.ERROR, "failed")
        import sys

        record.exc_info = sys.exc_info()
    line = ColorFormatter(color=False).format(record)
    assert "failed" in line and "ValueError: boom" in line


def test_abbreviate_url_identity_when_short() -> None:
    url = "https://v3-cinemeta.strem.io/catalog/movie/top.json"
    assert abbreviate_url(url) == url


def test_abbreviate_url_keeps_host_and_tail() -> None:
    url = "https://aiostreams.example.com/playback/" + "x" * 1200 + "/final-segment"
    short = abbreviate_url(url)
    assert len(short) < 160
    assert short.startswith("https://aiostreams.example.com/playback/")
    assert "…" in short
    assert short.endswith(f"/final-segment ({len(url)} chars)")


def test_no_color_env_disables(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("NO_COLOR", "1")
    assert not _wants_color(io.StringIO())


def test_configure_respects_level_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GRAVITAS_LOG_LEVEL", "debug")
    root = logging.getLogger()
    before_level, before_handlers = root.level, list(root.handlers)
    try:
        configure_logging(stream=io.StringIO())
        assert root.level == logging.DEBUG
        # DEBUG runs keep httpx wire chatter enabled.
        assert logging.getLogger("httpx").level == logging.NOTSET
    finally:
        root.setLevel(before_level)
        for h in root.handlers:
            if h not in before_handlers:
                root.removeHandler(h)


def test_configure_quiets_httpx_at_info(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("GRAVITAS_LOG_LEVEL", raising=False)
    root = logging.getLogger()
    before_level, before_handlers = root.level, list(root.handlers)
    try:
        configure_logging(stream=io.StringIO())
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
    finally:
        root.setLevel(before_level)
        for h in root.handlers:
            if h not in before_handlers:
                root.removeHandler(h)
        for noisy in ("httpx", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.NOTSET)
