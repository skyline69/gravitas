from typing import Any

from gravitas.infrastructure.desktop.idle_inhibitor import IdleInhibitor, _LinuxBackend


class RecordingBackend:
    def __init__(self, succeeds: bool = True) -> None:
        self.calls: list[str] = []
        self.succeeds = succeeds

    def inhibit(self) -> bool:
        self.calls.append("inhibit")
        return self.succeeds

    def release(self) -> None:
        self.calls.append("release")


def test_each_change_of_wish_reaches_the_backend_once() -> None:
    backend = RecordingBackend()
    inhibitor = IdleInhibitor(lambda: backend)
    inhibitor.set_inhibited(True)
    assert inhibitor.wait_idle()
    inhibitor.set_inhibited(True)
    assert inhibitor.wait_idle()
    inhibitor.set_inhibited(False)
    assert inhibitor.wait_idle()
    assert backend.calls == ["inhibit", "release"]


def test_no_backend_is_a_quiet_no_op() -> None:
    inhibitor = IdleInhibitor(lambda: None)
    inhibitor.set_inhibited(True)
    assert inhibitor.wait_idle()


def test_a_backend_that_raises_does_not_stop_the_next_wish() -> None:
    class Flaky(RecordingBackend):
        def inhibit(self) -> bool:
            super().inhibit()
            raise RuntimeError("bus went away")

    backend = Flaky()
    inhibitor = IdleInhibitor(lambda: backend)
    inhibitor.set_inhibited(True)
    assert inhibitor.wait_idle()
    inhibitor.set_inhibited(False)
    assert inhibitor.wait_idle()
    assert backend.calls == ["inhibit", "release"]


class ScriptedLinux(_LinuxBackend):
    """The D-Bus calls, answered from a script instead of a session bus."""

    def __init__(self, screensaver: bool) -> None:
        super().__init__()
        self.screensaver = screensaver
        self.sent: list[tuple[str, str]] = []

    def _call(
        self,
        bus_name: str,
        path: str,
        interface: str,
        method: str,
        signature: str | None = None,
        body: tuple[Any, ...] = (),
    ) -> tuple[Any, ...]:
        self.sent.append((interface, method))
        if interface == "org.freedesktop.ScreenSaver":
            if not self.screensaver:
                raise RuntimeError("org.freedesktop.DBus.Error.ServiceUnknown")
            return (7522,) if method == "Inhibit" else ()
        if method == "Inhibit":
            # The portal's `u` flag must go out as 8 (idle), under "sua{sv}".
            assert signature == "sua{sv}" and body[1] == 8
            return ("/org/freedesktop/portal/desktop/request/1_2/t",)
        return ()


def test_linux_prefers_the_screensaver_and_releases_by_cookie() -> None:
    backend = ScriptedLinux(screensaver=True)
    assert backend.inhibit() is True
    backend.release()
    assert backend.sent == [
        ("org.freedesktop.ScreenSaver", "Inhibit"),
        ("org.freedesktop.ScreenSaver", "UnInhibit"),
    ]


def test_linux_falls_back_to_the_portal_inside_the_sandbox() -> None:
    """The Flatpak cannot see org.freedesktop.ScreenSaver; the portal is how
    it asks, and closing the Request is how it stops asking."""
    backend = ScriptedLinux(screensaver=False)
    assert backend.inhibit() is True
    backend.release()
    assert backend.sent == [
        ("org.freedesktop.ScreenSaver", "Inhibit"),
        ("org.freedesktop.portal.Inhibit", "Inhibit"),
        ("org.freedesktop.portal.Request", "Close"),
    ]
