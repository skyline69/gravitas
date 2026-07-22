from gravitas.presentation.controllers.onboarding_controller import OnboardingController


class _FlagHolder:
    done: bool = False


def _build(done: bool) -> tuple[OnboardingController, _FlagHolder, list[None]]:
    holder = _FlagHolder()
    holder.done = done
    persisted: list[None] = []
    controller = OnboardingController(holder, lambda: persisted.append(None))
    return controller, holder, persisted


def test_active_on_fresh_install(qapp: object) -> None:
    controller, _holder, _persisted = _build(done=False)
    assert controller.active is True


def test_inactive_for_returning_user(qapp: object) -> None:
    controller, _holder, _persisted = _build(done=True)
    assert controller.active is False


def test_complete_flags_notifies_and_persists(qapp: object) -> None:
    controller, holder, persisted = _build(done=False)
    fired: list[None] = []
    controller.activeChanged.connect(lambda: fired.append(None))
    controller.complete()
    assert holder.done is True
    assert controller.active is False
    assert fired == [None]
    assert persisted == [None]


def test_complete_is_idempotent(qapp: object) -> None:
    controller, _holder, persisted = _build(done=False)
    controller.complete()
    controller.complete()
    # A double-fired Finish button must not rewrite the settings file twice.
    assert persisted == [None]
