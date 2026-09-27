from gravitas.presentation.controllers.window_controller import WindowController


class Recorder:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def set_floating(self, floating: bool) -> None:
        self.calls.append(floating)


def test_forwards_picture_in_picture_to_the_desktop(qapp: object) -> None:
    desktop = Recorder()
    controller = WindowController(desktop)
    controller.setFloating(True)
    controller.setFloating(False)
    assert desktop.calls == [True, False]


def test_without_a_desktop_to_ask_it_does_nothing(qapp: object) -> None:
    WindowController(None).setFloating(True)
