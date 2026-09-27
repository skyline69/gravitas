"""Reading the best screen's height out of Qt."""

from gravitas.infrastructure import display


class FakeSize:
    def __init__(self, height: int) -> None:
        self._height = height

    def height(self) -> int:
        return self._height


class FakeScreen:
    def __init__(self, height: int, dpr: float = 1.0) -> None:
        self._height = height
        self._dpr = dpr

    def size(self) -> FakeSize:
        return FakeSize(self._height)

    def devicePixelRatio(self) -> float:
        return self._dpr


def _screens(monkeypatch, screens: list[object], app: object | None = object()) -> None:
    """Swap the whole QGuiApplication reference, not its methods: `instance`
    is inherited from QCoreApplication, so patching it there also blinds
    pytest-qt's own teardown."""

    class FakeApp:
        @staticmethod
        def instance() -> object | None:
            return app

        @staticmethod
        def screens() -> list[object]:
            return screens

    monkeypatch.setattr(display, "QGuiApplication", FakeApp)


def test_the_tallest_screen_wins(monkeypatch) -> None:
    _screens(monkeypatch, [FakeScreen(1080), FakeScreen(1440), FakeScreen(720)])
    assert display.best_screen_height() == 1440


def test_logical_size_is_multiplied_back_to_physical_pixels(monkeypatch) -> None:
    # A 2880x1800 Retina panel reports 1440x900 logical at 2x. Taking the
    # logical number would cap every source at 900p.
    _screens(monkeypatch, [FakeScreen(900, dpr=2.0)])
    assert display.best_screen_height() == 1800


def test_no_screens_means_no_cap(monkeypatch) -> None:
    _screens(monkeypatch, [])
    assert display.best_screen_height() == 0


def test_no_application_means_no_cap(monkeypatch) -> None:
    _screens(monkeypatch, [FakeScreen(2160)], app=None)
    assert display.best_screen_height() == 0


def test_a_screen_that_throws_is_skipped(monkeypatch) -> None:
    class Exploding:
        def size(self):
            raise RuntimeError("display disconnected")

        def devicePixelRatio(self) -> float:
            return 1.0

    _screens(monkeypatch, [Exploding(), FakeScreen(1080)])
    assert display.best_screen_height() == 1080


def test_the_real_screen_answers_a_sane_number(qapp: object) -> None:
    # Offscreen platform in tests: whatever it reports must not be negative.
    assert display.best_screen_height() >= 0
